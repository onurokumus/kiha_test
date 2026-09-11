"""Phase 6b single-time-plot CSV/PNG checks using isolated real data and Chromium.

Global Python runs only stdlib/Playwright. All backend/native processing uses
the project's Python 3.13 server. PNG headers use struct and pixel inspection
uses the browser's native decoder; no additional image dependency is needed.
Only this browser's transformed uPlot module is instrumented for verification.
Temporary data/profile and both owned servers are cleaned on completion.
"""
import argparse
import base64
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

from verify_data_quality import ROOT, servers, upload
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout
from verify_filter_overlay import dataset_hashes, snapshot
from verify_time_y_zoom import instrument

A, B, C = 'plot_export_alpha', 'plot_export_beta', 'plot_export_missing'
LOAD, SECOND, REFERENCE, TINY = 'load_N', 'second_N', 'reference_N', 'tiny_A'
COLS = [LOAD, SECOND, REFERENCE, TINY]
POINTS = [
    {'id': 7, 'name': 'Short spike', 'label': 'raw', 'start_s': .075,
     'end_s': .675, 'start_idx': 100, 'end_idx': 700},
    {'id': 9, 'name': 'Long spike', 'label': 'envelope', 'start_s': 1.975,
     'end_s': 8.475, 'start_idx': 2000, 'end_idx': 8500},
]
DESPIKE = {'kind': 'despike', 'despikeWindowMs': '25', 'maxSpikeMs': '3',
           'threshold': '3.5', 'absFloor': '2', 'replacement': 'linear'}
MOVING = {'kind': 'moving_avg', 'winS': '.021'}
ORACLE_DATASET = None
ORACLE_CACHE = {}

# The backend runtime alone imports Arrow/Polars/NumPy. This read-only oracle
# uses actual stored timestamps and the full-rate shared DSP, before JSON plot
# rounding or display reduction. Fixture files cannot change during these reads.
ORACLE_CODE = r'''
import json, math, sys
import numpy as np
import polars as pl
from app import dsp, store
from app.locks import data_read
request=json.load(sys.stdin)
rows=[]
for source in request['sources']:
    name=source['test']; tp=source.get('tp_id'); column=request['column']
    with data_read(name):
        meta=store.get_meta(name)
        if request['data']=='original':
            i0,i1=store.testpoint_range(name,tp) if tp is not None else store.window_bounds(meta,source.get('t0'),source.get('t1'))
            frame=pl.read_parquet(store.TESTS_DIR/name/'data.parquet',columns=[meta['time_column'],column]).slice(i0,i1-i0)
            start=i0; filtered=None
        else:
            result=dsp.filtered_samples(name,[column],t0=source.get('t0'),t1=source.get('t1'),
                px=source.get('px',1500),display=source.get('display','auto'),tp_id=tp,**request['filter'])
            i0,i1=result.i0,result.i1; start=result.s0; frame=result.frame; filtered=result.filtered[column]
        times=frame[meta['time_column']].to_numpy(); values=frame[column].to_numpy()
        origin=float(times[0]) if tp is not None else None
        bounds=request.get('x_range')
        lo,hi=bounds if bounds is not None else (-math.inf,math.inf)
        scale=max(1,abs(lo),abs(hi),abs(origin or 0),max(abs(float(v)) for v in times)) if bounds is not None else 1
        tol=8*np.finfo(np.float64).eps*scale
        for offset,(time,value) in enumerate(zip(times,values)):
            index=start+offset; relative=float(time-origin) if origin is not None else None
            x=relative if relative is not None else float(time)
            if not (i0<=index<i1 and lo-tol<=x<=hi+tol): continue
            item={'source_test':name,'test_point_id':str(tp) if tp is not None else '',
                'sample_index':index,'time_s':float(time),'tp_time_s':relative}
            if request['data'] in ('original','both'):
                item[f'{column} [original]']=float(value) if math.isfinite(value) else None
            if filtered is not None:
                value=float(filtered[offset]);item[f'{column} [filtered]']=value if math.isfinite(value) else None
            rows.append(item)
json.dump(rows,sys.stdout,allow_nan=False)
'''


