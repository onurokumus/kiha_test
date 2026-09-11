"""Phase 6f isolated XY native-row/CSV/ZIP/PNG and desktop browser checks.

Backend/native reads use Python3.13 only; global Python is stdlib/Playwright.
Reuse owned-server lifecycle, real browser zoom and native canvas inspection.
"""
import argparse
import csv
import io
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import parse_qs, urlparse
import zipfile

from playwright.sync_api import expect, sync_playwright
import verify_plot_exports as single
import verify_multi_plot_exports as multi
from verify_data_quality import ROOT, servers, upload
from verify_filter_overlay import dataset_hashes
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout
from verify_time_y_zoom import instrument

A, B = 'xy_export_alpha', 'xy_export_beta'
X, Y, TINY, COMMA, REFERENCE = 'position_N', 'load_N', 'tiny_A', 'sensor, µV', 'reference_V'
COLS = [Y, X, TINY, COMMA]
XS = [X, X, Y, X]
POINTS = [{'id':7,'name':'Long loop','label':'long','start_s':.5,'end_s':9.5,'start_idx':100,'end_idx':10010},
          {'id':9,'name':'Short loop','label':'short','start_s':10.5,'end_s':14.,'start_idx':10241,'end_idx':14010}]

ORACLE = r'''
import json,sys
import polars as pl
q=json.load(sys.stdin)
frame=pl.read_parquet(q['path'],columns=list(dict.fromkeys([q['time'],q['x'],q['y']]))).slice(q['i0'],q['i1']-q['i0'])
rows=[]
for i,row in enumerate(frame.iter_rows(named=True),q['i0']):
    import math
    x,y=row[q['x']],row[q['y']]
    if x is not None and y is not None and math.isfinite(x) and math.isfinite(y):
        rows.append([i,row[q['time']],x,y])
json.dump(rows,sys.stdout,allow_nan=False)
'''


def fixtures(request):
    for name,n,fs in ((A,16384,1024),(B,9000,512)):
        columns=[X,Y,TINY,REFERENCE]+([COMMA] if name==A else [])
        output=io.StringIO();writer=csv.writer(output);writer.writerow(['time',*columns])
        for i in range(n):
            values={X:math.sin(i/71),Y:math.cos(i/43)+.3*math.sin(i/31),
                    TINY:(i%29-14)*1.234567890123456e-11,REFERENCE:10+i/1000,COMMA:math.sin(i/25)}
            if i in (101,113,501): values[X]='nan' if i==101 else 'inf'
            if i in (101,150,777): values[Y]=''
            # Native extrema between stride samples: default CSV must retain them.
            if i==104: values[X]=1000
            if i==109: values[Y]=10000
            writer.writerow([format(1000.125+i/fs,'.12f'),*(values[c] for c in columns)])
        assert upload(request,name,output.getvalue())['status']=='ready'
        points=POINTS if name==A else [{'id':7,'name':'Other rate','label':'other','start_s':.5,
                                      'end_s':15.,'start_idx':17,'end_idx':8210}]
        assert request.put(f'tests/{name}/testpoints',data={'version':1,'test':name,'test_points':points}).ok
        for column in columns: assert request.get(f'tests/{name}/tp_stats',params={'col':column}).ok


def seed(page, sources=None):
    settings={'scatterX':X,'scatterY':REFERENCE,'clustering':False,'gridColumns':COLS,'defaultViewMode':'xy'}
    session={'version':1,**({'sources':sources} if sources is not None else {}),'currentTest':A,'xAxis':X,'yAxis':REFERENCE,'axesUserSet':True,
        'selections':[{'test':test,'tpId':tp,'hidden':False} for test,tp in ((A,7),(A,9),(B,7))],
        'plotConfigs':COLS,'plotsUserEdited':True,'plotDensity':'quad','viewMode':'xy','xySource':'tp',
        'xyXCols':XS,'xyYCols':COLS,'plotFilters':[{'kind':'moving_avg','winS':'.051'},{},{},{}],
        'scatterCollapsed':True}
    page.add_init_script(f'''if(!sessionStorage.getItem('xy-seeded')){{
        localStorage.clear();sessionStorage.setItem('xy-seeded','true');
        localStorage.setItem('ptt.settings.v1',{json.dumps(json.dumps(settings))});
        localStorage.setItem('ptt.analysis-session.v1',{json.dumps(json.dumps(session))});
    }}''')
    page.add_init_script(multi.CANVAS_INSTRUMENT)


