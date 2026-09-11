"""Real waterfall FFT UI/export checks with isolated Python 3.13 data and Chromium."""
import csv
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import zipfile

from playwright.sync_api import sync_playwright, expect
from verify_data_quality import ROOT, servers, upload
from verify_filter_overlay import dataset_hashes
from verify_browser_zoom import set_browser_zoom, capture_browser_view, wait_for_chart_layout


def run(web, api, dataset, temporary, output):
    extension=temporary/'extension'; extension.mkdir()
    (extension/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'Waterfall test zoom','version':'1.0',
        'permissions':['tabs'],'background':{'service_worker':'background.js'}}))
    (extension/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as p:
        request=p.request.new_context(base_url=api+'/')
        for name, amplitude in [('waterfall_A',3),('waterfall_B',1)]:
            lines=['time,signal_N,reference_V']
            for i in range(16384):
                t=i/512; chirp=math.sin(2*math.pi*(15*t+1.2*t*t))
                lines.append(f'{100+t:.12f},{amplitude*chirp+0.3*math.sin(2*math.pi*120*t):.15g},{1+math.cos(t):.15g}')
            assert upload(request,name,'\n'.join(lines))['status']=='ready'
            points=[{'id':7,'name':'Sweep','start_s':0,'end_s':32,'start_idx':0,'end_idx':16384}]
            assert request.put(f'tests/{name}/testpoints',data={'test':name,'version':1,'test_points':points}).ok
            for col in ('signal_N','reference_V'):
                assert request.get(f'tests/{name}/tp_stats',params={'col':col}).ok
        sources=request.get('analysis-sources').json()['sources']; before=dataset_hashes(dataset)
        context=p.chromium.launch_persistent_context(str(temporary/'profile'),channel='chromium',headless=True,no_viewport=True,
            args=[f'--disable-extensions-except={extension}',f'--load-extension={extension}','--window-size=1600,1100'])
        page=context.pages[0]; errors=[]; posts=[]; held=[]; state={'hold':False,'fail':False}
        page.on('pageerror',lambda e:errors.append(str(e)))
        def instrument(route):
            response=route.fetch(); text=response.text()
            needle='plot.current = u;'
            assert needle in text
            route.fulfill(response=response,body=text.replace(needle,needle+' u.root.__waterfall = u;'))
        context.route('**/src/components/plots/WaterfallPlot.tsx*',instrument)
        def waterfall_route(route):
            if state['fail'] and 'waterfall_B/' in route.request.url:
                route.fulfill(status=503,json={'detail':'Isolated source failure'}); return
            if state['hold']:
                held.append((route,route.fetch())); return
            route.continue_()
        context.route('**/waterfall?*',waterfall_route)
        def exported(route):
            posts.append(route.request.post_data_json); route.continue_()
        context.route('**/waterfall-export*',exported)
        session={'version':1,'sources':sources,'currentTest':'waterfall_A','xAxis':'signal_N','yAxis':'reference_V','axesUserSet':True,
            'selections':[{'test':name,'tpId':7,'hidden':False} for name in ('waterfall_A','waterfall_B')],
            'plotConfigs':['signal_N','reference_V'],'plotsUserEdited':True,'plotDensity':'single',
            'viewMode':'spectrum','specMode':'waterfall','specSource':'tp','specLogY':False,'scatterCollapsed':True,
            'waterfallWindow':512,'waterfallOverlap':50}
        page.add_init_script(f'''if(!sessionStorage.getItem('seeded')){{localStorage.clear();sessionStorage.setItem('seeded','1');
          localStorage.setItem('ptt.analysis-session.v1',{json.dumps(json.dumps(session))});}}''')
        def plot(): return page.get_by_role('group',name='signal_N waterfall plot',exact=True).first
        def settled(count=2):
            page.wait_for_load_state('networkidle'); expect(plot().locator('.uplot')).to_have_count(count)
            expect(plot()).not_to_contain_text('Calculating waterfall FFT'); wait_for_chart_layout(page)
        def scales():
            return plot().locator('.uplot').evaluate_all('els=>els.map(el=>({x:[el.__waterfall.scales.x.min,el.__waterfall.scales.x.max],y:[el.__waterfall.scales.y.min,el.__waterfall.scales.y.max]}))')
        def menu(action):
            plot().get_by_role('button',name='Plot actions for signal_N',exact=True).click()
            page.get_by_role('menuitem',name=action,exact=True).click()
        def export(fmt, tag):
            menu('Export CSV / PNG…'); panel=page.get_by_role('dialog',name='Export signal_N plot',exact=True)
            with page.expect_download(timeout=60000) as download:
                panel.get_by_role('button',name=f'Download {fmt}',exact=True).click()
            path=output/f'{tag}.zip'; download.value.save_as(path)
            with zipfile.ZipFile(path) as archive:
                assert 'analysis.json' in archive.namelist()
                metadata=json.loads(archive.read('analysis.json'))
                assert 'waterfall' in json.dumps(metadata)
                if fmt=='CSV':
                    rows=list(csv.DictReader(io.StringIO(archive.read(next(n for n in archive.namelist() if n.endswith('.csv'))).decode())))
                    assert {r['source_test'] for r in rows}=={'waterfall_A','waterfall_B'}
                    assert max(float(r['magnitude_U']) for r in rows)>2.5
                else:
                    entry=next(n for n in archive.namelist() if n.endswith('.png'))
                    (output/f'{tag}.png').write_bytes(archive.read(entry))
            page.keyboard.press('Escape');expect(panel).not_to_be_visible()
        try:
            worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            page.goto(web); settled()
            print('PASS: two native sources loaded',flush=True)
            assert scales()[0]==scales()[1]
            plot().locator('.u-over').first.hover(position={'x':110,'y':90})
            expect(plot()).to_contain_text('source frame centers')
            plot().screenshot(path=str(output/'two-sources.png'))
            initial=scales(); box=plot().locator('.u-over').first.bounding_box()
            page.mouse.move(box['x']+box['width']*.2,box['y']+box['height']*.2)
            page.mouse.down();page.mouse.move(box['x']+box['width']*.8,box['y']+box['height']*.8,steps=12);page.mouse.up()
            page.wait_for_timeout(350)
            zoomed=scales(); assert zoomed[0]==zoomed[1] and zoomed[0]['x']!=initial[0]['x'] and zoomed[0]['y']!=initial[0]['y'],(zoomed,initial)
            plot().get_by_role('button',name='Expand signal_N',exact=True).click(); settled()
            assert scales()==zoomed
            page.wait_for_timeout(400);page.reload();settled();assert scales()==zoomed
            menu('Reset axes');page.wait_for_timeout(350);assert scales()==initial
            plot().locator('.u-over').first.hover();page.wait_for_timeout(100);page.mouse.wheel(0,-300);page.wait_for_timeout(400)
            wheel=scales(); assert wheel[0]==wheel[1] and wheel!=initial, (wheel, initial, page.evaluate("JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).plotViewports"))
            box=plot().locator('.u-over').first.bounding_box()
            page.keyboard.down('Shift');page.mouse.move(box['x']+box['width']*.5,box['y']+100);page.mouse.down();page.mouse.move(box['x']+box['width']*.65,box['y']+100,steps=8);page.mouse.up();page.keyboard.up('Shift')
            page.wait_for_timeout(300);assert scales()!=wheel
            plot().get_by_label('Waterfall canvas for Sweep · waterfall_A · TP 7',exact=True).focus();page.keyboard.press('Home');page.wait_for_timeout(300)
            assert scales()==initial
            print('PASS: linked two-axis zoom, pan, keyboard reset, maximize and reload persistence',flush=True)
            export('CSV','grid'); export('PNG','comparison')
            menu('Analysis details…'); details=page.get_by_role('dialog',name='Waterfall analysis for signal_N')
            expect(details).to_contain_text('hann_periodic'); page.keyboard.press('Escape')
            page.get_by_role('button',name='Log color',exact=True).click();settled()
            export('PNG','comparison-log')
            print('PASS: real CSV/PNG metadata packages and log color',flush=True)
            page.get_by_role('combobox',name='Window (samples)',exact=True).select_option('256');settled()
            page.get_by_role('combobox',name='Overlap',exact=True).select_option('75');settled()
            page.wait_for_timeout(400); page.reload();settled()
            expect(page.get_by_role('combobox',name='Window (samples)',exact=True)).to_have_value('256')
            expect(page.get_by_role('combobox',name='Overlap',exact=True)).to_have_value('75')
            state['fail']=True;page.get_by_role('combobox',name='Overlap',exact=True).select_option('50');page.wait_for_load_state('networkidle')
            expect(plot()).to_contain_text('Isolated source failure')
            menu('Export CSV / PNG…');panel=page.get_by_role('dialog',name='Export signal_N plot');expect(panel.get_by_role('button',name='Download CSV',exact=True)).to_be_disabled();page.keyboard.press('Escape');expect(panel).not_to_be_visible()
            state['fail']=False;plot().get_by_role('button',name='Retry waterfall').click();settled()
            state['hold']=True;page.get_by_role('combobox',name='Overlap',exact=True).select_option('25')
            while not held: page.wait_for_timeout(50)
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('fft')
            state['hold']=False
            for route,response in held:
                try:route.fulfill(response=response)
                except Exception:pass
            held.clear();page.wait_for_load_state('networkidle')
            expect(page.get_by_role('group',name='signal_N spectrum plot',exact=True).first.locator('.uplot')).to_have_count(1)
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('welch');page.wait_for_load_state('networkidle')
            page.get_by_role('combobox',name='Estimator',exact=True).select_option('waterfall');settled(1)
            expect(page.get_by_role('group',name='Data source',exact=True).get_by_role('button',name='Full test',exact=True)).to_have_attribute('aria-pressed','true')
            plot().screenshot(path=str(output/'single-test-default.png'))
            page.get_by_role('group',name='Data source',exact=True).get_by_role('button',name='Selected TPs',exact=True).click();settled()
            print('PASS: entering Waterfall defaults to one active-test map; comparison requires explicit Selected TPs',flush=True)
            print('PASS: settings persistence, failure/Retry, late-response guard and FFT/Welch compatibility',flush=True)
            plot().get_by_role('button',name='Minimize signal_N',exact=True).click();settled()
            page.wait_for_timeout(350)
            page.evaluate('''()=>{const s=JSON.parse(localStorage.getItem('ptt.analysis-session.v1'));
              s.plotDensity='nine';s.plotConfigs=Array.from({length:9},(_,i)=>i%2?'reference_V':'signal_N');
              localStorage.setItem('ptt.analysis-session.v1',JSON.stringify(s));}''')
            page.reload();settled();expect(page.locator('[aria-label$="waterfall plot"]')).to_have_count(9)
            page.get_by_role('button',name='Export selected plots',exact=True).click()
            panel=page.get_by_role('dialog',name='Export selected plots',exact=True)
            expect(panel.locator('input[aria-label^="Select plot "]')).to_have_count(9)
            with page.expect_download(timeout=60000) as download:panel.get_by_role('button',name='Download CSV ZIP',exact=True).click()
            archive_path=output/'nine-plots.zip';download.value.save_as(archive_path)
            with zipfile.ZipFile(archive_path) as archive:assert sum(n.endswith('.csv') for n in archive.namelist())==9
            panel.get_by_role('button',name='Clear selection',exact=True).click()
            panel.get_by_label('Select plot 1: signal_N',exact=True).check();panel.get_by_label('Select plot 2: reference_V',exact=True).check()
            panel.get_by_label('2 × 2',exact=True).check()
            with page.expect_download(timeout=60000) as download:panel.get_by_role('button',name='Download combined PNG',exact=True).click()
            archive_path=output/'two-plots.zip';download.value.save_as(archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                image=next(n for n in archive.namelist() if n.endswith('.png'));(output/'two-plots.png').write_bytes(archive.read(image))
            panel.get_by_role('button',name='Close',exact=True).click()
            page.wait_for_timeout(350)
            page.evaluate('''()=>{const s=JSON.parse(localStorage.getItem('ptt.analysis-session.v1'));
              s.plotDensity='single';s.specSource='full';s.fullRange=[2,10];
              localStorage.setItem('ptt.analysis-session.v1',JSON.stringify(s));}''')
            page.reload();settled(1)
            menu('Export CSV / PNG…');panel=page.get_by_role('dialog',name='Export signal_N plot',exact=True)
            with page.expect_download(timeout=60000) as download:panel.get_by_role('button',name='Download CSV',exact=True).click()
            download.value.save_as(output/'full-interval.zip')
            assert posts[-1]['sources'][0]['t0']==2 and posts[-1]['sources'][0]['t1']==10
            assert posts[-1]['sources'][0]['expected_i0']==1024
            page.keyboard.press('Escape');expect(panel).not_to_be_visible()
            page.get_by_role('group',name='Data source',exact=True).get_by_role('button',name='Selected TPs',exact=True).click();settled()
            plot().get_by_role('button',name='Expand signal_N',exact=True).click();settled()
            print('PASS: nine-slot CSV ZIP, selected2x2 PNG, Full-test interval parity',flush=True)
            cdp=context.new_cdp_session(page); window=cdp.send('Browser.getWindowForTarget')['windowId']
            for width,zoom in [(1100,1),(1600,1.25),(1600,1.5)]:
                cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'width':width,'height':1100}})
                set_browser_zoom(worker,page,zoom);settled()
                assert plot().evaluate('el=>el.scrollWidth<=el.clientWidth+1')
                for chart in plot().locator('.uplot').all():
                    assert chart.evaluate('el=>el.clientWidth>180&&el.clientHeight>100')
                capture_browser_view(cdp,output/f'desktop-{width}-{zoom}.png')
            assert not errors,errors
            assert dataset_hashes(dataset)==before,'Source samples changed'
            (output/'results.json').write_text(json.dumps({'checks':'two-source values, interaction, export, settings, failures, desktop zoom',
                'posts':posts,'source_files_unchanged':len(before),'browser_errors':errors},indent=2))
            print('PASS: desktop resize, actual 125%/150% zoom, unchanged data, no browser errors',flush=True)
        except Exception:
            page.screenshot(path=str(output/'failure.png'),full_page=True)
            print(page.locator('body').inner_text()[-6500:],flush=True)
            print(errors,flush=True)
            raise
        finally:
            context.close();request.dispose()


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    output=ROOT/'data/verification/waterfall';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kiha-waterfall-') as directory:
        temporary=Path(directory)
        with servers(temporary,output,3350,8350) as (web,api,dataset):run(web,api,dataset,temporary,output)