def oracle_rows(payload):
    key=json.dumps(payload,sort_keys=True)
    if key not in ORACLE_CACHE:
        result=subprocess.run([str(ROOT/'backend/.venv/Scripts/python.exe'),'-c',ORACLE_CODE],
            cwd=ROOT/'backend',env={**os.environ,'KIHA_DATA_DIR':str(ORACLE_DATASET)},
            input=json.dumps(payload),capture_output=True,text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,timeout=30)
        assert result.returncode==0,result.stderr
        ORACLE_CACHE[key]=json.loads(result.stdout)
    return ORACLE_CACHE[key]


def raw_value(test, column, index):
    if 4500 <= index < 4510:
        return None
    value = 500 if index in (400, 4000) else 10 + math.sin(index / 150)
    offset = 30 if test == B else 0
    if column == LOAD:
        return None if index == 300 else float(f'{value + offset:.9f}')
    if column == SECOND:
        return float(f'{2*value:.9f}')
    if column == REFERENCE:
        return 20.0 + offset
    if column == TINY:
        return float(f'{(index%7+1)*1.234567890123e-10:.15g}')
    raise AssertionError(column)


def fixtures(request):
    for test in (A,B,C):
        columns = [REFERENCE,TINY] if test == C else ([LOAD,REFERENCE,TINY] if test == B else COLS)
        lines = ['time,'+','.join(columns)+',unplotted_secret']
        for index in range(9000):
            if 4500 <= index < 4510:
                continue
            values = [raw_value(test,col,index) for col in columns]
            lines.append(f'{1000.125+index/1000:.3f},'+','.join('' if v is None else str(v) for v in values)+',99999')
        assert upload(request,test,'\n'.join(lines))['status']=='ready'
        response=request.put(f'tests/{test}/testpoints',data={'version':1,'test':test,'test_points':POINTS})
        assert response.ok,response.text()
        # Existing Windows cold-cache parallel statistics reads can race a
        # cache replacement (PermissionError). Warm this unrelated cache
        # sequentially so the export harness isolates download behavior.
        for column in columns+['unplotted_secret']:
            response=request.get(f'tests/{test}/tp_stats',params={'col':column})
            assert response.ok,response.text()


def seed(page):
    settings={'scatterX':LOAD,'scatterY':REFERENCE,'clustering':False,
              'gridColumns':COLS,'defaultViewMode':'tp'}
    session={'version':1,'currentTest':A,'xAxis':LOAD,'yAxis':REFERENCE,'axesUserSet':True,
        'selections':[{'test':test,'tpId':point,'hidden':False}
                      for test,point in [(A,7),(A,9),(B,7),(C,7)]],
        'plotConfigs':COLS,'plotsUserEdited':True,'plotDensity':'quad',
        'plotFilters':[DESPIKE,MOVING,{},{}],'plotShowOriginal':[True,False],
        'scatterCollapsed':True}
    page.add_init_script(f'''if (!sessionStorage.getItem('plot-export-seeded')) {{
        localStorage.clear(); sessionStorage.setItem('plot-export-seeded','true');
        localStorage.setItem('ptt.settings.v1',{json.dumps(json.dumps(settings))});
        localStorage.setItem('ptt.analysis-session.v1',{json.dumps(json.dumps(session))});
    }}''')


def plot(page,col=LOAD,full=False):
    return page.get_by_role('group',name=f'{col} {"full test" if full else "time"} plot',exact=True)


def open_export(page,col=LOAD,full=False):
    target=plot(page,col,full)
    button=target.get_by_role('button',name=f'Export {col} plot',exact=True)
    button.focus()
    page.keyboard.press('Enter')
    panel=page.get_by_role('dialog',name=f'Export {col} plot',exact=True)
    expect(panel).to_be_visible()
    panel.get_by_role('checkbox',name='Include analysis metadata (ZIP)',exact=True).uncheck()
    return panel


