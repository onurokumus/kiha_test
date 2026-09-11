"""Phase 6c real ZIP/combined-PNG verification with isolated data and Chromium.

Reuse Phase 6b's full-rate Python 3.13 oracle and sequential statistics-cache
warmup. Global Python uses only stdlib/Playwright. Test-only browser canvas
instrumentation observes the actual offscreen panels copied into each PNG.
No production source or user datasets/profiles are instrumented or modified.
"""
import argparse
import base64
import csv
import io
import json
import math
from pathlib import Path
import re
import tempfile
import time
import zipfile

from playwright.sync_api import expect, sync_playwright

import verify_plot_exports as single
from verify_data_quality import ROOT, servers
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout
from verify_filter_overlay import dataset_hashes
from verify_time_y_zoom import instrument

COLS = [single.LOAD,single.SECOND,single.REFERENCE,single.TINY,
        single.LOAD,single.SECOND,single.REFERENCE,single.TINY,single.LOAD]
FILTERS = [single.DESPIKE,single.MOVING,{}, {},
           {'kind':'moving_avg','winS':'.081'},
           {**single.DESPIKE,'threshold':'8'}, {'kind':'detrend'}, {}, {}]
DISPLAYS = ['overlay','filtered','original','original','overlay','filtered','filtered','original','original']
DATA = {1:'both',2:'filtered',3:'original',4:'original',5:'both',6:'filtered',7:'filtered',8:'original',9:'original'}

CANVAS_INSTRUMENT = r'''(()=>{
    window.__multiPngCaptures=[];
    const fill=CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText=function(text,x,y,...rest){
        if(!this.canvas.isConnected){
            (this.canvas.__verificationTexts??=[]).push({text:String(text),x,y});
        }
        return fill.call(this,text,x,y,...rest);
    };
    const draw=CanvasRenderingContext2D.prototype.drawImage;
    CanvasRenderingContext2D.prototype.drawImage=function(source,...args){
        if(source instanceof HTMLCanvasElement && !source.isConnected){
            const r=args.length===8?args.slice(4):args;
            (this.canvas.__verificationPanels??=[]).push({
                texts:source.__verificationTexts??[],
                x:r[0],y:r[1],width:r[2]??source.width,height:r[3]??source.height,
                sourceWidth:source.width,sourceHeight:source.height});
        }
        return draw.call(this,source,...args);
    };
    const encode=HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob=function(callback,...args){
        window.__multiPngCaptures.push({width:this.width,height:this.height,
            texts:this.__verificationTexts??[],panels:this.__verificationPanels??[]});
        return encode.call(this,callback,...args);
    };
})();'''


def seed(page):
    settings={'scatterX':single.LOAD,'scatterY':single.REFERENCE,'clustering':False,
              'gridColumns':COLS,'defaultViewMode':'tp'}
    session={'version':1,'currentTest':single.A,'xAxis':single.LOAD,'yAxis':single.REFERENCE,
        'axesUserSet':True,'selections':[{'test':test,'tpId':point,'hidden':False}
            for test,point in [(single.A,7),(single.A,9),(single.B,7),(single.C,7)]],
        'plotConfigs':COLS,'plotsUserEdited':True,'plotDensity':'nine','plotFilters':FILTERS,
        'plotShowOriginal':[True,False,False,False,True,False,False,False,False],
        'scatterCollapsed':True}
    page.add_init_script(f'''if(!sessionStorage.getItem('multi-export-seeded')){{
        localStorage.clear();sessionStorage.setItem('multi-export-seeded','true');
        localStorage.setItem('ptt.settings.v1',{json.dumps(json.dumps(settings))});
        localStorage.setItem('ptt.analysis-session.v1',{json.dumps(json.dumps(session))});
    }}''')
    page.add_init_script(CANVAS_INSTRUMENT)


def plot(page,slot=1,full=False):
    column=COLS[slot-1]
    occurrence=COLS[:slot-1].count(column)
    return page.get_by_role('group',name=f'{column} {"full test" if full else "time"} plot',exact=True).nth(occurrence)


def settled(page,full=False):
    for slot,display in enumerate(DISPLAYS,1):
        expect(plot(page,slot,full)).to_have_attribute('data-filter-display',display)
    wait_for_chart_layout(page)


def trigger(page):
    return page.get_by_role('button',name='Export selected plots',exact=True)


def open_panel(page):
    trigger(page).focus()
    page.keyboard.press('Enter')
    panel=page.get_by_role('dialog',name='Export selected plots',exact=True)
    expect(panel).to_be_visible()
    panel.get_by_role('checkbox',name='Include analysis metadata (ZIP)',exact=True).uncheck()
    return panel


