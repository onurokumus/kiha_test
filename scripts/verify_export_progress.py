"""Phase 6h real long exports, cancellation, retry and desktop progress UI.

Native fixture ingestion uses backend Python 3.13; global Python only runs
stdlib and Playwright. Controlled canvas delays are browser-only test barriers.
"""
import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import zipfile

from playwright.sync_api import expect, sync_playwright
from verify_data_quality import ROOT, servers
from verify_filter_overlay import dataset_hashes
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout
import verify_plot_exports as single
import verify_multi_plot_exports as multi

NAME = 'long_export'
COLS = ['force_N', 'position_N', 'reference_V', 'tiny_A']
ROWS = 700_000
FIXTURE = r'''
import numpy as np, polars as pl
from app import store
from app.ingest import ingest_csv
n=700_000; i=np.arange(n); rng=np.random.default_rng(81)
directory=store.TESTS_DIR/'long_export'; directory.mkdir(parents=True)
raw=directory/'raw.csv'
pl.DataFrame({'time':i/1024, 'force_N':np.sin(i/31)+rng.normal(0,.01,n),
 'position_N':np.cos(i/57), 'reference_V':10+i/1e5, 'tiny_A':np.sin(i/79)*1e-9}).write_csv(raw)
ingest_csv(raw,'long_export',time_mode='column',time_column='time')
store.write_json_atomic(directory/'testpoints.json',{'version':1,'test':'long_export',
 'test_points':[{'id':7,'name':'Long native interval','label':'long','start_idx':0,'end_idx':n,'start_s':0,'end_s':n/1024}]})
'''


def seed(page):
    settings = {'scatterX': COLS[0], 'scatterY': COLS[2], 'gridColumns': COLS, 'clustering': False}
    session = {'version':1,'currentTest':NAME,'xAxis':COLS[0],'yAxis':COLS[2],'axesUserSet':True,
        'selections':[{'test':NAME,'tpId':7,'hidden':False}], 'plotConfigs':COLS,'plotsUserEdited':True,
        'plotDensity':'quad','viewMode':'tp','xySource':'tp','xyXCols':[COLS[1]]*4,'xyYCols':COLS,
        'plotFilters':[{'kind':'moving_avg','winS':'.051'},{},{},{}], 'scatterCollapsed':True}
    page.add_init_script(f'''if(!sessionStorage.getItem('progress-seeded')){{
      localStorage.clear();sessionStorage.setItem('progress-seeded','true');
      localStorage.setItem('ptt.settings.v1',{json.dumps(json.dumps(settings))});
      localStorage.setItem('ptt.analysis-session.v1',{json.dumps(json.dumps(session))});}}''')
    page.add_init_script('''window.__exportYieldDelay=0;window.__heldEncodings=[];window.__holdEncoding=false;
      const original=HTMLCanvasElement.prototype.toBlob;
      HTMLCanvasElement.prototype.toBlob=function(callback,...args){
        return original.call(this,blob=>{if(window.__holdEncoding)window.__heldEncodings.push(()=>callback(blob));else callback(blob);},...args);};''')