def close_export(page,panel,col=LOAD,full=False):
    page.keyboard.press('Escape')
    expect(panel).not_to_be_visible()
    expect(plot(page,col,full).get_by_role('button',name=f'Export {col} plot',exact=True)).to_be_focused()


def download(page,button,output,label,extension):
    button.focus()
    with page.expect_download() as event:
        page.keyboard.press('Enter')
    result=event.value
    assert result.failure() is None
    assert result.suggested_filename.endswith('.'+extension),result.suggested_filename
    target=output/f'{label}.{extension}'
    result.save_as(target)
    return target,result.suggested_filename


def csv_download(page,panel,output,label,choice,posts,request):
    panel.get_by_role('radio',name=choice,exact=True).check()
    before=len(posts)
    path,name=download(page,panel.get_by_role('button',name='Download CSV',exact=True),output,label,'csv')
    assert len(posts)==before+1,posts[before:]
    payload=posts[-1]
    assert payload['data']=={'Original':'original','Filtered':'filtered','Original and filtered':'both'}[choice],payload
    response=request.post('plot-export',data=payload)
    assert response.ok,response.text()
    assert response.body()==path.read_bytes(),'Browser CSV differs from exact captured request replay'
    rows=list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
    assert rows,'CSV has no data rows'
    column=payload['column']
    header=['source_test','test_point_id','sample_index','time_s','tp_time_s']
    if payload['data'] in ('original','both'):
        header += [f'{column} [original]']
    if payload['data'] in ('filtered','both'):
        header += [f'{column} [filtered]']
    assert list(rows[0])==header,(list(rows[0]),header)
    assert 'unplotted_secret' not in path.read_text(),header
    assert column in name,name
    expected_rows=oracle_rows(payload)
    assert len(rows)==len(expected_rows),(len(rows),len(expected_rows))
    for actual,expected in zip(rows,expected_rows):
        for key,value in expected.items():
            if isinstance(value,(int,float)):
                assert actual[key] and math.isclose(float(actual[key]),value,rel_tol=1e-13,abs_tol=1e-17),(actual,expected)
            else:
                assert actual[key]==('' if value is None else value),(actual,expected)
    for row in rows:
        index=int(row['sample_index'])
        assert math.isclose(float(row['time_s']),index/1000,abs_tol=1e-9),row
        if row['test_point_id']:
            point=next(p for p in POINTS if p['id']==int(row['test_point_id']))
            assert point['start_idx']<=index<point['end_idx'],row
            relative=(index-point['start_idx'])/1000
            assert math.isclose(float(row['tp_time_s']),relative,abs_tol=1e-9),row
        else:
            assert row['tp_time_s']=='',row
        original=raw_value(row['source_test'],column,index)
        if payload['data'] in ('original','both'):
            text=row[f'{column} [original]']
            assert (text=='') if original is None else math.isclose(float(text),original,rel_tol=1e-13,abs_tol=1e-17),row
        if payload['data'] in ('filtered','both'):
            text=row[f'{column} [filtered]']
            if original is None:
                assert text=='',row
            elif payload['filter']['kind']=='despike':
                if index in (400,4000):
                    expected=(raw_value(row['source_test'],column,index-1)+raw_value(row['source_test'],column,index+1))/2
                    assert math.isclose(float(text),expected,rel_tol=1e-10,abs_tol=1e-9),row
                else:
                    assert math.isclose(float(text),original,rel_tol=1e-13,abs_tol=1e-12),row
    return rows,payload,path


def assert_complete(rows,payload):
    expected=[(r['source_test'],r['test_point_id'],r['sample_index']) for r in oracle_rows(payload)]
    actual=[(r['source_test'],r['test_point_id'],int(r['sample_index'])) for r in rows]
    assert actual==expected,(len(actual),len(expected),actual[:2],expected[:2],actual[-2:],expected[-2:])