def close_panel(page,panel):
    page.keyboard.press('Escape')
    expect(panel).not_to_be_visible()
    expect(trigger(page)).to_be_focused()


def select_slots(panel,slots,layout='2x2',order=None):
    panel.get_by_role('radio',name='2 × 2' if layout=='2x2' else '3 × 3',exact=True).check()
    for slot in range(1,10):
        panel.get_by_role('checkbox',name=f'Select plot {slot}: {COLS[slot-1]}',exact=True).uncheck()
    for slot in order or slots:
        panel.get_by_role('checkbox',name=f'Select plot {slot}: {COLS[slot-1]}',exact=True).check()
    for slot in slots:
        panel.get_by_role('combobox',name=f'CSV data for plot {slot}',exact=True).select_option(DATA[slot])


def verify_csv(content,request):
    rows=list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
    expected=single.oracle_rows(request)
    assert len(rows)==len(expected),(len(rows),len(expected))
    assert rows and list(rows[0])==list(expected[0]),(list(rows[0]),list(expected[0]))
    for actual,want in zip(rows,expected):
        for key,value in want.items():
            if isinstance(value,(int,float)):
                assert actual[key] and math.isclose(float(actual[key]),value,rel_tol=1e-13,abs_tol=1e-17),(actual,want)
            else:
                assert actual[key]==('' if value is None else value),(actual,want)
    assert 'unplotted_secret' not in content.decode()
    return rows


def download_zip(page,panel,output,label,posts,request,slots,layout):
    before=len(posts)
    path,name=single.download(page,panel.get_by_role('button',name='Download CSV ZIP',exact=True),output,label,'zip')
    assert len(posts)==before+1
    payload=posts[-1]
    assert payload['layout']==layout,payload
    assert [p['slot'] for p in payload['plots']]==sorted(slots),payload
    evidence={'file':path.name,'download_name':name,'layout':layout,'slots':sorted(slots),'entries':[]}
    all_rows={}
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None,'ZIP CRC check failed'
        names=archive.namelist()
        assert len(names)==len(set(names))==len(slots),names
        for ordinal,(entry,job) in enumerate(zip(names,payload['plots']),1):
            slot=job['slot']; item=job['request']
            assert re.match(fr'^{ordinal:02d}_slot-{slot}_',entry),entry
            assert entry.endswith('.csv') and '/' not in entry and '\\' not in entry and '..' not in entry,entry
            assert item['column']==COLS[slot-1] and item['data']==DATA[slot],item
            data=archive.read(entry)
            oracle_response=request.post('plot-export',data=item)
            assert oracle_response.ok,oracle_response.text()
            assert oracle_response.body()==data,'Archive entry differs from identical single-plot request'
            rows=verify_csv(data,item)
            all_rows[slot]=rows
            evidence['entries'].append({'name':entry,'slot':slot,'rows':len(rows),'header':list(rows[0]),
                'sources':item['sources'],'filter':item['filter'],'x_range':item['x_range']})
    if 1 in all_rows and 5 in all_rows:
        key=lambda row:row['source_test']==single.A and row['sample_index']=='400' and row['test_point_id']=='7'
        first=next((r for r in all_rows[1] if key(r)),None)
        fifth=next((r for r in all_rows[5] if key(r)),None)
        if first and fifth:
            assert first['load_N [filtered]']!=fifth['load_N [filtered]'],'Duplicate variable slots lost independent filters'
    return evidence,payload


