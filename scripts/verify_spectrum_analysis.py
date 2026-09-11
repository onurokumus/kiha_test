"""Phase 6d live Spectrum verification against independent full-rate references.

Global Python uses stdlib/Playwright only. Native Parquet, NumPy and SciPy work
runs in backend/.venv (Python 3.13). Fixtures, profiles and servers are isolated;
the existing cold statistics cache is warmed sequentially before browser work.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

from verify_data_quality import ROOT, servers, upload
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout
from verify_filter_overlay import dataset_hashes
from verify_time_y_zoom import instrument

TEST, GAP = 'spectrum_analysis', 'spectrum_gap'
SIGNAL, REPAIRED, REFERENCE, RPM = 'signal_N', 'repaired_N', 'reference_V', 'rpm'
COLS = [SIGNAL, REPAIRED, REFERENCE, RPM]
FS, N = 2048, 32768
POINTS = [
    {'id': 7, 'name': 'Long tone', 'label': 'first', 'start_s': .475,
     'end_s': 8.475, 'start_idx': 1024, 'end_idx': 17408},
    {'id': 9, 'name': 'Short tone', 'label': 'second', 'start_s': 8.975,
     'end_s': 12.975, 'start_idx': 18432, 'end_idx': 26624},
]

ORACLE_CODE = r'''
import json, sys
import numpy as np
import polars as pl
from scipy import signal
from app import dsp, store
from app.locks import data_read
query=json.load(sys.stdin)
name=query.pop('test'); column=query.pop('col'); mode=query.pop('mode','fft')
for key in ('tp_id','nperseg'):
    if key in query: query[key]=int(query[key])
for key in ('t0','t1'):
    if key in query: query[key]=float(query[key])
with data_read(name):
    meta=store.get_meta(name)
    i0,i1=store.testpoint_range(name,query['tp_id']) if 'tp_id' in query else store.window_bounds(meta,query.get('t0'),query.get('t1'))
    frame=pl.read_parquet(store.TESTS_DIR/name/'data.parquet').slice(i0,i1-i0)
    values=frame[column].to_numpy().astype(float); times=frame[meta['time_column']].to_numpy()
    finite=np.isfinite(values); indices=np.arange(len(values))
    clean=np.interp(indices,indices[finite],values[finite]); clean=clean-clean.mean()
    fs=meta['fs_hz']; n=len(clean)
    if mode=='welch':
        length=min(max(query.get('nperseg',4096),64),n)
        frequencies,magnitudes=signal.welch(clean,fs=fs,window='hann',nperseg=length,
            noverlap=length//2,nfft=length,detrend='constant',return_onesided=True,
            scaling='density',average='mean')
    else:
        frequencies=np.fft.rfftfreq(n,1/fs)
        magnitudes=np.abs(np.fft.rfft(clean))*2/n
        magnitudes[0]/=2
        if n%2==0:magnitudes[-1]/=2
    result=dsp.spectrum_samples(name,column,mode,query.get('t0'),query.get('t1'),
        nperseg=query.get('nperseg',4096),rpm_col=query.get('rpm_col'),tp_id=query.get('tp_id'))
    np.testing.assert_allclose(result.freqs,frequencies,rtol=1e-14,atol=1e-14)
    np.testing.assert_allclose(result.mag,magnitudes,rtol=1e-10,atol=2e-14)
    output={'freqs':frequencies.tolist(),'mag':magnitudes.tolist(),'metadata':result.metadata,
        'i0':i0,'i1':i1,'n_samples':n,'finite_count':int(finite.sum()),'nan_count':int((~finite).sum()),
        'time_start_s':float(times[0]),'time_end_s':float(times[-1]),'fs_hz':fs}
    if query.get('rpm_col'):
        rpm=frame[query['rpm_col']].to_numpy();rpm=np.abs(rpm[np.isfinite(rpm)])
        output.update(mean_rpm=float(rpm.mean()),min_rpm=float(rpm.min()),max_rpm=float(rpm.max()))
    json.dump(output,sys.stdout,allow_nan=False)
'''


def fixtures(request):
    lines=['time,'+','.join(COLS)]
    missing={1024:'',2000:'nan',3000:'inf',17407:'-inf',18432:'',20000:'nan'}
    for index in range(N):
        t=index/FS
        value=7+3*math.sin(2*math.pi*128.125*t)+.8*math.cos(2*math.pi*320*t)
        repaired=missing.get(index,format(value,'.16g'))
        lines.append(f'{1000.125+t:.12f},{value:.16g},{repaired},{2+math.cos(t):.16g},{1200 if index<16384 else 2400}')
    assert upload(request,TEST,'\n'.join(lines))['status']=='ready'
    response=request.put(f'tests/{TEST}/testpoints',data={'version':1,'test':TEST,'test_points':POINTS})
    assert response.ok,response.text()
    for column in COLS:
        response=request.get(f'tests/{TEST}/tp_stats',params={'col':column})
        assert response.ok,response.text()
    content='time,signal_N,rpm\n'+'\n'.join(f'{i/FS:.12f},{math.sin(i):.16g},1800' for i in range(1024) if not 400<=i<410)
    assert upload(request,GAP,content)['status']=='ready'
    response=request.get(f'tests/{GAP}/spectrum',params={'col':SIGNAL})
    assert response.status==400 and 'gap' in response.text(),response.text()


def seed(page, sources=None):
    settings={'scatterX':SIGNAL,'scatterY':REFERENCE,'clustering':False,
              'gridColumns':COLS,'defaultViewMode':'spectrum'}
    session={'version':1,**({'sources':sources} if sources is not None else {}),'currentTest':TEST,'xAxis':SIGNAL,'yAxis':REFERENCE,'axesUserSet':True,
        'selections':[{'test':TEST,'tpId':p['id'],'hidden':False} for p in POINTS],
        'plotConfigs':COLS,'plotsUserEdited':True,'plotDensity':'quad','viewMode':'spectrum',
        'specMode':'fft','specSource':'tp','specXAxis':'hz','specRpmCol':RPM,'specLogY':False,
        'plotFilters':[{'kind':'moving_avg','winS':'.051'},{},{},{}],
        'scatterCollapsed':True}
    page.add_init_script(f'''if(!sessionStorage.getItem('spectrum-seeded')){{
        localStorage.clear();sessionStorage.setItem('spectrum-seeded','true');
        localStorage.setItem('ptt.settings.v1',{json.dumps(json.dumps(settings))});
        localStorage.setItem('ptt.analysis-session.v1',{json.dumps(json.dumps(session))});
    }}''')


def plot(page,column=SIGNAL):
    return page.get_by_role('group',name=f'{column} spectrum plot',exact=True)


def snapshot(page,column=SIGNAL):
    target=plot(page,column).locator('.uplot')
    target.wait_for()
    wait_for_chart_layout(page)
    return target.evaluate('''el=>{
        const u=el.__verificationPlot;
        return {data:u.data,labels:u.series.slice(1).map(s=>s.label),axes:u.axes.map(a=>a.label),
            x:[u.scales.x.min,u.scales.x.max],y:[u.scales.y.min,u.scales.y.max]};
    }''')


def settled(page):
    page.wait_for_load_state('networkidle')
    for column in COLS:
        plot(page,column).locator('.uplot').wait_for()
        expect(plot(page,column)).not_to_contain_text('Calculating spectrum')
        expect(plot(page,column)).not_to_contain_text('Recalculating spectrum')
    wait_for_chart_layout(page)


def details(page,column=SIGNAL):
    button=plot(page,column).get_by_role('button',name=f'Spectrum details for {column}',exact=True)
    button.focus();page.keyboard.press('Enter')
    panel=page.get_by_role('dialog',name=f'Spectrum details for {column}',exact=True)
    expect(panel).to_be_visible()
    return panel


def close_details(page,panel,column=SIGNAL):
    page.keyboard.press('Escape');expect(panel).not_to_be_visible()
    expect(plot(page,column).get_by_role('button',name=f'Spectrum details for {column}',exact=True)).to_be_focused()


def assert_geometry(panel):
    assert panel.evaluate('''el=>{
        const r=el.getBoundingClientRect();return r.left>=-1&&r.top>=-1&&r.right<=innerWidth+1
            &&r.bottom<=innerHeight+1&&el.scrollWidth<=el.clientWidth+1;
    }'''),'Details dialog overflows viewport or horizontally clips content'


def run_checks(web,api,dataset,temporary,output):
    oracle_cache={}
    def oracle(query):
        key=json.dumps(query,sort_keys=True)
        if key not in oracle_cache:
            result=subprocess.run([str(ROOT/'backend/.venv/Scripts/python.exe'),'-c',ORACLE_CODE],
                cwd=ROOT/'backend',env={**os.environ,'KIHA_DATA_DIR':str(dataset)},
                input=json.dumps({'test':TEST,**query}),capture_output=True,text=True,timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            assert result.returncode==0,result.stderr
            oracle_cache[key]=json.loads(result.stdout)
        return oracle_cache[key]

    def verify_response(query,body):
        reference=oracle(query)
        for field in ('i0','i1','n_samples','finite_count','nan_count','time_start_s','time_end_s','fs_hz'):
            assert math.isclose(body[field],reference[field],rel_tol=1e-13,abs_tol=1e-12),(field,body[field],reference[field])
        for field in ('mean_rpm','min_rpm','max_rpm'):
            if field in reference:assert math.isclose(body[field],reference[field],rel_tol=1e-13),(field,body[field],reference[field])
        if 'tp_id' in query:
            assert body['tp_id']==int(query['tp_id'])
            assert 't0' not in query and 't1' not in query,query
        count=len(reference['freqs']);factor=max(1,math.ceil(count/4000))
        expected_indices=[]
        for start in range(0,count,factor):
            expected_indices.append(max(range(start,min(start+factor,count)),key=lambda i:reference['mag'][i]))
        assert body['bin_indices']==expected_indices,'Reduced frequencies must retain their maximizing original bin'
        assert body['reduction']=={'method':'none' if factor==1 else 'max-bin','factor':factor,
            'n_bins_original':count,'n_bins_returned':len(expected_indices)},body['reduction']
        for index,freq,mag in zip(body['bin_indices'],body['freqs'],body['mag']):
            assert math.isclose(freq,reference['freqs'][index],rel_tol=1e-14,abs_tol=1e-14)
            assert math.isclose(mag,reference['mag'][index],rel_tol=1e-9,abs_tol=2e-14)
        peak=max(range(count),key=lambda i:reference['mag'][i])
        assert body['peak']['bin_index']==peak
        assert math.isclose(body['peak']['frequency_hz'],reference['freqs'][peak],abs_tol=1e-14)
        assert body['method']['version']=='kiha-spectrum-v2',body['method']
        assert body['method']['units']==('U²/Hz' if query.get('mode')=='welch' else 'U')
        method=body['method'];welch=query.get('mode')=='welch'
        length=min(max(int(query.get('nperseg',4096)),64),reference['n_samples']) if welch else reference['n_samples']
        assert method['nfft']==length and math.isclose(method['bin_spacing_hz'],reference['fs_hz']/length,rel_tol=1e-14)
        assert method['window']==('hann_periodic' if welch else 'rectangular')
        assert method['source']=='stored' and method['prefilter']=='none' and method['detrend']=='constant'
        assert method['missing_values']=='linear_by_index_hold_edges'
        if welch:
            overlap=length//2;segments=1+(reference['n_samples']-length)//(length-overlap)
            used=length+(segments-1)*(length-overlap)
            assert (method['nperseg'],method['noverlap'],method['segment_count'],method['used_samples'],method['trailing_samples'])==(length,overlap,segments,used,reference['n_samples']-used)
        assert body['quality']['known_gap_count']==0 and body['quality']['gap_metadata_available']
        assert len(body['freqs'])==len(body['mag'])==len(expected_indices)
        return {'query':query,'samples':body['n_samples'],'finite':body['finite_count'],
                'missing':body['nan_count'],'peak':body['peak'],'method':body['method'],'reduction':body['reduction']}

    extension=temporary/'extension';extension.mkdir()
    (extension/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'Spectrum verification zoom',
        'version':'1.0','permissions':['tabs'],'background':{'service_worker':'background.js'}}))
    (extension/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as playwright:
        request=playwright.request.new_context(base_url=api+'/')
        fixtures(request)
        source_hashes=dataset_hashes(dataset)
        evidence=[]
        # HTTP defaults and an explicit long Welch segment exercise both
        # reduction branches without exposing new UI estimator controls.
        for query in [
            {'col':SIGNAL,'mode':'fft','tp_id':7},
            {'col':REPAIRED,'mode':'fft','tp_id':7},
            {'col':REPAIRED,'mode':'fft','tp_id':9},
            {'col':SIGNAL,'mode':'welch','tp_id':7,'nperseg':8192},
            {'col':SIGNAL,'mode':'fft'},
            {'col':SIGNAL,'mode':'welch','t0':.503,'t1':3.497,'rpm_col':RPM},
        ]:
            response=request.get(f'tests/{TEST}/spectrum',params=query)
            assert response.ok,response.text()
            evidence.append(verify_response(query,response.json()))
        assert evidence[0]['samples']==16384 and math.isclose(evidence[0]['peak']['frequency_hz'],128.125,abs_tol=1e-12)
        assert evidence[1]['missing']==4 and evidence[2]['missing']==2
        print('PASS: exact saved TP rows, actual sample times, full-rate shared result/reference, FFT/Welch retained peak bins and per-TP nonfinite counts',flush=True)

        context=playwright.chromium.launch_persistent_context(str(temporary/'profile'),channel='chromium',
            headless=True,no_viewport=True,args=[f'--disable-extensions-except={extension}',
            f'--load-extension={extension}','--window-size=1440,1000'])
        page=context.pages[0]
        requests,responses,errors,writes,held=[],[],[],[],[]
        state={'failure':None,'hold':False,'legacy':False,'missing_identity':False}
        page.on('pageerror',lambda error:errors.append(error.stack or str(error)))
        page.on('console',lambda message:errors.append(message.text) if message.type=='error' else None)
        page.on('request',lambda req:writes.append((req.method,req.url)) if req.url.startswith(api)
                and req.method not in ('GET','HEAD','OPTIONS') else None)
        def spectrum_route(route):
            query={k:v[0] for k,v in parse_qs(urlparse(route.request.url).query).items()}
            requests.append(query)
            if state['failure'] and query.get('col')==SIGNAL and (state['failure']=='all' or query.get('tp_id')=='9'):
                route.fulfill(status=503,json={'detail':'Isolated spectrum retry failure'});return
            response=route.fetch()
            body=response.json()
            if response.ok:
                responses.append({'query':query,'body':body})
            if state['legacy']:
                body={key:value for key,value in body.items() if key in {'mode','col','fs_hz','n_samples','nan_count','freqs','mag','mean_rpm','min_rpm','max_rpm','rpm_col','tp_id'}}
            if state['missing_identity'] and query.get('col')==SIGNAL:
                body.pop('tp_id',None)
            if state['hold'] and query.get('mode')=='fft':
                held.append((route,response,body));return
            route.fulfill(response=response,json=body)
        context.route('**/src/utils/uplotSync.ts*',instrument)
        context.route('**/spectrum?*',spectrum_route)
        try:
            seed(page)
            worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            page.goto(web);settled(page)

            def verify_plot(column=SIGNAL,mode='fft',axis='hz',log=False,full=False):
                actual=snapshot(page,column)
                expected_y=('PSD (U²/Hz)' if mode=='welch' else 'Magnitude (U)')+(' · log10' if log else '')
                assert actual['axes']==['Order (cycles/rev)' if axis=='per_rev' else 'Frequency (Hz)',expected_y],actual['axes']
                assert len(actual['labels'])==(1 if full else 2),actual['labels']
                for position,label in enumerate(actual['labels'],1):
                    tp=None if full else next(p['id'] for p in POINTS if p['name'] in label)
                    matching=next(r for r in reversed(responses) if r['query'].get('col')==column
                        and r['query'].get('mode')==mode and r['query'].get('tp_id')==(str(tp) if tp is not None else None)
                        and bool(r['query'].get('rpm_col'))==(axis=='per_rev'))
                    body=matching['body'];verify_response(matching['query'],body)
                    xs,ys=actual['data'][position]
                    wanted_x=[value*60/body['mean_rpm'] if axis=='per_rev' else value for value in body['freqs']]
                    wanted_y=[math.log10(value) if value>0 else None for value in body['mag']] if log else body['mag']
                    assert len(xs)==len(wanted_x) and len(ys)==len(wanted_y)
                    assert all(math.isclose(a,b,rel_tol=1e-13,abs_tol=1e-12) for a,b in zip(xs,wanted_x))
                    assert all(a==b or (a is not None and b is not None and math.isclose(a,b,rel_tol=1e-13,abs_tol=1e-12)) for a,b in zip(ys,wanted_y))
                return actual

            initial=verify_plot();verify_plot(REPAIRED)
            assert all('tp_id' in q and 't0' not in q and 't1' not in q for q in requests),requests
            panel=details(page,REPAIRED)
            for point in POINTS:
                section=panel.get_by_role('region',name=f'{point["name"]} · {TEST} · TP {point["id"]} analysis',exact=True)
                expect(section).to_be_visible()
                for caption in ('Rows','Sample times','Samples','Signal quality','Estimator','Frequency bins','Display reduction','Peak','Timing'):
                    expect(section.get_by_text(caption,exact=True)).to_be_visible()
                expect(section).to_contain_text(str(point['start_idx']))
            expect(panel).to_contain_text('interpolat',ignore_case=True)
            assert_geometry(panel);capture_browser_view(context.new_cdp_session(page),output/'tp-details.png')
            close_details(page,panel,REPAIRED)
            # Frequency gestures are display-only and do not trigger a spectrum request.
            before=len(requests);over=plot(page).locator('.u-over');over.hover();page.mouse.wheel(0,-250)
            page.wait_for_timeout(350);zoomed=snapshot(page)
            assert zoomed['x'][1]-zoomed['x'][0]<initial['x'][1]-initial['x'][0]
            over.dblclick();page.wait_for_timeout(150);assert len(requests)==before
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('welch');settled(page)
            hz=verify_plot(mode='welch')
            page.get_by_role('group',name='Spectrum x-axis').get_by_role('button',name='Per rev',exact=True).click();settled(page)
            order=verify_plot(mode='welch',axis='per_rev')
            assert [trace[1] for trace in hz['data'][1:]]==[trace[1] for trace in order['data'][1:]],'Order conversion changed PSD values'
            panel=details(page);expect(panel).to_contain_text('Only frequency is remapped; PSD stays in U²/Hz.')
            capture_browser_view(context.new_cdp_session(page),output/'order-psd-details.png');close_details(page,panel)
            before=len(requests);page.get_by_role('button',name='Log scale',exact=True).click();settled(page)
            verify_plot(mode='welch',axis='per_rev',log=True);assert len(requests)==before
            page.get_by_role('button',name='Log scale',exact=True).click()
            page.get_by_role('group',name='Spectrum x-axis').get_by_role('button',name='Hz',exact=True).click();settled(page)
            print('PASS: actual browser spectrum arrays, per-TP details, FFT/PSD/order/log labels, unchanged PSD magnitude and client-only frequency zoom',flush=True)

            state['failure']='partial'
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('fft')
            expect(plot(page).get_by_role('button',name='Retry',exact=True)).to_be_visible()
            assert len(snapshot(page)['labels'])==1
            panel=details(page);expect(panel).to_contain_text('TP 9');expect(panel).to_contain_text('Isolated spectrum retry failure');close_details(page,panel)
            state['failure']=None;plot(page).get_by_role('button',name='Retry',exact=True).click();settled(page);verify_plot()
            state['failure']='all'
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('welch')
            expect(plot(page).get_by_role('alert')).to_be_visible()
            expect(plot(page).locator('.uplot')).to_have_count(0)
            state['failure']=None;plot(page).get_by_role('button',name='Retry',exact=True).click();settled(page);verify_plot(mode='welch')
            # Delayed old estimator responses cannot replace a newer selection.
            state['hold']=True
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('fft')
            deadline=time.monotonic()+10
            while len(held)<8 and time.monotonic()<deadline:page.wait_for_timeout(50)
            assert held,'No spectrum response was held'
            expect(plot(page)).to_contain_text('Calculating spectrum')
            expect(plot(page).locator('.uplot')).to_have_count(0)
            state['hold']=False
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('welch')
            for route,response,body in held:
                try:route.fulfill(response=response,json=body)
                except Exception as error:assert 'closed' in str(error).lower() or 'invalid' in str(error).lower()
            held.clear();settled(page);verify_plot(mode='welch')
            state['missing_identity']=True
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('fft')
            expect(plot(page).get_by_role('alert')).to_contain_text('did not confirm the requested test-point interval')
            expect(plot(page).locator('.uplot')).to_have_count(0)
            state['missing_identity']=False
            plot(page).get_by_role('button',name='Retry',exact=True).click();settled(page);verify_plot()
            state['legacy']=True
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('welch');settled(page)
            panel=details(page);expect(panel).to_contain_text('unavailable',ignore_case=True);close_details(page,panel)
            state['legacy']=False
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('fft');settled(page)
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('welch');settled(page)
            print('PASS: identified partial/all failures, Retry, hidden stale data, delayed-response rejection and legacy metadata fallback',flush=True)

            page.get_by_role('group',name='Data source').get_by_role('button',name='Full test',exact=True).click();settled(page)
            verify_plot(mode='welch',full=True)
            panel=details(page);expect(panel).to_contain_text(TEST);assert_geometry(panel);close_details(page,panel)
            # Exercise the actual Full-test time zoom -> Spectrum request path,
            # rather than relying only on the direct API interval oracle above.
            modes=page.get_by_role('group',name='Plot mode',exact=True)
            modes.get_by_role('button',name='Full test',exact=True).click()
            time_plot=page.get_by_role('group',name=f'{SIGNAL} full test plot',exact=True)
            time_plot.locator('.u-over').wait_for();page.wait_for_load_state('networkidle')
            wait_for_chart_layout(page)
            time_plot.locator('.u-over').hover();page.mouse.wheel(0,-300)
            page.wait_for_timeout(700);page.wait_for_load_state('networkidle')
            full_range=page.evaluate("JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).fullRange")
            assert full_range and full_range[0]>0 and full_range[1]<(N-1)/FS,full_range
            modes.get_by_role('button',name='Spectrum',exact=True).click();settled(page)
            verify_plot(mode='welch',full=True)
            scoped=next(item for item in reversed(responses) if item['query'].get('col')==SIGNAL
                and item['query'].get('mode')=='welch' and 'tp_id' not in item['query'])
            assert math.isclose(float(scoped['query']['t0']),full_range[0],rel_tol=1e-13)
            assert math.isclose(float(scoped['query']['t1']),full_range[1],rel_tol=1e-13)
            body=scoped['body'];assert body['i0']>0 and body['i1']<N,body
            interval_evidence=verify_response(scoped['query'],body)
            panel=details(page)
            section=panel.get_by_role('region',name=f'{TEST} analysis',exact=True)
            expect(section.get_by_text('Rows',exact=True).locator('..').locator('dd')).to_contain_text(f'[{body["i0"]}, {body["i1"]})')
            centers=section.get_by_text('Sample times',exact=True).locator('..').locator('dd')
            for field in ('time_start_s','time_end_s'):
                expect(centers).to_contain_text(page.evaluate('value=>String(value)',body[field]))
            capture_browser_view(context.new_cdp_session(page),output/'full-cropped-details.png')
            close_details(page,panel)
            print('PASS: real Full-test time X zoom produces exact non-null Spectrum request bounds, native rows and restricted sample-time details',flush=True)
            cdp=context.new_cdp_session(page);window=cdp.send('Browser.getWindowForTarget')['windowId']
            baseline=page.evaluate('devicePixelRatio')
            for full in (True,False):
                if not full:
                    page.get_by_role('group',name='Data source').get_by_role('button',name='Selected TPs',exact=True).click();settled(page)
                for width,factor in ((1100,1),(1440,1.25),(1440,1.5)):
                    cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'width':width,'height':1000}})
                    set_browser_zoom(worker,page,factor)
                    page.wait_for_function('dpr=>Math.abs(devicePixelRatio-dpr)<.001',arg=baseline*factor);settled(page)
                    verify_plot(mode='welch',full=full)
                    panel=details(page);assert_geometry(panel)
                    tag=f'{"full" if full else "tp"}-{width}-{factor}'
                    capture_browser_view(cdp,output/f'{tag}-details.png');close_details(page,panel)
                    button=plot(page).get_by_role('button',name=f'Expand {SIGNAL}',exact=True)
                    button.focus();page.keyboard.press('Enter');wait_for_chart_layout(page)
                    verify_plot(mode='welch',full=full)
                    capture_browser_view(cdp,output/f'{tag}-expanded.png')
                    plot(page).get_by_role('button',name=f'Minimize {SIGNAL}',exact=True).click();settled(page)
                set_browser_zoom(worker,page,1)
            print('PASS: Full-source comparison, keyboard details/focus, desktop resize/maximize/restore, actual125%/150% zoom',flush=True)
            assert source_hashes==dataset_hashes(dataset),'Source files changed'
            assert not writes,writes
            unexpected=[error for error in errors if '503' not in error and 'Isolated spectrum retry failure' not in error]
            assert not unexpected,unexpected
            (output/'results.json').write_text(json.dumps({'api_cases':evidence,'native_oracle_cases':len(oracle_cache),
                'browser_full_interval':interval_evidence,
                'browser_spectrum_requests':len(requests),'source_files_unchanged':sorted(source_hashes),
                'unexpected_errors':unexpected,'unexpected_writes':writes},indent=2),encoding='utf-8')
            print('PASS: all source hashes unchanged, no unexpected browser errors or writes',flush=True)
        except Exception:
            page.screenshot(path=str(output/'failure.png'),full_page=True)
            (output/'failure-state.json').write_text(json.dumps({'errors':errors,'requests':requests,
                'body':page.locator('body').inner_text()},indent=2),encoding='utf-8')
            raise
        finally:
            context.close();request.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port',type=int,default=3150)
    parser.add_argument('--backend-port',type=int,default=8150)
    args=parser.parse_args()
    output=ROOT/'data/verification/spectrum-analysis';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-spectrum-analysis-') as directory:
        temporary=Path(directory)
        with servers(temporary,output,args.frontend_port,args.backend_port) as (web,api,dataset):
            run_checks(web,api,dataset,temporary,output)
    print('PASS: temporary fixtures/profile removed and owned servers stopped',flush=True)


if __name__=='__main__':
    main()