def png_inspect(page,path,expected_colors):
    data=path.read_bytes()
    assert data[:8]==b'\x89PNG\r\n\x1a\n'
    width,height=struct.unpack('>II',data[16:24])
    assert width>=300 and height>=150,(width,height)
    result=page.evaluate('''async ({url,colors}) => {
        const image=new Image(); image.src=url; await image.decode();
        const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;
        const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);
        const pixels=ctx.getImageData(0,0,canvas.width,canvas.height).data;
        const wanted=colors.map(c=>[parseInt(c.slice(1,3),16),parseInt(c.slice(3,5),16),parseInt(c.slice(5,7),16)]);
        let opaque=0, light=0;const distinct=new Set(),counts=wanted.map(()=>0);
        for(let i=0;i<pixels.length;i+=4){
            if(pixels[i+3]===255)opaque++;
            if(pixels[i]+pixels[i+1]+pixels[i+2]>250)light++;
            if(i%64===0)distinct.add(`${pixels[i]},${pixels[i+1]},${pixels[i+2]},${pixels[i+3]}`);
            wanted.forEach((c,k)=>{if(c.every((v,j)=>Math.abs(v-pixels[i+j])<12)&&pixels[i+3]>200)counts[k]++;});
        }
        return {width:canvas.width,height:canvas.height,opaque,light,distinct:distinct.size,colorPixels:counts};
    }''',{'url':'data:image/png;base64,'+base64.b64encode(data).decode(),'colors':expected_colors})
    assert (result['width'],result['height'])==(width,height)
    assert result['opaque']==width*height,result
    assert result['light']>500 and result['distinct']>20,result
    assert all(value>5 for value in result['colorPixels']),result
    return result


def check_panel_layout(panel):
    assert panel.evaluate('''el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.top>=0&&
        r.right<=innerWidth+1&&r.bottom<=innerHeight+1&&el.scrollWidth<=el.clientWidth+1;}'''),'Export dialog overflows viewport'


def wait_plots(page,full=False):
    for col,display in ((LOAD,'overlay'),(SECOND,'filtered'),(REFERENCE,'original'),(TINY,'original')):
        expect(plot(page,col,full)).to_have_attribute('data-filter-display',display)
    wait_for_chart_layout(page)