def download_png(page,panel,output,label,slots,layout):
    page.evaluate('window.__multiPngCaptures=[]')
    path,name=single.download(page,panel.get_by_role('button',name='Download combined PNG',exact=True),output,label,'png')
    pixels=single.png_inspect(page,path,[])
    capture=page.evaluate('window.__multiPngCaptures.at(-1)')
    assert capture and len(capture['panels'])==len(slots),capture
    side=2 if layout=='2x2' else 3
    panels=capture['panels']
    slots_seen=[int(re.match(r'^Plot\s+(\d+)',row['text'])[1]) for row in capture['texts']
                if re.match(r'^Plot\s+\d+',row['text'])]
    for ordinal,p in enumerate(panels):
        assert p['texts'],'An exported panel lost its title/source/settings/legend text'
        assert p['width']>100 and p['height']>100,p
        assert p['x']>=0 and p['y']>=0 and p['x']+p['width']<=capture['width']+1 and p['y']+p['height']<=capture['height']+1,p
        if ordinal%side:
            assert p['x']>panels[ordinal-1]['x'] and p['y']==panels[ordinal-1]['y'],panels
        if ordinal>=side:
            assert p['y']>panels[ordinal-side]['y'],panels
    assert slots_seen==sorted(slots),(slots_seen,slots)
    # Three selected panels in 3x3 must leave the lower-right cell empty while
    # keeping all three rows in the image. Inspect the center of that cell.
    if layout=='3x3' and len(slots)==3:
        region=page.evaluate('''async url=>{
            const image=new Image();image.src=url;await image.decode();
            const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;
            const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);
            const x=Math.floor(image.width*5/6),y=Math.floor(image.height*5/6);
            const p=ctx.getImageData(x-8,y-8,16,16).data;const colors=new Set();
            for(let i=0;i<p.length;i+=4)colors.add([...p.slice(i,i+4)].join(','));return [...colors];
        }''','data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode())
        assert len(region)==1,region
        assert capture['height']>=3*panels[0]['height'],capture
    return {'file':path.name,'download_name':name,'layout':layout,'slots':slots_seen,**pixels,
            'panel_rectangles':[{k:p[k] for k in ('x','y','width','height')} for p in panels]}


def run_checks(web,api,dataset,temporary,output):
    single.ORACLE_DATASET=dataset
    single.ORACLE_CACHE.clear()
    extension=temporary/'extension';extension.mkdir()
    (extension/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'Multi plot export zoom',
        'version':'1.0','permissions':['tabs'],'background':{'service_worker':'background.js'}}))
    (extension/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as playwright:
        request=playwright.request.new_context(base_url=api+'/')
        single.fixtures(request)
        hashes=dataset_hashes(dataset)
        context=playwright.chromium.launch_persistent_context(str(temporary/'profile'),channel='chromium',
            headless=True,no_viewport=True,accept_downloads=True,args=[
            f'--disable-extensions-except={extension}',f'--load-extension={extension}','--window-size=1440,1000'])
        page=context.pages[0]
        posts,downloads,errors,writes,zip_evidence,png_evidence,held=[],[],[],[],[],[],[]
        state={'fail_late':False,'hold_zip':False,'validation_422':False}
        metadata_held=[]
        metadata_seen=set()
        page.on('response',lambda response:metadata_seen.add(response.url)
                if response.url in {api+'/tests/'+single.B,api+'/tests/'+single.C} else None)
        page.on('download',lambda item:downloads.append(item.suggested_filename))
        page.on('pageerror',lambda error:errors.append(error.stack or str(error)))
        page.on('console',lambda message:errors.append(message.text) if message.type=='error' else None)
        page.on('request',lambda req:writes.append((req.method,req.url)) if req.url.startswith(api)
                and req.method not in ('GET','HEAD','OPTIONS') and '/export-progress' not in req.url and not req.url.endswith('/plot-export/bundle') else None)

        def bundle_route(route):
            payload=route.request.post_data_json
            posts.append(payload)
            if state['validation_422']:
                route.fulfill(status=422,content_type='application/json',body=json.dumps({'detail':[{
                    'type':'too_long','loc':['body','plots',1,'request','sources'],
                    'msg':'List should have at most 128 items after validation, not 129'}]}))
            elif state['fail_late']:
                payload=json.loads(json.dumps(payload))
                last=payload['plots'][-1]['request']
                last['data']='filtered';last['filter']={'kind':'moving_avg','window_s':100}
                response=route.fetch(post_data=json.dumps(payload))
                assert response.status>=400,response.text()
                assert 'application/json' in response.headers.get('content-type','')
                route.fulfill(response=response)
            elif state['hold_zip']:
                state['hold_zip']=False
                held.append((route,route.fetch()))
            else:
                route.continue_()

        context.route('**/src/utils/uplotSync.ts*',instrument)
        context.route('**/plot-export/bundle',bundle_route)
        context.route(api+'/tests/'+single.A,lambda route:metadata_held.append((route,route.fetch())))
        try:
            seed(page)
            worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            page.goto(web,wait_until='domcontentloaded')
            deadline=time.monotonic()+15
            while (not metadata_held or len(metadata_seen)<2) and time.monotonic()<deadline:
                page.wait_for_timeout(50)
            assert metadata_held and len(metadata_seen)==2,(len(metadata_held),metadata_seen)
            # Both partial schemas omit a restored column. Give normalization
            # effects time to run before the active test's complete schema arrives.
            page.wait_for_timeout(500)
            for route,response in metadata_held:
                route.fulfill(response=response)
            context.unroute(api+'/tests/'+single.A)
            page.wait_for_load_state('networkidle');settled(page)
            actual=page.evaluate("JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).plotConfigs")
            assert actual==COLS,actual
            print('PASS: delayed active-test metadata preserves all nine restored variable/filter slots',flush=True)
            panel=open_panel(page)
            select_slots(panel,[1,2,5,9])
            item,_=download_zip(page,panel,output,'tp-four',posts,request,[1,2,5,9],'2x2');zip_evidence.append(item)
            png_evidence.append(download_png(page,panel,output,'tp-four',[1,2,5,9],'2x2'))
            # A fifth selection must be explicit and cannot silently truncate.
            panel.get_by_role('checkbox',name=f'Select plot 3: {COLS[2]}',exact=True).check()
            expect(panel.get_by_role('button',name='Download CSV ZIP',exact=True)).to_be_disabled()
            expect(panel.get_by_role('button',name='Download combined PNG',exact=True)).to_be_disabled()
            expect(panel).to_contain_text('Select at most 4 plots for a 2 × 2 layout.')
            select_slots(panel,list(range(1,10)),'3x3')
            item,_=download_zip(page,panel,output,'tp-nine',posts,request,list(range(1,10)),'3x3');zip_evidence.append(item)
            png_evidence.append(download_png(page,panel,output,'tp-nine',list(range(1,10)),'3x3'))
            select_slots(panel,[2,5,9],'3x3',order=[9,2,5])
            item,_=download_zip(page,panel,output,'tp-packed-order',posts,request,[2,5,9],'3x3');zip_evidence.append(item)
            png_evidence.append(download_png(page,panel,output,'tp-packed-order',[2,5,9],'3x3'))
            close_panel(page,panel)
            print('PASS: real2x2/3x3 ZIP entries match full-rate per-slot nativeDSP, duplicate variables keep distinct settings, capacity guard and ascending packed PNG/order/blank cells',flush=True)

            plot(page,1).get_by_role('button',name=f'Expand {COLS[0]}',exact=True).click()
            expect(trigger(page)).to_be_disabled()
            expect(trigger(page)).to_have_attribute('title','Restore the grid to export multiple plots.')
            page.get_by_role('group',name=f'{COLS[0]} time plot',exact=True).get_by_role('button',name=f'Minimize {COLS[0]}',exact=True).click()
            settled(page)
            panel=open_panel(page);select_slots(panel,[1,2,5,9])
            state['fail_late']=True
            before=len(downloads)
            panel.get_by_role('button',name='Download CSV ZIP',exact=True).click()
            expect(panel.get_by_role('alert')).to_contain_text('window longer')
            assert len(downloads)==before,'A failed late job delivered a partial ZIP'
            state['fail_late']=False
            item,_=download_zip(page,panel,output,'tp-retry',posts,request,[1,2,5,9],'2x2');zip_evidence.append(item)
            state['validation_422']=True;before=len(downloads)
            panel.get_by_role('button',name='Download CSV ZIP',exact=True).click()
            expect(panel.get_by_role('alert')).to_contain_text('at most 128 items')
            assert len(downloads)==before,'A validation failure delivered a ZIP'
            state['validation_422']=False
            item,_=download_zip(page,panel,output,'tp-validation-retry',posts,request,[1,2,5,9],'2x2');zip_evidence.append(item)
            # Close suppresses delivery even after the backend completed ZIP staging.
            state['hold_zip']=True;before=len(downloads)
            panel.get_by_role('button',name='Download CSV ZIP',exact=True).click()
            page.wait_for_timeout(800)
            assert held,'ZIP response was not held'
            close_panel(page,panel)
            for route,response in held:
                try:route.fulfill(response=response)
                except Exception as error:
                    assert 'closed' in str(error).lower() or 'invalid' in str(error).lower(),str(error)
            held.clear();page.wait_for_timeout(350)
            assert len(downloads)==before,'Closed ZIP export delivered a late file'
            panel=open_panel(page);select_slots(panel,[1,2,5,9])
            page.evaluate('''()=>{
                window.__originalMultiToBlob=HTMLCanvasElement.prototype.toBlob;window.__multiRelease=null;
                HTMLCanvasElement.prototype.toBlob=function(callback,...args){
                    window.__originalMultiToBlob.call(this,blob=>{window.__multiRelease=()=>callback(blob);},...args);
                };
            }''')
            before=len(downloads)
            panel.get_by_role('button',name='Download combined PNG',exact=True).click()
            page.wait_for_function('typeof window.__multiRelease==="function"')
            close_panel(page,panel)
            page.evaluate('''()=>{HTMLCanvasElement.prototype.toBlob=window.__originalMultiToBlob;window.__multiRelease();}''')
            page.wait_for_timeout(350)
            assert len(downloads)==before,'Closed combined PNG delivered a late file'
            print('PASS: maximized guard/restore, all-or-error late DSP failure and retry, ZIP/PNG Close suppresses late downloads',flush=True)

            page.get_by_role('button',name='Full test',exact=True).click();settled(page,True)
            page.get_by_role('group',name='Full-test trace style').get_by_role('button',name='Min/max',exact=True).click()
            settled(page,True)
            panel=open_panel(page);select_slots(panel,list(range(1,10)),'3x3')
            item,payload=download_zip(page,panel,output,'full-nine',posts,request,list(range(1,10)),'3x3');zip_evidence.append(item)
            assert all(p['request']['x_range'] is None for p in payload['plots'])
            assert all(p['request']['sources'][0]['display']=='envelope' for p in payload['plots'])
            png_evidence.append(download_png(page,panel,output,'full-nine',list(range(1,10)),'3x3'))
            close_panel(page,panel)
            over=plot(page,1,True).locator('.u-over');over.hover();page.mouse.wheel(0,-240)
            page.wait_for_timeout(700);settled(page,True)
            panel=open_panel(page);select_slots(panel,[1,2,5,9])
            item,payload=download_zip(page,panel,output,'full-cropped',posts,request,[1,2,5,9],'2x2');zip_evidence.append(item)
            assert all(p['request']['x_range'] is not None for p in payload['plots'])
            close_panel(page,panel)
            print('PASS: Full-test9-plot envelope ZIP/PNG, exact current filter contexts and explicitX crop per slot',flush=True)

            cdp=context.new_cdp_session(page);window=cdp.send('Browser.getWindowForTarget')['windowId']
            baseline_dpr=page.evaluate('devicePixelRatio')
            for full in (True,False):
                if not full:
                    page.get_by_role('button',name='Test points',exact=True).click();settled(page)
                for width,factor in ((1100,1),(1440,1.25),(1440,1.5)):
                    cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'width':width,'height':1000}})
                    set_browser_zoom(worker,page,factor)
                    page.wait_for_function('dpr=>Math.abs(devicePixelRatio-dpr)<.001',arg=baseline_dpr*factor)
                    settled(page,full)
                    panel=open_panel(page);select_slots(panel,[1,2,5,9])
                    single.check_panel_layout(panel)
                    tag=f'{"full" if full else "tp"}-{width}-{factor}'
                    item,_=download_zip(page,panel,output,tag,posts,request,[1,2,5,9],'2x2');zip_evidence.append(item)
                    capture_browser_view(cdp,output/f'{tag}-dialog.png')
                    if factor==1.5:
                        png_evidence.append(download_png(page,panel,output,tag,[1,2,5,9],'2x2'))
                    close_panel(page,panel)
                set_browser_zoom(worker,page,1)
            print('PASS: keyboard/Escape/focus,1100px resize, actual125%/150% browser zoom and real layout downloads',flush=True)

            assert dataset_hashes(dataset)==hashes,'Source files changed'
            assert not writes,writes
            unexpected=[error for error in errors if '400' not in error and '422' not in error and 'window longer' not in error]
            assert not unexpected,unexpected
            (output/'results.json').write_text(json.dumps({'downloads':downloads,'archives':zip_evidence,'pngs':png_evidence,
                'requests':posts,'delayed_metadata_regression':True,'validation_422_retry':True,
                'unchanged_source_files':sorted(hashes),'unexpected_errors':unexpected,'unexpected_writes':writes},indent=2))
            print('PASS: source/raw/pyramid/meta/TP hashes unchanged; no unexpected writes or browser errors',flush=True)
        except Exception:
            page.screenshot(path=str(output/'failure.png'),full_page=True)
            (output/'failure-state.json').write_text(json.dumps({'errors':errors,'posts':posts,'downloads':downloads,
                'buttons':page.get_by_role('button').all_text_contents()},indent=2))
            raise
        finally:
            context.close();request.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port',type=int,default=3150)
    parser.add_argument('--backend-port',type=int,default=8150)
    args=parser.parse_args()
    output=ROOT/'data/verification/multi-plot-exports';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-multi-plot-exports-') as directory:
        temporary=Path(directory)
        with servers(temporary,output,args.frontend_port,args.backend_port) as (web,api,dataset):
            run_checks(web,api,dataset,temporary,output)
    print('PASS: temporary fixtures/profile removed; both owned servers stopped',flush=True)


if __name__=='__main__':
    main()