def run_checks(web,api,dataset,temporary,output):
    cache,evidence,images,posts,responses,errors,writes,downloads={ },[],[],[],[],[],[],[]
    def oracle(source,x,y):
        query={'path':str(dataset/'tests'/source['test']/'data.parquet'),'time':source['expected_time_column'],
               'x':x,'y':y,'i0':source['expected_i0'],'i1':source['expected_i1']}
        key=json.dumps(query,sort_keys=True)
        if key not in cache:
            result=subprocess.run([str(ROOT/'backend/.venv/Scripts/python.exe'),'-c',ORACLE],
                input=key,capture_output=True,text=True,encoding='utf-8',timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            assert result.returncode==0,result.stderr
            cache[key]=json.loads(result.stdout)
        return cache[key]

    def inside(value,bounds):
        if bounds is None:return True
        lo,hi=bounds;tol=8*2.220446049250313e-16*max(abs(lo),abs(hi))
        return lo-tol<=value<=hi+tol

    def verify_csv(content,payload):
        rows=list(csv.DictReader(io.StringIO(content)));expected=[]
        for source in payload['sources']:
            expected.extend((source,row) for row in oracle(source,payload['x_column'],payload['column'])
                            if inside(row[2],payload.get('x_range')) and inside(row[3],payload.get('y_range')))
        assert len(rows)==len(expected),(len(rows),len(expected),payload)
        xcol=payload['x_column'];ycol=payload['column'];same=xcol==ycol
        assert list(rows[0])[-(1 if same else 2):]==([f'{xcol} [X/Y]'] if same else [f'{xcol} [X]',f'{ycol} [Y]'])
        for actual,(source,(index,t,x,y)) in zip(rows,expected):
            assert actual['source_test']==source['test'] and actual['test_point_id']==str(source.get('tp_id',''))
            assert int(actual['sample_index'])==index and float(actual['time_s'])==t
            assert float(actual[f'{xcol} [X/Y]' if same else f'{xcol} [X]'])==x
            if not same: assert float(actual[f'{ycol} [Y]'])==y
            assert int(actual['source_i0'])==source['expected_i0'] and int(actual['source_i1'])==source['expected_i1']
            assert actual['time_column']==source['expected_time_column'] and actual['method_version']=='kiha-xy-v2'
            assert actual['missing_values']=='omit_nonfinite_pairs' and actual['prefilter']=='none'
        return {'rows':len(rows),'sources':payload['sources'],'x':xcol,'y':ycol,
                'x_range':payload.get('x_range'),'y_range':payload.get('y_range')}

    def plot(page,slot=1):
        index=(slot-1)%4 if slot<9 else 0
        return page.get_by_role('group',name=f'{COLS[index]} versus {XS[index]} XY plot',exact=True).nth((slot-1)//4)

    def settled(page):
        page.wait_for_load_state('networkidle')
        for target in page.locator('[role=group][aria-label$=" XY plot"]').all():
            target.locator('.uplot').wait_for()
            expect(target).not_to_contain_text('Loading paired samples')
            expect(target).not_to_contain_text('Updating XY view')
        wait_for_chart_layout(page)

    def panel(page,slot=1):
        target=plot(page,slot);label=target.get_attribute('aria-label').removesuffix(' XY plot')
        target.get_by_role('button',name=f'Export {label} plot',exact=True).focus();page.keyboard.press('Enter')
        modal=page.get_by_role('dialog',name=f'Export {label} plot',exact=True)
        expect(modal).to_be_visible();single.check_panel_layout(modal)
        modal.get_by_role('checkbox',name='Include analysis metadata (ZIP)',exact=True).uncheck()
        expect(modal.get_by_role('radio')).to_have_count(0)
        return modal

    def close(page,modal):page.keyboard.press('Escape');expect(modal).not_to_be_visible()

    def select_slots(modal,slots,layout='2x2'):
        modal.get_by_role('radio',name='2 × 2' if layout=='2x2' else '3 × 3',exact=True).check()
        for slot,checkbox in enumerate(modal.locator('input[type=checkbox][aria-label^="Select plot "]').all(),1):checkbox.set_checked(slot in slots)

    def csv_file(page,modal,label):
        before=len(posts)
        button=modal.get_by_role('button',name='Download CSV',exact=True)
        expect(button).to_be_enabled()
        path,name=single.download(page,button,output,label,'csv')
        assert len(posts)==before+1
        payload=posts[-1];evidence.append({'file':path.name,'filename':name,**verify_csv(path.read_text(encoding='utf-8'),payload)})
        return payload

    def zip_file(page,modal,label,slots):
        path,name=single.download(page,modal.get_by_role('button',name='Download CSV ZIP',exact=True),output,label,'zip')
        payload=posts[-1];assert [entry['slot'] for entry in payload['plots']]==slots
        with zipfile.ZipFile(path) as archive:
            assert len(archive.namelist())==len(slots)
            for ordinal,(name,entry) in enumerate(zip(archive.namelist(),payload['plots']),1):
                assert name.startswith(f'{ordinal:02d}_slot-{entry["slot"]}_')
                evidence.append({'file':path.name,'entry':name,**verify_csv(archive.read(name).decode('utf-8'),entry['request'])})
        return payload

    def png_file(page,modal,label,slots=None,layout='2x2'):
        page.evaluate('window.__multiPngCaptures=[]')
        if slots:result=multi.download_png(page,modal,output,label,slots,layout)
        else:
            path,name=single.download(page,modal.get_by_role('button',name='Download PNG',exact=True),output,label,'png')
            result={'file':path.name,'filename':name,**single.png_inspect(page,path,[])}
        capture=page.evaluate('window.__multiPngCaptures.at(-1)');cards=capture['panels'] if slots else [capture]
        texts=[]
        for card in cards:
            text=' '.join(row['text'] for row in card['texts'])
            assert 'kiha-xy-v2' in text and 'rows [' in text and 'prefilter=none' in text,text
            assert 'finite pairs' in text and 'X (' in text and 'Y (' in text,text
            texts.append(text)
        result['annotation_text']=texts;images.append(result);return texts

    def verify_display(page,slot=1,full=False):
        actual=plot(page,slot).locator('.uplot').evaluate('''el=>{const u=el.__verificationPlot;
            return {data:u.data,labels:u.series.slice(1).map(s=>s.label),axes:u.axes.map(a=>a.label)};}''')
        index=(slot-1)%4 if slot<9 else 0;x,y=XS[index],COLS[index]
        assert actual['axes']==[x,y],actual['axes']
        for facet,label in zip(actual['data'][1:],actual['labels']):
            test=B if B in label else A
            tp=None if full else 9 if 'TP 9' in label else 7
            response=next(item for item in reversed(responses) if item['test']==test
                and item['query'].get('y_col')==y and item['query'].get('x')==x
                and item['query'].get('tp_id')==(str(tp) if tp is not None else None))
            body=response['body'];pairs=body['series'][y]
            assert facet==[pairs['x'],pairs['y']]
            source={'test':test,'expected_i0':body['i0'],'expected_i1':body['i1'],'expected_time_column':body['time_column']}
            native=oracle(source,x,y);by_index={row[0]:row for row in native}
            assert pairs['finite_count']==len(native)
            assert pairs['missing_pair_count']==body['n_raw']-len(native)
            assert all(by_index[i][2:]==[a,b] for i,a,b in zip(pairs['sample_indices'],pairs['x'],pairs['y']))
            if tp is not None:
                expected=(100,10010) if test==A and tp==7 else (10241,14010) if test==A else (17,8210)
                assert (body['i0'],body['i1'])==expected
                assert 't0' not in response['query'] and 't1' not in response['query']

    extension=temporary/'extension';extension.mkdir()
    (extension/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'XY export zoom','version':'1.0',
        'permissions':['tabs'],'background':{'service_worker':'background.js'}}))
    (extension/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as playwright:
        request=playwright.request.new_context(base_url=api+'/');fixtures(request)
        source_catalog=request.get('analysis-sources').json()['sources'];hashes=dataset_hashes(dataset)
        context=playwright.chromium.launch_persistent_context(str(temporary/'profile'),channel='chromium',headless=True,
            no_viewport=True,accept_downloads=True,args=[f'--disable-extensions-except={extension}',f'--load-extension={extension}','--window-size=1440,1000'])
        page=context.pages[0];held=[];xyheld=[]
        state={'fail':False,'hold':False,'xyhold':False,'partial':False,'legacy':False,'noid':False}
        page.on('download',lambda item:downloads.append(item.suggested_filename))
        page.on('pageerror',lambda error:errors.append(error.stack or str(error)))
        page.on('console',lambda message:errors.append(message.text) if message.type=='error' else None)
        page.on('request',lambda req:writes.append((req.method,req.url)) if req.url.startswith(api)
                and req.method not in ('GET','HEAD','OPTIONS') and '/export-progress' not in req.url and '/xy-export' not in req.url else None)
        def export_route(route):
            payload=route.request.post_data_json;posts.append(payload)
            if state['fail']:
                payload=json.loads(json.dumps(payload));payload['plots'][-1]['request']['y_range']=[1e50,2e50]
                response=route.fetch(post_data=json.dumps(payload));assert response.status==400
                assert 'content-disposition' not in response.headers;route.fulfill(response=response)
            elif state['hold']:held.append((route,route.fetch()))
            else:route.continue_()
        def xy_route(route):
            query={k:v[0] for k,v in parse_qs(urlparse(route.request.url).query).items()}
            if state['partial'] and query.get('y_col')==Y and query.get('tp_id')=='9':
                route.fulfill(status=503,json={'detail':'Isolated XY source failure'});return
            response=route.fetch();body=response.json()
            if response.ok:
                responses.append({'test':urlparse(route.request.url).path.split('/')[-2],'query':query,'body':body})
            if state['legacy']:body.pop('method_version',None)
            if state['noid']:body.pop('tp_id',None)
            if state['xyhold']:xyheld.append((route,response,body))
            else:route.fulfill(response=response,json=body)
        context.route('**/src/utils/uplotSync.ts*',instrument)
        context.route('**/xy-export',export_route);context.route('**/xy-export/bundle',export_route)
        context.route('**/xy?*',xy_route)
        try:
            seed(page, source_catalog);worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            page.goto(web);settled(page)
            for slot in range(1,5):verify_display(page,slot)
            modal=panel(page);payload=csv_file(page,modal,'default-pairs')
            assert payload['x_range'] is None and payload['y_range'] is None and len(payload['sources'])==3
            assert any(int(row['sample_index'])==104 and row['source_test']==A for row in csv.DictReader((output/'default-pairs.csv').open(encoding='utf-8')))
            png_file(page,modal,'default-pairs');close(page,modal)
            for slot,label in ((2,'equal-axes'),(3,'tiny-values'),(4,'comma-column')):
                modal=panel(page,slot);data=csv_file(page,modal,label)
                if slot==4:assert len(data['sources'])==2
                close(page,modal)
            # Box zoom, wheel and Shift-drag affect both coordinate axes.
            over=plot(page).locator('.u-over');box=over.bounding_box()
            page.mouse.move(box['x']+box['width']*.2,box['y']+box['height']*.2);page.mouse.down()
            page.mouse.move(box['x']+box['width']*.8,box['y']+box['height']*.8,steps=8);page.mouse.up();page.wait_for_timeout(200)
            modal=panel(page);cropped=csv_file(page,modal,'box-crop');assert cropped['x_range'] and cropped['y_range'];close(page,modal)
            over.hover();page.mouse.wheel(0,-300);page.wait_for_timeout(200)
            page.keyboard.down('Shift');page.mouse.move(box['x']+box['width']*.5,box['y']+box['height']*.5)
            page.mouse.down();page.mouse.move(box['x']+box['width']*.58,box['y']+box['height']*.56,steps=8)
            page.mouse.up();page.keyboard.up('Shift');page.wait_for_timeout(200)
            modal=panel(page);panned=csv_file(page,modal,'wheel-pan');assert panned['x_range']!=cropped['x_range'] and panned['y_range']!=cropped['y_range']
            png_file(page,modal,'wheel-pan');close(page,modal)
            over.dblclick();page.wait_for_timeout(150)
            modal=panel(page);reset=csv_file(page,modal,'reset-pairs');assert reset['x_range'] is None and reset['y_range'] is None;close(page,modal)
            tiny=plot(page,3).locator('.uplot');before=tiny.evaluate('el=>el.__verificationPlot.scales.y.max-el.__verificationPlot.scales.y.min')
            plot(page,3).locator('.u-over').hover();page.mouse.wheel(0,-300);page.wait_for_timeout(200)
            after=tiny.evaluate('el=>el.__verificationPlot.scales.y.max-el.__verificationPlot.scales.y.min');assert after<before<1e-9,(before,after)
            modal=panel(page,3);csv_file(page,modal,'tiny-wheel');close(page,modal)
            print('PASS: exact TP/display/native pairs, precision, X=Y, exact comma names, unequal grids, native extrema, box/wheel/pan/reset and tiny-axis zoom',flush=True)

            tray=page.get_by_role('button',name='3 of 20 test points selected. Expand selection tray.',exact=True)
            tray.click()
            page.get_by_role('button',name=f'Hide Other rate from {B}',exact=True).click();settled(page)
            modal=panel(page);hidden=csv_file(page,modal,'hidden-tp');assert len(hidden['sources'])==2;close(page,modal)
            page.get_by_role('button',name=f'Show Other rate from {B}',exact=True).click();settled(page)
            page.get_by_role('button',name='3 of 20 test points selected. Collapse selection tray.',exact=True).click()
            plot(page).get_by_role('button',name=f'Expand {Y} versus {X}',exact=True).click();wait_for_chart_layout(page)
            expect(multi.trigger(page)).to_be_disabled()
            plot(page).locator('.u-legend .u-series').filter(has_text=B).locator('.u-label').click();page.wait_for_timeout(150)
            modal=panel(page);hidden=csv_file(page,modal,'hidden-legend');assert len(hidden['sources'])==2
            assert B not in png_file(page,modal,'hidden-legend')[0];close(page,modal)
            plot(page).locator('.uplot').evaluate('el=>el.__verificationPlot.series.forEach((s,i)=>i&&el.__verificationPlot.setSeries(i,{show:false}))')
            modal=panel(page);before=len(downloads);modal.get_by_role('button',name='Download CSV',exact=True).click()
            expect(modal.get_by_role('alert')).to_contain_text('Show at least one');assert len(downloads)==before;close(page,modal)
            plot(page).get_by_role('button',name=f'Minimize {Y} versus {X}',exact=True).click();settled(page)
            plot(page).locator('.u-over').hover();page.mouse.wheel(0,-300);page.wait_for_timeout(200)
            modal=multi.open_panel(page);select_slots(modal,[1,2,4]);quad=zip_file(page,modal,'selected-2x2',[1,2,4])
            assert quad['plots'][0]['request']['x_range'] and quad['plots'][1]['request']['x_range'] is None
            png_file(page,modal,'selected-2x2',[1,2,4]);close(page,modal)
            modal=multi.open_panel(page);select_slots(modal,[1,3,4],'3x3');zip_file(page,modal,'selected-3x3',[1,3,4]);png_file(page,modal,'selected-3x3',[1,3,4],'3x3');close(page,modal)
            page.evaluate('''({cols,xs})=>{const state=JSON.parse(localStorage.getItem('ptt.analysis-session.v1'));
                state.plotDensity='nine';state.plotConfigs=[...cols,...cols,cols[0]];
                state.xyYCols=state.plotConfigs;state.xyXCols=[...xs,...xs,xs[0]];
                localStorage.setItem('ptt.analysis-session.v1',JSON.stringify(state));}''',{'cols':COLS,'xs':XS})
            page.reload();settled(page)
            modal=multi.open_panel(page);expect(modal.locator('input[type=checkbox][aria-label^="Select plot "]')).to_have_count(9)
            zip_file(page,modal,'nine-slots',list(range(1,10)));png_file(page,modal,'nine-slots',list(range(1,10)),'3x3');close(page,modal)
            print('PASS: application/legend visibility, all-hidden rejection, maximize/restore, per-slot crops and selected2x2/3x3/nine-slot ZIP+PNG',flush=True)

            modal=multi.open_panel(page);select_slots(modal,[1,4]);before=len(downloads);state['fail']=True
            modal.get_by_role('button',name='Download CSV ZIP',exact=True).click();expect(modal.get_by_role('alert')).to_contain_text('Plot 4:')
            assert len(downloads)==before;state['fail']=False;zip_file(page,modal,'retry-late-failure',[1,4])
            before=len(downloads);state['hold']=True;modal.get_by_role('button',name='Download CSV ZIP',exact=True).click()
            deadline=time.monotonic()+20
            while not held and time.monotonic()<deadline:page.wait_for_timeout(50)
            assert held;close(page,modal);state['hold']=False
            for route,response in held:route.fulfill(response=response)
            held.clear();page.wait_for_timeout(350);assert len(downloads)==before
            state['partial']=True;page.reload();page.wait_for_load_state('networkidle');expect(plot(page)).to_contain_text('could not be loaded')
            modal=panel(page);expect(modal.get_by_role('button',name='Download CSV',exact=True)).to_be_disabled()
            expect(modal.get_by_role('button',name='Download PNG',exact=True)).to_be_disabled();close(page,modal)
            state['partial']=False;plot(page).get_by_role('button',name='Retry',exact=True).click()
            expect(plot(page)).not_to_contain_text('could not be loaded');settled(page)
            modal=panel(page);csv_file(page,modal,'retry-source');close(page,modal)
            state['legacy']=True;page.reload();settled(page);modal=panel(page)
            expect(modal.get_by_role('button',name='Download CSV',exact=True)).to_be_disabled();expect(modal).to_contain_text('current backend');close(page,modal)
            state['legacy']=False;state['noid']=True;page.reload();page.wait_for_load_state('networkidle')
            expect(plot(page).get_by_role('alert')).to_contain_text('did not confirm');expect(plot(page).locator('.uplot')).to_have_count(0)
            state['noid']=False;page.reload();settled(page)
            print('PASS: real late-slot failure/no partial attachment, Retry, Close suppression, partial loading and legacy/missing-identity guards',flush=True)

            source=page.get_by_role('group',name='Data source');source.get_by_role('button',name='Full test',exact=True).click();settled(page)
            modes=page.get_by_role('group',name='Plot mode',exact=True);modes.get_by_role('button',name='Full test',exact=True).click()
            page.wait_for_load_state('networkidle');time_plot=page.get_by_role('group',name=f'{Y} full test plot',exact=True).first
            time_plot.locator('.u-over').wait_for();wait_for_chart_layout(page);time_plot.locator('.u-over').hover();page.mouse.wheel(0,-300)
            page.wait_for_timeout(700);page.wait_for_load_state('networkidle')
            interval=page.evaluate("JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).fullRange");assert interval and interval[0]>0
            modes.get_by_role('button',name='XY',exact=True).click();settled(page);verify_display(page,full=True)
            modal=panel(page);full=csv_file(page,modal,'full-time-interval')
            assert full['sources'][0]['t0']==interval[0] and full['sources'][0]['t1']==interval[1]
            assert full['sources'][0]['expected_i0']>0 and full['x_range'] is None;close(page,modal)
            # Reset time scope while staying mounted: never export the prior cloud.
            state['xyhold']=True;page.get_by_role('button',name='Reset zoom',exact=True).click()
            deadline=time.monotonic()+15
            while not xyheld and time.monotonic()<deadline:page.wait_for_timeout(50)
            assert xyheld;expect(plot(page).locator('.uplot')).to_have_count(0)
            modal=panel(page);expect(modal.get_by_role('button',name='Download CSV',exact=True)).to_be_disabled();close(page,modal)
            state['xyhold']=False;source.get_by_role('button',name='Selected TPs',exact=True).click()
            for route,response,body in xyheld:
                try:route.fulfill(response=response,json=body)
                except Exception as cause:assert 'closed' in str(cause).lower() or 'invalid' in str(cause).lower()
            xyheld.clear();settled(page);verify_display(page)
            modal=panel(page);fresh=csv_file(page,modal,'after-stale-response');assert all('tp_id' in s for s in fresh['sources']);close(page,modal)
            cdp=context.new_cdp_session(page);window=cdp.send('Browser.getWindowForTarget')['windowId'];baseline=page.evaluate('devicePixelRatio')
            for full in (False,True):
                if full:source.get_by_role('button',name='Full test',exact=True).click();settled(page)
                for width,factor in ((1100,1),(1440,1.25),(1440,1.5)):
                    cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'width':width,'height':1000}})
                    set_browser_zoom(worker,page,factor);page.wait_for_function('dpr=>Math.abs(devicePixelRatio-dpr)<.001',arg=baseline*factor);settled(page)
                    tag=f'{"full" if full else "tp"}-{width}-{factor}'
                    modal=multi.open_panel(page);select_slots(modal,[1,3]);single.check_panel_layout(modal)
                    capture_browser_view(cdp,output/f'{tag}-dialog.png');zip_file(page,modal,tag,[1,3]);png_file(page,modal,tag,[1,3]);close(page,modal)
                    plot(page).get_by_role('button',name=f'Expand {Y} versus {X}',exact=True).click();wait_for_chart_layout(page)
                    modal=panel(page);png_file(page,modal,tag+'-expanded');close(page,modal)
                    expect(plot(page).get_by_role('button',name=f'Export {Y} versus {X} plot',exact=True)).to_be_focused()
                    plot(page).get_by_role('button',name=f'Minimize {Y} versus {X}',exact=True).click();settled(page)
                set_browser_zoom(worker,page,1)
            # Ensure edit controls and exports stay independently reachable.
            page.get_by_role('button',name='Edit plots',exact=True).click();wait_for_chart_layout(page)
            target=plot(page);expect(target.get_by_role('button',name='X variable',exact=True)).to_be_visible()
            modal=panel(page);single.check_panel_layout(modal);close(page,modal)
            page.get_by_role('button',name='Editing plots',exact=True).click()
            assert hashes==dataset_hashes(dataset),'Source files changed'
            unexpected=[error for error in errors if '400' not in error and '503' not in error]
            assert not unexpected,unexpected;assert not writes,writes
            (output/'results.json').write_text(json.dumps({'csv_entries':evidence,'images':images,'native_oracles':len(cache),
                'downloads':downloads,'xy_responses':len(responses),'source_files_unchanged':sorted(hashes),
                'unexpected_errors':unexpected,'unexpected_writes':writes},indent=2),encoding='utf-8')
            print(f'PASS: Full exact time scope, stale held response guard, keyboard/Escape/focus/edit controls,1100px/125%/150%, {len(evidence)} CSV entries, {len(images)} PNGs, {len(hashes)} unchanged files',flush=True)
        except Exception:
            page.screenshot(path=str(output/'failure.png'),full_page=True)
            (output/'failure-state.json').write_text(json.dumps({'errors':errors,'posts':posts,
                'body':page.locator('body').inner_text()},indent=2),encoding='utf-8');raise
        finally:context.close();request.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port',type=int,default=3150);parser.add_argument('--backend-port',type=int,default=8150)
    args=parser.parse_args();output=ROOT/'data/verification/xy-exports';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kiha-xy-exports-') as directory:
        temporary=Path(directory)
        with servers(temporary,output,args.frontend_port,args.backend_port) as (web,api,dataset):
            run_checks(web,api,dataset,temporary,output)
    print('PASS: owned servers stopped; temporary fixtures/profile removed',flush=True)


if __name__=='__main__':main()
