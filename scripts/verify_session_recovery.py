"""Phase 8a isolated real source/session lifecycle and desktop browser checks."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from playwright.sync_api import expect, sync_playwright
from verify_data_quality import ROOT, servers, upload
from verify_browser_zoom import capture_browser_view, set_browser_zoom

KEY = 'ptt.analysis-session.v1'


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and p.name != 'tp_stats.json'
            and not p.name.startswith('.tp_stats.json.')}


def run_checks(web, api, dataset, temporary, output):
    extension = temporary / 'extension'; extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Recovery zoom',
        'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as p:
        request = p.request.new_context(base_url=api + '/')
        csv = 'time,signal,force,rpm\n' + ''.join(f'{i/10},{i},{i*2},{1800+i}\n' for i in range(100))
        for name in ('alpha', 'beta'):
            assert upload(request, name, csv)['status'] == 'ready'
            assert request.put(f'tests/{name}/testpoints', data={'test': name, 'test_points': [
                {'id': 3, 'name': 'First run', 'start_s': 0, 'end_s': 4},
                {'id': 4, 'name': 'Second run', 'start_s': 4, 'end_s': 8}]}).ok
            for column in ('signal','force','rpm'): assert request.get(f'tests/{name}/tp_stats?col={column}').ok
        catalog = request.get('analysis-sources').json()['sources']
        original = hashes(dataset / 'tests')
        context = p.chromium.launch_persistent_context(str(temporary / 'profile'), channel='chromium', headless=True,
            no_viewport=True, args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]; errors, console = [], []
        # Deliberately load the first selected source last. Preserve both saved
        # selection order and slot settings across partial metadata universes.
        page.add_init_script('''const originalFetch=window.fetch.bind(window);
          window.fetch=async (...args)=>{
            if(String(args[0]).includes('/tests/alpha?')) await new Promise(r=>setTimeout(r,400));
            return originalFetch(...args);
          };''')
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('console', lambda e: console.append(e.text) if e.type == 'error' else None)
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp = context.new_cdp_session(page)
        def stored(): return page.evaluate('key => JSON.parse(localStorage.getItem(key))', KEY)
        def seed(session):
            page.evaluate('({key, session}) => localStorage.setItem(key, JSON.stringify(session))', {'key': KEY, 'session': session})
            page.reload(); page.wait_for_load_state('networkidle')
        def recovery(): return page.get_by_role('complementary', name='Session recovery')
        def trace_ready(): expect(page.locator('.u-over').first).to_be_visible(timeout=20000)
        try:
            # The upload count is independent of the identity catalog. Slow or
            # failed source checks may pause analysis but must not invent zero
            # ready tests, load unverified saved names, or discard autosave.
            held_catalog, metadata_requests = [], []
            page.on('request', lambda req: metadata_requests.append(req.url)
                    if '/api/tests/' in req.url else None)
            page.route(api + '/analysis-sources', lambda route: held_catalog.append(route))
            page.goto(web)
            expect(page.locator('.app-header-summary')).to_have_text('2 ready tests')
            assert held_catalog and not metadata_requests
            for route in held_catalog:
                route.fulfill(status=500, json={'detail': 'Injected identity outage'})
            page.wait_for_load_state('networkidle')
            expect(page.get_by_role('heading', name='Unable to verify analysis sources')).to_be_visible()
            expect(page.locator('.app-header-summary')).to_have_text('2 ready tests')
            assert stored() is None and not metadata_requests
            page.screenshot(path=str(output/'catalog-unavailable.png'), full_page=True)
            page.get_by_role('button', name='Uploads', exact=True).click()
            expect(page.get_by_role('heading', name='Test uploads', exact=True)).to_be_visible()
            expect(page.locator('.app-header-summary')).to_have_text('2 ready tests')
            expect(page.get_by_role('button', name='Retry source check')).to_be_visible()
            assert stored() is None and not metadata_requests
            page.unroute(api + '/analysis-sources')
            page.get_by_role('button', name='Retry source check').click()
            expect(page.locator('.app-connection-banner')).to_have_count(0)
            page.get_by_role('button', name='Analyze', exact=True).click()
            expect(page.get_by_role('region', name='Analysis controls', exact=True)).to_be_visible()
            page.wait_for_function('key => JSON.parse(localStorage.getItem(key))?.sources?.some(s => s.id)', arg=KEY)

            # Distinguish a missing list from a successful empty list, even
            # when source verification also fails; retry restores both.
            page.route(api + '/tests', lambda route: route.fulfill(status=503, json={'detail': 'Injected list outage'}))
            page.route(api + '/analysis-sources', lambda route: route.fulfill(status=500, json={'detail': 'Injected identity outage'}))
            snapshot = stored()
            page.reload(); page.wait_for_load_state('networkidle')
            expect(page.locator('.app-header-summary')).to_have_text('Test count unavailable')
            expect(page.get_by_role('heading', name='Unable to load test data')).to_be_visible()
            assert stored() == snapshot
            page.unroute(api + '/tests'); page.unroute(api + '/analysis-sources')
            page.get_by_role('button', name='Try again', exact=True).click()
            expect(page.get_by_role('region', name='Analysis controls', exact=True)).to_be_visible()
            expect(page.locator('.app-header-summary')).to_have_text('2 ready tests')
            print('PASS: slow/500 source catalog retains initial ready count, Uploads and retry; list outage shows unavailable count', flush=True)
            # Pure resolver cases run against the same served TS module as App.
            pure = page.evaluate('''async () => {
              const {defaultAnalysisSession, normalizeAnalysisSession} = await import('/src/services/analysisSession.ts');
              const {resolveSessionSources} = await import('/src/services/sessionSources.ts');
              const a = {name:'a', id:'uuid-a', revision:'r1', status:'ready', columns:['x','y'], test_points:[{id:3,revision:'tp1'}]};
              const base = {...defaultAnalysisSession(), currentTest:'a', selections:[{test:'a',tpId:3,hidden:true}],
                sources:[a], plotConfigs:['x','y'], fullRange:[1,3], timeYRanges:[{context:JSON.stringify(['x',[JSON.stringify(['a',3,0,4])]]),range:[0,8]}],
                filterState:{tpKeys:['a:3'], labels:['run'], parameterFilters:[]}};
              const cases=[];
              const check=(name, ok)=>{if(!ok) throw Error(name); cases.push(name)};
              let r=resolveSessionSources(base,[a]); check('same identity retains settings',r.session.currentTest==='a'&&r.session.selections[0].hidden&&r.session.fullRange[0]===1);
              r=resolveSessionSources(base,[{...a,name:'renamed'}]); check('rename remaps all references and Y context',r.session.currentTest==='renamed'&&r.session.filterState.tpKeys[0]==='renamed:3'&&r.session.timeYRanges[0].context.includes('renamed'));
              r=resolveSessionSources(base,[{...a,id:'replacement'}]); check('same-name replacement fails closed',!r.session.currentTest&&!r.session.selections.length&&r.needsReview);
              r=resolveSessionSources(base,[a,{...a,name:'copy'}]); check('duplicate identity fails closed',!r.session.selections.length);
              r=resolveSessionSources(base,[]); check('missing source remains reviewable',r.needsReview&&!r.session.fullRange);
              r=resolveSessionSources(base,[{...a,status:'rebuilding',test_points:undefined}]); check('busy identity remains pending',r.session.selections.length===1);
              r=resolveSessionSources(base,[{...a,revision:'r2'}]); check('edited data resets axes retains source',r.session.currentTest==='a'&&!r.session.fullRange&&r.session.selections.length===1);
              r=resolveSessionSources(base,[{...a,test_points:[{id:3,revision:'tp2'}]}]); check('changed TP interval skipped',r.needsReview&&!r.session.selections.length&&!r.session.timeYRanges.length);
              r=resolveSessionSources({...base,sources:undefined},[a]); check('legacy pauses',r.legacy&&!r.session.currentTest);
              r=resolveSessionSources({...base,sources:undefined},[a],true); check('explicit legacy reconnect',!r.legacy&&r.session.selections.length===1);
              r=resolveSessionSources(base,[{...a,columns:['y']}]); check('missing variable stays in its slot',r.session.plotConfigs.join(',')==='x,y'&&r.messages.some(m=>m.includes('Plot 1')));
              r=resolveSessionSources(normalizeAnalysisSession({...base,sources:[{name:'a',id:35}]}),[a]); check('malformed modern ID is not legacy',!r.legacy&&!r.session.selections.length);
              return cases;
            }''')
            session = page.evaluate('''async sources => {
              const {defaultAnalysisSession} = await import('/src/services/analysisSession.ts');
              return {...defaultAnalysisSession(), sources, currentTest:'alpha', xAxis:'signal',yAxis:'force',axesUserSet:true,
                selections:[{test:'alpha',tpId:3,hidden:false},{test:'beta',tpId:4,hidden:true}],
                plotConfigs:['signal','force','rpm'],plotsUserEdited:true,plotDensity:'quad',
                plotFilters:Array.from({length:9},(_,i)=>({...defaultAnalysisSession().plotFilters[i],kind:i===0?'moving_avg':'',winS:'0.3'})),
                plotShowOriginal:[true,false,false,false,false,false,false,false,false],annotationsVisible:false,
                timeZoom:[0.4,2.4],timeYRanges:[{context:JSON.stringify(['signal',[JSON.stringify(['alpha',3,0,4])]]),range:[2,25]}],
                filterState:{tpKeys:['alpha:3','beta:4'],labels:[],parameterFilters:[]}};
            }''', catalog)
            seed(session); trace_ready()
            page.wait_for_function('key => JSON.parse(localStorage.getItem(key)).selections.length===2', arg=KEY)
            current = stored(); assert current['plotConfigs'][:3] == session['plotConfigs']
            assert current['plotFilters'][0]['kind'] == 'moving_avg' and current['plotShowOriginal'][0]
            assert current['annotationsVisible'] is False and current['timeYRanges'][0]['range'] == [2,25]
            assert not recovery().count()
            # Force the autosave debounce to complete after hydration.
            page.wait_for_function('key => JSON.parse(localStorage.getItem(key)).sources.every(s => s.id)', arg=KEY)
            assert hashes(dataset / 'tests') == original
            assert current['selections'][0]['test'] == 'alpha'
            status_path = dataset/'tests/alpha/status.json'
            status_before = status_path.read_bytes()
            status_path.write_text(json.dumps({'status':'rebuilding'}))
            seed(session)
            page.wait_for_function('key => JSON.parse(localStorage.getItem(key)).sources[0].status === undefined', arg=KEY)
            assert stored()['sources'][0]['revision'] == session['sources'][0]['revision']
            assert stored()['sources'][0]['test_points'] == session['sources'][0]['test_points']
            status_path.write_bytes(status_before)
            page.reload(); page.wait_for_load_state('networkidle'); trace_ready()
            assert stored()['selections'][0]['test'] == 'alpha'
            print('PASS: 12 pure resolver cases, delayed metadata order, busy/reload references and live selections/hidden/filter/overlay/annotation/axes recovery', flush=True)

            assert request.post('tests/alpha/rename?new_name=renamed').ok
            seed(session); trace_ready()
            expect(recovery()).to_contain_text('same dataset after rename or restore')
            page.wait_for_function('key => JSON.parse(localStorage.getItem(key)).currentTest === "renamed"', arg=KEY)
            assert stored()['filterState']['tpKeys'] == ['renamed:3','beta:4']
            assert 'renamed' in stored()['timeYRanges'][0]['context']
            entry = request.delete('tests/renamed').json()['trash_id']
            assert upload(request, 'alpha', csv)['status'] == 'ready'
            replacement = next(s for s in request.get('analysis-sources').json()['sources'] if s['name']=='alpha')
            assert replacement['id'] != catalog[0]['id']
            seed(session)
            expect(recovery()).to_contain_text('saved identity does not match')
            assert stored() == session, 'Skipped source references must not overwrite original recovery state'
            expect(page.get_by_role('combobox', name='Choose active test after recovery')).to_be_visible()
            # Trash restore under another name recovers the original after retry.
            assert request.post(f'trash/{entry}/restore', data={'name':'restored'}).ok
            recovery().get_by_role('button', name='Retry session recovery').click(); trace_ready()
            page.wait_for_function('key => JSON.parse(localStorage.getItem(key)).currentTest === "restored"', arg=KEY)
            assert stored()['selections'][0]['test'] == 'restored'
            print('PASS: real rename, trash, same-name replacement blocked without losing saved references, restore under another name and Retry', flush=True)

            # Changed TP IDs mean a different interval and must not reselect it.
            stable = stored()
            assert request.put('tests/restored/testpoints', data={'test':'restored','test_points':[
                {'id':3,'name':'Now different','start_s':1,'end_s':3}]}).ok
            seed(stable); expect(recovery()).to_contain_text('saved interval changed')
            assert stored() == stable
            page.get_by_role('button',name='Uploads',exact=True).click()
            page.get_by_role('button',name='Edit notes for beta',exact=True).click()
            notes = page.get_by_role('textbox',name='Findings / notes',exact=True)
            notes.fill('Unsaved note must survive canceled recovery')
            recovery().get_by_role('button',name='Retry session recovery').click()
            expect(page.get_by_role('alertdialog')).to_contain_text('Discard unsaved data-edit changes?')
            page.keyboard.press('Escape'); expect(notes).to_have_value('Unsaved note must survive canceled recovery')
            recovery().get_by_role('button',name='Retry session recovery').click()
            page.get_by_role('alertdialog').get_by_role('button',name='Discard draft',exact=True).click()
            expect(recovery()).to_contain_text('saved interval changed')
            recovery().get_by_role('button', name='Continue with recovered workspace').click()
            page.wait_for_function('key => JSON.parse(localStorage.getItem(key)).selections.length === 1', arg=KEY)
            assert stored()['selections'][0]['test'] == 'beta'
            # A real schema rebuild retains slot/filter alignment and empties old ranges.
            assert request.post('tests/restored/edit', data={'rename':{'signal':'force_new'}}).ok
            for col in ('force_new','force','rpm'): assert request.get(f'tests/restored/tp_stats?col={col}').ok
            full = {**stable, 'selections':[], 'viewMode':'full', 'fullRange':[1,3], 'filterState':{'tpKeys':[],'labels':[],'parameterFilters':[]}}
            seed(full)
            expect(page.get_by_role('region', name='Unavailable plot 1')).to_contain_text('signal')
            page.wait_for_function('key => JSON.parse(localStorage.getItem(key)).fullRange === null', arg=KEY)
            assert stored()['plotConfigs'][:3] == ['signal','force','rpm']
            assert stored()['plotFilters'][0]['kind'] == 'moving_avg'
            expect(recovery()).to_contain_text('sample data or variables changed')
            for width, factor in ((1440,1),(1100,1),(1440,1.25),(1440,1.5)):
                window_id = cdp.send('Browser.getWindowForTarget')['windowId']
                cdp.send('Browser.setWindowBounds', {'windowId':window_id,'bounds':{'width':width,'height':1000}})
                set_browser_zoom(worker,page,factor)
                box = page.get_by_role('region', name='Unavailable plot 1')
                box.get_by_role('button', name='Maximize slot').focus(); page.keyboard.press('Enter')
                expect(box.get_by_role('button',name='Restore grid')).to_be_visible()
                box.get_by_role('button',name='Restore grid').click()
                picker = page.get_by_role('combobox',name='Variable for plot 1',exact=True)
                picker.scroll_into_view_if_needed()
                assert picker.evaluate('''el => {
                  const r=el.getBoundingClientRect(); let top=0,bottom=innerHeight;
                  for(let p=el.parentElement;p;p=p.parentElement){
                    if(/auto|scroll|hidden/.test(getComputedStyle(p).overflowY)){
                      const b=p.getBoundingClientRect(); top=Math.max(top,b.top);bottom=Math.min(bottom,b.bottom);
                    }
                  } return r.top>=top-1 && r.bottom<=bottom+1 && document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===el;
                }'''), 'Variable recovery control is clipped by a short plot pane'
                assert recovery().evaluate('el => el.scrollWidth <= el.clientWidth + 1')
                capture_browser_view(cdp, output / f'recovery-{width}-{factor}.png')
            page.get_by_role('combobox',name='Variable for plot 1',exact=True).select_option('force_new')
            expect(page.get_by_role('region',name='Unavailable plot 1')).not_to_be_visible()
            trace_ready()
            print('PASS: changed TP bounds skipped, source edits reset ranges, missing variable retains slot/filter, explicit replacement, keyboard/maximize/resize/125%/150%', flush=True)

            legacy = {**session}; legacy.pop('sources')
            seed(legacy)
            expect(recovery()).to_contain_text('Older sessions saved test names')
            assert stored() == legacy
            recovery().get_by_role('button',name='Reconnect legacy session by name').focus(); page.keyboard.press('Enter')
            # alpha is the new uploaded copy and deliberately has no saved TPs.
            expect(recovery()).to_contain_text('reconnected by name at your request')
            if recovery().get_by_role('button',name='Continue with recovered workspace').count():
                recovery().get_by_role('button',name='Continue with recovered workspace').click()
            page.wait_for_function('key => Array.isArray(JSON.parse(localStorage.getItem(key)).sources)', arg=KEY)
            assert stored()['sources'][0]['id'] == replacement['id']
            seed(legacy); recovery().get_by_role('button',name='Discard legacy references').click()
            page.get_by_role('combobox',name='Choose active test after recovery').select_option('beta')
            trace_ready() if page.locator('.u-over').count() else None
            page.wait_for_function('key => JSON.parse(localStorage.getItem(key)).currentTest === "beta"', arg=KEY)
            assert stored()['selections'] == []
            # Network failure must leave storage and the retry path intact.
            page.route(api + '/analysis-sources', lambda route: route.fulfill(status=503,json={'detail':'Injected identity outage'}))
            metadata_requests.clear()
            seed(session)
            expect(page.get_by_role('heading',name='Unable to verify analysis sources')).to_be_visible()
            expect(page.locator('.app-header-summary')).to_have_text('3 ready tests')
            assert not metadata_requests, 'Saved names must not load before source identities are verified'
            assert stored() == session
            page.unroute(api + '/analysis-sources')
            page.get_by_role('button',name='Try again',exact=True).click()
            expect(recovery()).to_contain_text('same dataset after rename or restore')
            # Add an active duplicate UUID fixture; neither copy may be recovered.
            duplicate_dir = dataset / 'tests/alpha'
            (duplicate_dir/'source_identity.json').write_bytes((dataset/'tests/restored/source_identity.json').read_bytes())
            seed(session); expect(recovery()).to_contain_text('identity is ambiguous')
            assert stored() == session
            print('PASS: legacy reconnection/discard, 503 retry without state loss and duplicate-ID rejection', flush=True)
            assert not errors, errors
            assert all('503' in message or '500' in message for message in console), console
            beta = {k:v for k,v in original.items() if k.startswith('beta/')}
            assert {k:v for k,v in hashes(dataset/'tests').items() if k.startswith('beta/')} == beta
            (output/'results.json').write_text(json.dumps({'pure_cases':pure,'unchanged_beta_files':len(beta),'page_errors':errors,'console_errors':console},indent=2))
        except Exception:
            page.screenshot(path=str(output/'failure.png'),full_page=True)
            print(page.locator('body').aria_snapshot()[-14000:], flush=True)
            raise
        finally:
            context.close(); request.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port',type=int,default=3220)
    parser.add_argument('--backend-port',type=int,default=8220)
    args=parser.parse_args()
    output=ROOT/'data/verification/session-recovery'; output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-session-recovery-') as folder:
        temporary=Path(folder)
        with servers(temporary,output,args.frontend_port,args.backend_port) as (web,api,dataset):
            run_checks(web,api,dataset,temporary,output)
    print('PASS: isolated fixture/profile cleanup and owned servers stopped',flush=True)


if __name__ == '__main__': main()