def run_checks(web,api,dataset,temporary,output):
    global ORACLE_DATASET
    ORACLE_DATASET=dataset
    extension=temporary/'extension'
    extension.mkdir()
    (extension/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'Plot export zoom checks',
        'version':'1.0','permissions':['tabs'],'background':{'service_worker':'background.js'}}))
    (extension/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as playwright:
        request=playwright.request.new_context(base_url=api+'/')
        fixtures(request)
        before_hashes=dataset_hashes(dataset)
        context=playwright.chromium.launch_persistent_context(str(temporary/'profile'),channel='chromium',
            headless=True,no_viewport=True,accept_downloads=True,args=[
            f'--disable-extensions-except={extension}',f'--load-extension={extension}','--window-size=1440,1000'])
        page=context.pages[0]
        errors,posts,downloads,pngs,writes,held=[],[],[],[],[],[]
        state={'fail_export':False,'hold_filter':False}
        page.on('pageerror',lambda error:errors.append(error.stack or str(error)))
        page.on('console',lambda message:errors.append(message.text) if message.type=='error' else None)
        page.on('download',lambda item:downloads.append(item.suggested_filename))
        page.on('request',lambda req:writes.append((req.method,req.url)) if req.url.startswith(api)
                and req.method not in ('GET','HEAD','OPTIONS') and '/export-progress' not in req.url and not req.url.endswith('/plot-export') else None)

        def export_route(route):
            posts.append(route.request.post_data_json)
            if state['fail_export']:
                route.fulfill(status=503,json={'detail':'Isolated export failure'})
            else:
                route.continue_()

        def filter_route(route):
            query=parse_qs(urlparse(route.request.url).query)
            if state['hold_filter'] and query.get('cols')==[LOAD] and query.get('tp_id')==['9']:
                state['hold_filter']=False
                held.append((route,route.fetch()))
            else:
                route.continue_()

        context.route('**/src/utils/uplotSync.ts*',instrument)
        context.route('**/plot-export',export_route)
        context.route('**/filter?*',filter_route)
        try:
            seed(page)
            worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            page.goto(web)
            page.wait_for_load_state('networkidle')
            wait_plots(page)
            panel=open_export(page)
            rows,payload,_=csv_download(page,panel,output,'tp-original','Original',posts,request)
            assert_complete(rows,payload)
            assert {(s['test'],s['tp_id']) for s in payload['sources']}=={(A,7),(A,9),(B,7)},payload
            baseline_count=len(rows)
            assert baseline_count>7000,baseline_count
            filtered,filtered_payload,_=csv_download(page,panel,output,'tp-filtered','Filtered',posts,request)
            assert_complete(filtered,filtered_payload)
            both,both_payload,_=csv_download(page,panel,output,'tp-both','Original and filtered',posts,request)
            assert_complete(both,both_payload)
            assert len(both)==baseline_count
            snap=snapshot(page)
            colors=list(dict.fromkeys(s['color'][:7] for s in snap['series'] if 'filtered' in s['label']))
            # CSV radio is deliberately Original while PNG remains the current overlay.
            panel.get_by_role('radio',name='Original',exact=True).check()
            path,_=download(page,panel.get_by_role('button',name='Download PNG',exact=True),output,'tp-overlay','png')
            pngs.append({'file':path.name,**png_inspect(page,path,colors)})
            close_export(page,panel)
            # Hold only this isolated browser's asynchronous PNG encoding.
            # Closing the dialog must suppress a later completed download.
            page.evaluate('''()=>{
                window.__originalToBlob=HTMLCanvasElement.prototype.toBlob;
                window.__pngRelease=null;
                HTMLCanvasElement.prototype.toBlob=function(callback,type,quality){
                    window.__originalToBlob.call(this,blob=>{window.__pngRelease=()=>callback(blob);},type,quality);
                };
            }''')
            panel=open_export(page)
            count=len(downloads)
            panel.get_by_role('button',name='Download PNG',exact=True).click()
            page.wait_for_function('typeof window.__pngRelease === "function"')
            close_export(page,panel)
            page.evaluate('''()=>{HTMLCanvasElement.prototype.toBlob=window.__originalToBlob;window.__pngRelease();}''')
            page.wait_for_timeout(350)
            assert len(downloads)==count,'Closed PNG export produced a late download'
            print('PASS: TP original/filtered/both full-resolution real CSV, multiple source IDs, missing-column exclusion, exact values/gaps/spike repair; actual overlay PNG',flush=True)

            plot(page).get_by_role('button',name=f'Y axis for {LOAD}',exact=True).click()
            ypanel=page.get_by_role('dialog',name=f'Y axis for {LOAD}',exact=True)
            ypanel.get_by_label('Minimum',exact=True).fill('8')
            ypanel.get_by_label('Maximum',exact=True).fill('12')
            ypanel.get_by_role('button',name='Apply range',exact=True).click()
            panel=open_export(page)
            rows,payload,_=csv_download(page,panel,output,'tp-yzoom-original','Original',posts,request)
            assert len(rows)==baseline_count,'Y zoom must not discard CSV rows'
            close_export(page,panel)
            over=plot(page).locator('.u-over')
            over.hover()
            page.mouse.wheel(0,-240)
            page.wait_for_timeout(450)
            panel=open_export(page)
            cropped,payload,_=csv_download(page,panel,output,'tp-xzoom-both','Original and filtered',posts,request)
            assert_complete(cropped,payload)
            assert len(cropped)<baseline_count
            assert payload['filter']['kind']=='despike'
            close_export(page,panel)
            page.get_by_role('button',name='Reset zoom',exact=True).click()
            page.get_by_role('button',name=re.compile('Expand selection tray')).click()
            page.get_by_role('button',name=f'Hide Short spike from {B}',exact=True).click()
            expect(plot(page)).to_have_attribute('data-filter-display','overlay')
            panel=open_export(page)
            hidden,payload,_=csv_download(page,panel,output,'tp-hidden','Original',posts,request)
            assert all(source['test']!=B for source in payload['sources'])
            assert_complete(hidden,payload)
            close_export(page,panel)
            print('PASS: exact X sample-center crop, complete-TP despike before crop, Y zoom preserves rows, hidden sources excluded',flush=True)

            panel=open_export(page,SECOND)
            rows,payload,_=csv_download(page,panel,output,'tp-independent-filter','Filtered',posts,request)
            assert payload['filter']['kind']=='moving_avg' and payload['filter']['window_s']==.021,payload
            assert all(source['test']==A for source in payload['sources'])
            assert_complete(rows,payload)
            close_export(page,panel,SECOND)
            panel=open_export(page,TINY)
            expect(panel.get_by_role('radio',name='Filtered',exact=True)).to_be_disabled()
            expect(panel.get_by_role('radio',name='Original and filtered',exact=True)).to_be_disabled()
            tiny,payload,_=csv_download(page,panel,output,'tiny-original','Original',posts,request)
            assert_complete(tiny,payload)
            assert all(float(row[f'{TINY} [original]'])>0 for row in tiny if row[f'{TINY} [original]'])
            close_export(page,panel,TINY)
            print('PASS: per-plot independent actual filter settings and tiny full-precision original CSV; unfiltered choices disabled',flush=True)

            panel=open_export(page)
            state['fail_export']=True
            before_downloads=len(downloads)
            panel.get_by_role('button',name='Download CSV',exact=True).click()
            expect(panel.get_by_role('alert')).to_contain_text('Isolated export failure')
            assert len(downloads)==before_downloads,'Failed export produced a file'
            state['fail_export']=False
            csv_download(page,panel,output,'tp-export-retry','Original',posts,request)
            close_export(page,panel)
            plot(page).get_by_role('button',name=f'Expand {LOAD}',exact=True).click()
            state['hold_filter']=True
            plot(page).get_by_label('Threshold (MAD)',exact=True).fill('5')
            page.wait_for_timeout(650)
            assert held
            panel=open_export(page)
            expect(panel.get_by_role('radio',name='Filtered',exact=True)).to_be_disabled()
            expect(panel.get_by_role('radio',name='Original and filtered',exact=True)).to_be_disabled()
            expect(panel.get_by_role('button',name='Download PNG',exact=True)).to_be_disabled()
            expect(panel.get_by_role('button',name='Download CSV',exact=True)).to_be_enabled()
            csv_download(page,panel,output,'pending-original','Original',posts,request)
            close_export(page,panel)
            for route,response in held:
                route.fulfill(response=response)
            held.clear()
            expect(plot(page)).to_have_attribute('data-filter-display','overlay')
            plot(page).get_by_role('button',name=f'Minimize {LOAD}',exact=True).click()
            print('PASS: failure produces no file and retry succeeds; pending filter gates filtered CSV/PNG while original remains available',flush=True)

            page.get_by_role('button',name='Full test',exact=True).click()
            wait_plots(page,True)
            for mode in ('Line','Min/max'):
                page.get_by_role('group',name='Full-test trace style').get_by_role('button',name=mode,exact=True).click()
                expect(plot(page,full=True)).to_have_attribute('data-filter-display','overlay')
                panel=open_export(page,full=True)
                rows,payload,_=csv_download(page,panel,output,f'full-{mode.replace("/","").lower()}',
                                           'Original and filtered',posts,request)
                assert_complete(rows,payload)
                assert len(payload['sources'])==1 and payload['sources'][0]['test']==A and 'tp_id' not in payload['sources'][0]
                assert payload['sources'][0]['display']==('line' if mode=='Line' else 'envelope')
                assert payload['sources'][0]['expected_i0'] is not None
                path,_=download(page,panel.get_by_role('button',name='Download PNG',exact=True),output,
                                f'full-{mode.replace("/","").lower()}','png')
                pngs.append({'file':path.name,**png_inspect(page,path,['#dcdcaa'])})
                close_export(page,panel,full=True)
            over=plot(page,full=True).locator('.u-over')
            over.hover()
            page.mouse.wheel(0,-240)
            page.wait_for_timeout(700)
            expect(plot(page,full=True)).to_have_attribute('data-filter-display','overlay')
            panel=open_export(page,full=True)
            rows,payload,_=csv_download(page,panel,output,'full-zoom-both','Original and filtered',posts,request)
            assert_complete(rows,payload)
            assert payload['sources'][0]['t0'] is not None and payload['sources'][0]['t1'] is not None
            close_export(page,panel,full=True)
            print('PASS: Full Line/envelope real CSV+PNG, current X range and exact active filter request context',flush=True)

            cdp=context.new_cdp_session(page)
            window=cdp.send('Browser.getWindowForTarget')['windowId']
            baseline_dpr=page.evaluate('devicePixelRatio')
            for full in (True,False):
                if not full:
                    page.get_by_role('button',name='Test points',exact=True).click()
                    wait_plots(page)
                for width,factor in ((1100,1),(1440,1.25),(1440,1.5)):
                    cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'width':width,'height':1000}})
                    set_browser_zoom(worker,page,factor)
                    page.wait_for_function('dpr=>Math.abs(devicePixelRatio-dpr)<.001',arg=baseline_dpr*factor)
                    wait_for_chart_layout(page)
                    panel=open_export(page,full=full)
                    check_panel_layout(panel)
                    tag=f'{"full" if full else "tp"}-{width}-{factor}'
                    csv_download(page,panel,output,tag,'Original',posts,request)
                    capture_browser_view(cdp,output/f'{tag}-dialog.png')
                    close_export(page,panel,full=full)
                    plot(page,full=full).get_by_role('button',name=f'Expand {LOAD}',exact=True).focus()
                    page.keyboard.press('Enter')
                    panel=open_export(page,full=full)
                    check_panel_layout(panel)
                    path,_=download(page,panel.get_by_role('button',name='Download PNG',exact=True),output,tag+'-expanded','png')
                    pngs.append({'file':path.name,**png_inspect(page,path,[])})
                    close_export(page,panel,full=full)
                    plot(page,full=full).get_by_role('button',name=f'Minimize {LOAD}',exact=True).click()
                page.get_by_role('group',name='Plot layout').get_by_role('button',name='9',exact=True).click()
                panel=open_export(page,full=full)
                check_panel_layout(panel)
                capture_browser_view(cdp,output/f'{"full" if full else "tp"}-nine-150-dialog.png')
                close_export(page,panel,full=full)
                page.get_by_role('group',name='Plot layout').get_by_role('button',name='4',exact=True).click()
                set_browser_zoom(worker,page,1)
            print('PASS: real desktop1100px/125%/150% zoom, tight9-slot menu layout, keyboard/Escape/focus, maximize/restore CSV/PNG',flush=True)

            assert not writes,writes
            assert dataset_hashes(dataset)==before_hashes,'Stored source data or metadata changed'
            unexpected=[error for error in errors if not any(part in error for part in ('503','Isolated export failure'))]
            assert not unexpected,unexpected
            (output/'results.json').write_text(json.dumps({'downloads':downloads,'pngs':pngs,
                'browser_export_payloads':posts,'unchanged_source_files':sorted(before_hashes),
                'unexpected_browser_errors':unexpected,'unexpected_browser_mutations':writes},indent=2))
            print('PASS: source/raw/pyramid/meta/testpoint hashes unchanged; no unexpected writes/browser errors',flush=True)
        except Exception:
            page.screenshot(path=str(output/'failure.png'),full_page=True)
            (output/'failure-state.json').write_text(json.dumps({'errors':errors,'posts':posts,
                'downloads':downloads,'buttons':page.get_by_role('button').all_text_contents()},indent=2))
            raise
        finally:
            context.close()
            request.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port',type=int,default=3130)
    parser.add_argument('--backend-port',type=int,default=8130)
    args=parser.parse_args()
    output=ROOT/'data/verification/plot-exports'
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-plot-exports-') as directory:
        temporary=Path(directory)
        with servers(temporary,output,args.frontend_port,args.backend_port) as (web,api,dataset):
            run_checks(web,api,dataset,temporary,output)
    print('PASS: temporary fixtures/profile removed; both owned servers stopped',flush=True)


if __name__=='__main__':
    main()