def run(web, api, dataset, temporary, output):
    subprocess.run([str(ROOT/'backend/.venv/Scripts/python.exe'), '-c', FIXTURE], cwd=ROOT/'backend',
        env={**os.environ,'KIHA_DATA_DIR':str(dataset)}, check=True, capture_output=True)
    extension=temporary/'extension'; extension.mkdir()
    (extension/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'Export progress zoom','version':'1.0',
        'permissions':['tabs'],'background':{'service_worker':'background.js'}}))
    (extension/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    errors=[]; writes=[]; downloads=[]; started=[]; stages=[]; canceled=[]
    with sync_playwright() as playwright:
        request=playwright.request.new_context(base_url=api+'/')
        for col in COLS: assert request.get(f'tests/{NAME}/tp_stats',params={'col':col}).ok
        hashes=dataset_hashes(dataset)
        context=playwright.chromium.launch_persistent_context(str(temporary/'profile'),channel='chromium',headless=True,
            no_viewport=True,accept_downloads=True,args=[f'--disable-extensions-except={extension}',f'--load-extension={extension}','--window-size=1440,1000'])
        page=context.pages[0]
        page.on('download',lambda item:downloads.append(item.suggested_filename))
        page.on('pageerror',lambda error:errors.append(str(error)))
        def observe(req):
            if req.headers.get('x-export-id'): started.append((req.headers['x-export-id'],req.url))
            if req.url.startswith(api) and req.method not in ('GET','HEAD','OPTIONS') and not any(part in req.url for part in ('/export-progress','/plot-export','/plot-image-export','/spectrum-export','/xy-export')):
                writes.append((req.method,req.url))
        page.on('request',observe)
        def instrument(route):
            response=route.fetch(); body=response.text()
            assert 'window.setTimeout(resolve, 0)' in body
            route.fulfill(response=response,body=body.replace('window.setTimeout(resolve, 0)','window.setTimeout(resolve, window.__exportYieldDelay || 0)'))
        context.route('**/src/services/exportProgress.ts*',instrument)

        def settled(after_cancel=False):
            page.wait_for_load_state('domcontentloaded' if after_cancel else 'networkidle')
            for target in page.locator('[role=group][aria-label$=" plot"]').all(): target.locator('.uplot').wait_for()
            wait_for_chart_layout(page)
        def panel(multiple=False):
            button=multi.trigger(page) if multiple else page.get_by_role('button',name=re.compile(r'^Export .* plot$')).first
            button.focus();page.keyboard.press('Enter')
            modal=page.get_by_role('dialog',name='Export selected plots' if multiple else button.get_attribute('aria-label'),exact=True)
            expect(modal).to_be_visible();return modal
        def close(modal): page.keyboard.press('Escape');expect(modal).not_to_be_visible()
        def native_started(previous):
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                page.wait_for_timeout(50)
                if len(started)>previous:
                    identifier=started[-1][0]; value=request.get('export-progress/'+identifier).json();stages.append(value)
                    if value['state']=='running' and value['stage'] not in ('Waiting to start','Checking plots','Checking sources','Waiting for source'):
                        return identifier
            raise AssertionError(('No live native progress',stages[-5:]))
        def stopped(identifier):
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                value=request.get('export-progress/'+identifier).json()
                if value['state']=='canceled': canceled.append(identifier);return
                assert value['state'] not in ('completed','failed'),value
                page.wait_for_timeout(100)
            raise AssertionError(value)
        def state(modal,wanted): expect(modal.locator('[data-export-state]')).to_have_attribute('data-export-state',wanted,timeout=20000)
        try:
            seed(page);page.goto(web);settled()
            worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            modal=panel(True);before=len(downloads);prior=len(started)
            modal.get_by_role('button',name='Download CSV ZIP',exact=True).click()
            identifier=native_started(prior)
            expect(modal.get_by_role('progressbar',name='Export progress')).to_be_visible()
            modal.get_by_role('button',name='Cancel export',exact=True).focus();page.keyboard.press('Enter')
            state(modal,'canceled');stopped(identifier);assert len(downloads)==before
            expect(modal.get_by_role('button',name='Download CSV ZIP',exact=True)).to_be_focused()
            page.screenshot(path=str(output/'canceled.png'));close(modal)
            print('PASS: cancel real 2.8M-row multi-plot CSV work, server acknowledgement and no partial download',flush=True)

            modal=panel();modal.get_by_role('radio',name='Original',exact=True).check()
            path,_=single.download(page,modal.get_by_role('button',name='Download CSV',exact=True),output,'retry-native','zip')
            state(modal,'completed')
            with zipfile.ZipFile(path) as archive:
                manifest=json.loads(archive.read('analysis.json'));plot=manifest['plots'][0]
                content=archive.read(plot['file']);assert hashlib.sha256(content).hexdigest()==plot['sha256']
                reader=csv.DictReader(io.StringIO(content.decode()));first=next(reader);count=1;last=first
                for last in reader: count+=1
                assert count==ROWS==plot['rows'];assert first['sample_index']=='0' and last['sample_index']==str(ROWS-1)
            close(modal)
            print('PASS: retry completes a real 700K-row CSV/sidecar with correct rows and hash',flush=True)

            # Slow the actual response body, then cancel after receiving bytes.
            transfer=context.new_cdp_session(page);transfer.send('Network.enable')
            modal=panel();modal.get_by_role('radio',name='Original',exact=True).check();before=len(downloads)
            transfer.send('Network.emulateNetworkConditions',{'offline':False,'latency':0,'downloadThroughput':262144,'uploadThroughput':-1})
            try:
                modal.get_by_role('button',name='Download CSV',exact=True).click()
                expect(modal.get_by_role('status')).to_contain_text('Receiving file',timeout=20000)
                modal.get_by_role('button',name='Cancel export',exact=True).click();state(modal,'canceled')
                assert len(downloads)==before
            finally:
                transfer.send('Network.emulateNetworkConditions',{'offline':False,'latency':0,'downloadThroughput':-1,'uploadThroughput':-1})
                transfer.detach()
            close(modal)
            print('PASS: cancel during real throttled file transfer discards partial bytes',flush=True)

            for mode in ('Spectrum','XY'):
                page.get_by_role('group',name='Plot mode',exact=True).get_by_role('button',name=mode,exact=True).click();settled()
                modal=panel(True);prior=len(started);before=len(downloads)
                modal.get_by_role('button',name='Download CSV ZIP',exact=True).click();identifier=native_started(prior)
                modal.get_by_role('button',name='Cancel export',exact=True).click();state(modal,'canceled');stopped(identifier)
                assert len(downloads)==before;close(modal)
            print('PASS: real Spectrum and XY native exports also cancel',flush=True)

            # Close and page reload must signal cancellation even without waiting in the modal.
            for action in ('close','reload'):
                modal=panel(True);prior=len(started);before=len(downloads)
                modal.get_by_role('button',name='Download CSV ZIP',exact=True).click();identifier=native_started(prior)
                if action=='close': close(modal)
                else: page.reload()
                stopped(identifier);assert len(downloads)==before
                if action=='reload': settled(after_cancel=True)
            print('PASS: Close and reload stop server work and suppress delivery',flush=True)

            modal=panel();before=len(downloads);page.evaluate('window.__holdEncoding=true')
            modal.get_by_role('button',name='Download PNG',exact=True).click()
            page.wait_for_function('window.__heldEncodings.length>0')
            modal.get_by_role('button',name='Cancel export',exact=True).click();state(modal,'canceling')
            page.evaluate('window.__holdEncoding=false;window.__heldEncodings.splice(0).forEach(callback=>callback())')
            state(modal,'canceled');assert len(downloads)==before;close(modal)
            page.evaluate('window.__exportYieldDelay=150')
            modal=panel(True);before=len(downloads)
            modal.get_by_role('button',name='Download combined PNG',exact=True).click()
            expect(modal.get_by_role('status')).to_contain_text('Composing image',timeout=10000)
            page.wait_for_function('''()=>{const bar=document.querySelector('[data-export-state="running"] progress');
                return bar && bar.value>0 && bar.value<bar.max;}''')
            modal.get_by_role('button',name='Cancel export',exact=True).click();state(modal,'canceled')
            assert len(downloads)==before;page.evaluate('window.__exportYieldDelay=0');close(modal)
            print('PASS: cancel during native PNG encoding and between captured-image composition cells',flush=True)

            def fail(route):
                payload=route.request.post_data_json;payload['sources'][0]['test']='missing_fixture'
                response=route.fetch(post_data=json.dumps(payload));assert response.status==404
                route.fulfill(response=response)
            context.route('**/xy-export',fail)
            modal=panel();modal.get_by_role('button',name='Download CSV',exact=True).click();state(modal,'failed')
            expect(modal.get_by_role('alert')).to_contain_text('Export failed');context.unroute('**/xy-export',fail)
            single.download(page,modal.get_by_role('button',name='Download PNG',exact=True),output,'retry-image','zip');state(modal,'completed');close(modal)

            cdp=context.new_cdp_session(page);window=cdp.send('Browser.getWindowForTarget')['windowId'];baseline=page.evaluate('devicePixelRatio')
            for width,factor in ((1100,1),(1440,1.25),(1440,1.5)):
                cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'width':width,'height':1000}})
                set_browser_zoom(worker,page,factor);page.wait_for_function('value=>Math.abs(devicePixelRatio-value)<.001',arg=baseline*factor);settled()
                modal=panel();single.check_panel_layout(modal)
                page.evaluate('window.__holdEncoding=true');modal.get_by_role('button',name='Download PNG',exact=True).click()
                page.wait_for_function('window.__heldEncodings.length>0')
                button=modal.get_by_role('button',name='Cancel export',exact=True);button.scroll_into_view_if_needed();expect(button).to_be_in_viewport()
                capture_browser_view(cdp,output/f'progress-{width}-{factor}.png')
                button.focus();page.keyboard.press('Enter')
                page.evaluate('window.__holdEncoding=false;window.__heldEncodings.splice(0).forEach(callback=>callback())')
                state(modal,'canceled');close(modal)
                expect(page.get_by_role('button',name=re.compile(r'^Export .* plot$')).first).to_be_focused()
            assert not errors,errors;assert not writes,writes;assert dataset_hashes(dataset)==hashes
            (output/'results.json').write_text(json.dumps({'fixture_rows':ROWS,'native_canceled':canceled,'observed_progress':stages,
                'downloads':downloads,'unchanged_source_files':sorted(hashes),'browser_errors':errors,'unexpected_writes':writes},indent=2),encoding='utf-8')
            print('PASS: failed/completed/canceled outcomes, 1100px/125%/150%, keyboard Cancel/Escape/focus and unchanged source files',flush=True)
        except Exception:
            page.screenshot(path=str(output/'failure.png'),full_page=True)
            (output/'failure.json').write_text(json.dumps({'body':page.locator('body').inner_text(),'errors':errors,'stages':stages},indent=2),encoding='utf-8')
            raise
        finally: context.close();request.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--frontend-port',type=int,default=3150);parser.add_argument('--backend-port',type=int,default=8150)
    args=parser.parse_args();output=ROOT/'data/verification/export-progress';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kiha-export-progress-') as directory:
        temporary=Path(directory)
        with servers(temporary,output,args.frontend_port,args.backend_port) as (web,api,dataset):run(web,api,dataset,temporary,output)
    print('PASS: owned servers stopped and isolated fixture/profile removed',flush=True)


if __name__=='__main__':main()
