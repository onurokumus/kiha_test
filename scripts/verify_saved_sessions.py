"""Phase 8b real session downloads/reopen, ranges, compatibility and desktop checks.
Only the browser fixture instruments uPlot; native data uses the owned 3.13 server.
"""
import argparse
import json
import math
from pathlib import Path
import tempfile

from playwright.sync_api import expect, sync_playwright
from verify_data_quality import ROOT, servers, upload
from verify_session_recovery import hashes, KEY
from verify_time_y_zoom import instrument
from verify_browser_zoom import set_browser_zoom, capture_browser_view, wait_for_chart_layout


def run_checks(web, api, dataset, temporary, output):
    extension = temporary/'extension'; extension.mkdir()
    (extension/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'Session zoom','version':'1.0',
        'permissions':['tabs'],'background':{'service_worker':'background.js'}}))
    (extension/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as p:
        request = p.request.new_context(base_url=api+'/')
        csv = 'time,signal,force,rpm,rpm_aux\n' + ''.join(
            f'{i/100},{math.sin(i/7)},{2*math.cos(i/11)},{1800+i%21},{2100+i%13}\n' for i in range(1200))
        for name in ('alpha','beta'):
            assert upload(request,name,csv)['status']=='ready'
            assert request.put(f'tests/{name}/testpoints',data={'test':name,'test_points':[
                {'id':3,'name':'Run one','start_s':0,'end_s':5},{'id':4,'name':'Run two','start_s':5,'end_s':10}]}).ok
            for col in ('signal','force','rpm','rpm_aux'): assert request.get(f'tests/{name}/tp_stats?col={col}').ok
        catalog = request.get('analysis-sources').json()['sources']; before = hashes(dataset/'tests')
        context = p.chromium.launch_persistent_context(str(temporary/'profile'),channel='chromium',headless=True,
            no_viewport=True,accept_downloads=True,args=[f'--disable-extensions-except={extension}',f'--load-extension={extension}','--window-size=1440,1000'])
        page=context.pages[0]; errors=[]; downloads=[]; evidence=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('**/src/utils/uplotSync.ts*',instrument)
        # First selected source finishes last, including two distinct RPM choices.
        page.add_init_script('''const actualFetch=window.fetch.bind(window);window.fetch=async(...a)=>{
          if(String(a[0]).includes('/tests/alpha?'))await new Promise(r=>setTimeout(r,400));return actualFetch(...a);};''')
        worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp=context.new_cdp_session(page)
        def stored(): return page.evaluate('key=>JSON.parse(localStorage.getItem(key))',KEY)
        def settle():
            page.wait_for_timeout(350)
            page.wait_for_load_state('networkidle')
            if page.locator('.uplot').count(): wait_for_chart_layout(page)
            page.wait_for_timeout(350)
        def plots(): return page.locator('.analyze-plots-pane .uplot')
        def slot(index=0):
            target=page.locator('.analyze-plots-pane [role="group"][aria-label$="plot"]').nth(index).locator('.uplot')
            target.wait_for();return target
        def axes(index=0):
            return slot(index).evaluate('(el)=>{const u=el.__verificationPlot;return [u.scales.x.min,u.scales.x.max,u.scales.y.min,u.scales.y.max]}')
        def set_axes(index,x,y=None):
            slot(index).evaluate('(el,q)=>{const u=el.__verificationPlot;u.batch(()=>{u.setScale("x",{min:q.x[0],max:q.x[1]});if(q.y)u.setScale("y",{min:q.y[0],max:q.y[1]})})}',{'x':x,'y':y})
            page.wait_for_timeout(400)
        def modal(): return page.get_by_role('dialog',name='Analysis sessions',exact=True)
        def sessions(): page.get_by_role('button',name='Sessions',exact=True).click(); expect(modal()).to_be_visible()
        def save(label):
            sessions(); modal().get_by_role('textbox',name='Session name',exact=True).fill(label)
            with page.expect_download() as event: modal().get_by_role('button',name='Save session file',exact=True).click()
            download=event.value; target=output/download.suggested_filename; download.save_as(target)
            result=json.loads(target.read_text(encoding='utf-8')); downloads.append(download.suggested_filename)
            modal().get_by_role('button',name='Close',exact=True).click(); return result
        def choose(payload):
            modal().get_by_label('Session file',exact=True).set_input_files({'name':'test.ptt-session.json','mimeType':'application/json',
                'buffer':json.dumps(payload).encode() if not isinstance(payload,bytes) else payload})
        def preview(payload):
            sessions(); choose(payload); expect(modal().get_by_label('Session preview',exact=True)).to_be_visible()
            expect(modal().get_by_role('button',name='Refresh preview')).to_be_enabled()
        def reopen(payload):
            preview(payload); modal().get_by_role('button',name='Open session',exact=True).click(); expect(modal()).not_to_be_visible(); settle()
        def mode(name):
            page.get_by_role('group',name='Plot mode',exact=True).get_by_role('button',name=name,exact=True).click();settle()
        def assert_axes(expected,index=0):
            actual=axes(index)
            assert all(abs(a-b)<1e-7*max(1,abs(b)) for a,b in zip(actual,expected)), (actual,expected)
        try:
            page.goto(web); settle()
            base=page.evaluate('''async sources=>{const{defaultAnalysisSession}=await import('/src/services/analysisSession.ts');
              const s=defaultAnalysisSession();return {...s,sources,currentTest:'alpha',xAxis:'signal',yAxis:'force',axesUserSet:true,
                selections:[{test:'alpha',tpId:3,hidden:false,color:'#ac4dba'},{test:'beta',tpId:4,hidden:true,color:'#e3ac4f'}],
                plotConfigs:['signal','force','signal','rpm_aux','force','signal','force','signal','rpm'], plotsUserEdited:true,
                plotDensity:'quad',scatterCollapsed:true,clusteringEnabled:false,datasheetVisible:false,
                mainZoom:[-1,1,-2,2],filterState:{tpKeys:['alpha:3','beta:4'],labels:[],parameterFilters:[{id:'signal-mean',column:'signal',mode:'mean',min:-1,max:1}]},
                showHorizontalErrorBars:true,showVerticalErrorBars:true,annotationsVisible:false,specRpmCol:'rpm_aux',
                xyXCols:Array(9).fill('force'),xyYCols:Array(9).fill('signal'),
                plotFilters:s.plotFilters.map((f,i)=>({...f,kind:i===0?'moving_avg':'',winS:'0.03'})),plotShowOriginal:[true,false,false,false,false,false,false,false,false],
                timeZoom:[.5,3.5],timeYRanges:[{context:JSON.stringify(['signal',[JSON.stringify(['alpha',3,0,5])]]),range:[-.6,.8]}]};}''',catalog)
            page.evaluate('([key,value])=>localStorage.setItem(key,JSON.stringify(value))',[KEY,base]); page.reload();settle()
            time_file=save('Time comparison'); current=time_file['session']
            assert current['selections']==base['selections'] and current['specRpmCol']=='rpm_aux'
            for key in ('mainZoom','filterState','timeZoom','timeYRanges','plotFilters','plotShowOriginal','annotationsVisible','showHorizontalErrorBars','showVerticalErrorBars','clusteringEnabled','datasheetVisible'):
                assert current[key]==base[key] or (key=='timeYRanges' and current[key][0]==base[key][0]),key
            time_axes=axes(); mode('Spectrum'); reopen(time_file); assert_axes(time_axes)
            print('PASS: real Time file, colors/hidden/filter/overlay/notes/scatter options and Time ranges round-trip',flush=True)
            # Actual wheel -> two independent manual X ranges; survives all draw rebuilds.
            mode('Spectrum'); plots().first.locator('.u-over').hover(); page.mouse.wheel(0,-200);settle()
            assert stored()['plotViewports']['spectrum'][0]
            old_autosave=stored()
            page.evaluate('''key=>{window.restoreSessionStorage=Storage.prototype.setItem;
              Storage.prototype.setItem=function(k,v){if(k===key)throw Error('Injected storage quota');return window.restoreSessionStorage.call(this,k,v);};}''',KEY)
            set_axes(0,[2,18]);set_axes(1,[4,25]); first=axes();second=axes(1)
            spectrum_file=save('Spectrum comparison'); assert spectrum_file['session']['plotViewports']['spectrum'][1]['x']==[4,25]
            assert stored()==old_autosave, 'Fixture must block automatic storage; explicit file still captures current ranges'
            page.evaluate('()=>{Storage.prototype.setItem=window.restoreSessionStorage;delete window.restoreSessionStorage;}')
            page.get_by_role('button',name='Expand signal',exact=True).first.click();settle();assert_axes(first)
            expanded_file=save('Maximized spectrum'); assert expanded_file['session']['expandedPlot']==0
            page.get_by_role('button',name='Minimize signal',exact=True).click();settle();assert_axes(first);assert_axes(second,1)
            mode('XY');reopen(expanded_file);expect(plots()).to_have_count(1);assert_axes(first)
            page.get_by_role('button',name='Minimize signal',exact=True).click();settle()
            page.get_by_role('group',name='signal spectrum plot',exact=True).first.get_by_role('button',name='Reset axes',exact=True).click();settle()
            assert stored()['plotViewports']['spectrum'][0] is None
            reopen(spectrum_file);assert_axes(first);assert_axes(second,1)
            mode('XY');set_axes(0,[-1.1,.8],[-.5,.6]);set_axes(1,[-.9,1.5],[-.7,.9]);first_xy=axes();second_xy=axes(1)
            xy_file=save('XY comparison');mode('Spectrum');reopen(xy_file);assert_axes(first_xy);assert_axes(second_xy,1)
            page.reload();settle();assert_axes(first_xy);assert_axes(second_xy,1)
            plots().first.locator('.u-over').dblclick();settle();assert stored()['plotViewports']['xy'][0] is None
            reopen(xy_file)
            print('PASS: Spectrum/XY wheel, independent X/Y slots, file/reload, maximize/restore, mode changes and reset',flush=True)
            # Ninth slot and hidden layout slots retain independent viewports.
            page.get_by_role('group',name='Plot layout').get_by_role('button',name='9',exact=True).click();settle()
            set_axes(8,[-.4,.7],[-.3,.4]);ninth=axes(8);nine_file=save('Nine XY slots')
            page.get_by_role('group',name='Plot layout').get_by_role('button',name='4',exact=True).click();settle()
            reopen(nine_file);expect(plots()).to_have_count(9);assert_axes(ninth,8)
            reopen(spectrum_file)
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('welch')
            page.get_by_role('group',name='Spectrum x-axis').get_by_role('button',name='Per rev',exact=True).click();settle()
            set_axes(0,[.02,.7]);order=axes()
            page.get_by_role('button',name='Log scale',exact=True).click();settle();assert axes()[:2]==order[:2]
            order_file=save('Welch order log');mode('XY');reopen(order_file);assert axes()[:2]==order[:2]
            assert stored()['specLogY'] and stored()['specMode']=='welch' and stored()['specXAxis']=='per_rev'
            mode('Full test');plots().first.locator('.u-over').hover();page.mouse.wheel(0,-300);settle()
            full_range=stored()['fullRange'];assert full_range and full_range[0]>0
            full_file=save('Full time');full_axes=axes();mode('Spectrum');reopen(full_file);assert_axes(full_axes)
            mode('Spectrum');page.get_by_role('group',name='Data source').get_by_role('button',name='Full test',exact=True).click();settle()
            set_axes(0,[.03,.8]);full_spectrum=save('Full spectrum');mode('XY');reopen(full_spectrum)
            assert stored()['fullRange']==full_range and axes()[:2]==[.03,.8]
            mode('XY');page.get_by_role('group',name='Data source').get_by_role('button',name='Full test',exact=True).click();settle()
            set_axes(0,[-1,1],[-.5,.5]);full_xy=save('Full XY');mode('Spectrum');reopen(full_xy);assert_axes([-1,1,-.5,.5])
            reopen(xy_file)
            print('PASS: ninth/hidden layout slots, Welch/order/Log Y and exact Full time/Spectrum/XY ranges',flush=True)
            # File schema and stale/Close failure guards never replace the view.
            snapshot=stored(); sessions()
            page.evaluate('''()=>{const original=File.prototype.text;File.prototype.text=function(){File.prototype.text=original;return Promise.reject(Error('Injected file read failure'));};}''')
            choose(time_file);expect(modal().get_by_role('alert')).to_contain_text('file read failure');assert stored()==snapshot
            for invalid in (b'{bad',{}, {'format':'ptt-analysis-session','version':99},
                {**time_file,'session':{**time_file['session'],'selections':'bad'}},
                {**time_file,'session':{**time_file['session'],'timeZoom':[1,1]}},
                {**time_file,'session':{**time_file['session'],'plotFilters':[{'kind':'bogus'}]}}, b'x'*(2*1024*1024+1)):
                choose(invalid);expect(modal().get_by_role('alert')).to_be_visible(); assert stored()==snapshot
            modal().get_by_role('button',name='Close',exact=True).click()
            held=[]
            def hold(route): held.append((route,route.fetch()))
            page.route('**/api/analysis-sources',hold);sessions();choose(time_file)
            expect(modal().get_by_role('status')).to_contain_text('Checking');page.wait_for_timeout(400)
            modal().get_by_role('button',name='Close',exact=True).click()
            for route,response in held: route.fulfill(response=response)
            page.unroute('**/api/analysis-sources',hold);page.wait_for_timeout(500);assert stored()==snapshot
            def fail(route): route.fulfill(status=503,json={'detail':'Injected source check failure'})
            page.route('**/api/analysis-sources',fail);sessions();choose(time_file);expect(modal().get_by_role('alert')).to_contain_text('Injected')
            assert stored()==snapshot;page.unroute('**/api/analysis-sources',fail)
            modal().get_by_role('button',name='Refresh preview').click();expect(modal().get_by_role('button',name='Open session',exact=True)).to_be_enabled()
            modal().get_by_role('button',name='Close',exact=True).click();assert stored()==snapshot
            legacy={**time_file['session']};legacy.pop('sources');preview(legacy)
            expect(modal().get_by_role('button',name='Open with available sources')).to_be_disabled()
            modal().get_by_role('button',name='Reconnect legacy references by name').click();expect(modal().get_by_role('button',name='Open session',exact=True)).to_be_enabled()
            modal().get_by_role('button',name='Open session',exact=True).click();settle()
            print('PASS: malformed/version/size/range/filter rejection, legacy preview, network Retry and Close without replacing current view',flush=True)
            assert hashes(dataset/'tests')==before, 'Saving/reopening must not change source files'
            # Preview state is rechecked before apply: rename between preview and Open.
            preview(xy_file);assert request.post('tests/alpha/rename?new_name=renamed').ok
            modal().get_by_role('button',name='Open session',exact=True).click();expect(modal().get_by_role('status')).to_contain_text('Sources changed')
            expect(modal()).to_contain_text('same dataset after rename');modal().get_by_role('button',name='Open session',exact=True).click();settle()
            assert stored()['currentTest']=='renamed';assert_axes(first_xy)
            # Missing or same-name replacement references cannot be silently linked.
            entry=request.delete('tests/renamed').json()['trash_id'];assert upload(request,'alpha',csv)['status']=='ready'
            preview(xy_file);expect(modal()).to_contain_text('saved identity does not match');modal().get_by_role('button',name='Close',exact=True).click()
            assert request.post(f'trash/{entry}/restore',data={'name':'restored'}).ok
            reopen(xy_file);assert stored()['currentTest']=='restored';assert_axes(first_xy)
            # Genuine changed TP bounds are skipped; changed sample/schema revisions reset viewports.
            assert request.put('tests/restored/testpoints',data={'test':'restored','test_points':[{'id':3,'name':'Changed','start_s':1,'end_s':4}]}).ok
            preview(xy_file);expect(modal()).to_contain_text('saved interval changed')
            modal().get_by_role('button',name='Open with available sources').click();expect(modal()).not_to_be_visible();settle()
            assert all(s['test']!='restored' for s in stored()['selections']) and not any(stored()['plotViewports']['xy'])
            assert request.post('tests/restored/edit',data={'rename':{'signal':'signal_new'}}).ok
            for col in ('signal_new','force','rpm','rpm_aux'):assert request.get(f'tests/restored/tp_stats?col={col}').ok
            preview(full_xy);expect(modal()).to_contain_text('sample data or variables changed')
            modal().get_by_role('button',name='Open with available sources').click();expect(modal()).not_to_be_visible();settle()
            assert stored()['fullRange'] is None and not any(stored()['plotViewports']['xy'])
            expect(page.get_by_role('region',name='Unavailable plot 1')).to_contain_text('signal')
            # Reuse beta as the working view for desktop/draft checks.
            stable={**xy_file,'session':{**xy_file['session'],'currentTest':'beta','selections':[{'test':'beta','tpId':4,'hidden':False,'color':'#e3ac4f'}],
                'filterState':{'tpKeys':['beta:4'],'labels':[],'parameterFilters':[]}}}
            reopen(stable)
            page.get_by_role('button',name='Edit',exact=True).click(); notes=page.get_by_role('textbox',name='Findings / notes',exact=True);notes.fill('Unsaved session guard')
            page.get_by_role('button',name='Sessions',exact=True).click();confirm=page.get_by_role('alertdialog');expect(confirm).to_contain_text('unsaved')
            confirm.get_by_role('button',name='Cancel',exact=True).click();expect(notes).to_have_value('Unsaved session guard');expect(modal()).not_to_be_visible()
            page.get_by_role('button',name='Sessions',exact=True).click();page.get_by_role('alertdialog').get_by_role('button',name='Discard draft',exact=True).click();expect(modal()).to_be_visible()
            modal().get_by_role('button',name='Close',exact=True).click();settle()
            set_axes(0,[-.9,.7],[-.4,.8]);desktop_axes=axes()
            window=cdp.send('Browser.getWindowForTarget')['windowId']
            for width,factor in ((1100,1),(1440,1.25),(1440,1.5)):
                cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'width':width,'height':1000}});set_browser_zoom(worker,page,factor);settle()
                assert_axes(desktop_axes)
                page.get_by_role('button',name='Expand signal versus force',exact=True).first.click();settle();assert_axes(desktop_axes)
                page.get_by_role('button',name='Minimize signal versus force',exact=True).click();settle();assert_axes(desktop_axes)
                sessions();button=modal().get_by_role('button',name='Save session file');button.focus();expect(button).to_be_focused()
                with page.expect_download() as event: page.keyboard.press('Enter')
                downloads.append(event.value.suggested_filename)
                box=modal().bounding_box();vp=page.evaluate('({w:innerWidth,h:innerHeight})');assert box['width']<=vp['w'] and box['height']<=vp['h']
                capture_browser_view(cdp,output/f'sessions-{width}-{factor}.png')
                page.keyboard.press('Escape');expect(modal()).not_to_be_visible();expect(page.get_by_role('button',name='Sessions',exact=True)).to_be_focused()
                evidence.append({'width':width,'zoom':factor})
            beta_before={k:v for k,v in before.items() if k.startswith('beta/')}
            assert beta_before=={k:v for k,v in hashes(dataset/'tests').items() if k.startswith('beta/')}
            assert not errors,errors
            (output/'results.json').write_text(json.dumps({'downloads':downloads,'desktop':evidence,'unchanged_files_before_lifecycle':len(before),'unchanged_beta_files':len(beta_before),'page_errors':errors},indent=2))
            print('PASS: fresh compatibility checks, rename/trash/restore/changed TP, draft guard, keyboard and 1100px/125%/150%',flush=True)
        except Exception:
            page.screenshot(path=str(output/'failure.png'),full_page=True);print(page.locator('body').aria_snapshot()[-18000:],flush=True);raise
        finally:context.close();request.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--frontend-port',type=int,default=3240);parser.add_argument('--backend-port',type=int,default=8240);args=parser.parse_args()
    output=ROOT/'data/verification/saved-sessions';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-saved-sessions-') as folder:
        temporary=Path(folder)
        with servers(temporary,output,args.frontend_port,args.backend_port) as (web,api,dataset):run_checks(web,api,dataset,temporary,output)
    print('PASS: isolated fixture cleanup and owned servers stopped',flush=True)


if __name__=='__main__':main()
