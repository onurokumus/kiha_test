"""Single-line TP filtering and original overlays with isolated data and Chromium.

Run with global Python/Playwright; backend/native operations exclusively use
the project's Python 3.13 child. Browser-only uPlot instrumentation inspects
the arrays and styles actually drawn. No production source is instrumented.
Temporary fixtures/profile and owned servers are removed on completion.
Evidence remains in ignored data/verification/filter-overlay.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

from verify_data_quality import ROOT, servers, upload
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout
from verify_time_y_zoom import instrument

TEST = 'overlay_fixture'
LOAD, SECOND, REFERENCE, PHASE = 'load_N', 'second_N', 'reference_N', 'phase_V'
COLS = [LOAD, SECOND, REFERENCE, PHASE]
ORIGIN = 1000.125
POINTS = [
    {'id': 7, 'name': 'Short spike', 'label': 'raw', 'start_s': .075,
     'end_s': .675, 'start_idx': 100, 'end_idx': 700},
    {'id': 9, 'name': 'Long spike', 'label': 'reduced', 'start_s': 1.975,
     'end_s': 21.975, 'start_idx': 2000, 'end_idx': 22000},
]
FILTER = {'kind': 'despike', 'despikeWindowMs': '25', 'maxSpikeMs': '3',
          'threshold': '3.5', 'absFloor': '2', 'replacement': 'linear'}


def make_data(request):
    lines = ['time,' + ','.join(COLS)]
    for i in range(24000):
        if 4500 <= i < 4510:
            continue
        value = 500 if i in (400, 4000) else 10 + math.sin(i / 150)
        lines.append(f'{ORIGIN+i/1000:.3f},{"" if i == 300 else f"{value:.9f}"},'
                     f'{2*value:.9f},20,{math.cos(i/100):.9f}')
    assert upload(request, TEST, '\n'.join(lines))['status'] == 'ready'
    result = request.put(f'tests/{TEST}/testpoints', data={
        'version': 1, 'test': TEST, 'test_points': POINTS})
    assert result.ok, result.text()
    meta = request.get(f'tests/{TEST}').json()
    assert meta['time_gap_count'] == 1, meta
    originals = {}
    for point in POINTS:
        response = request.get(f'tests/{TEST}/testpoints/{point["id"]}/data',
                               params={'cols': ','.join(COLS), 'max_points': 1500})
        assert response.ok, response.text()
        originals[point['id']] = response.json()
    assert originals[7]['mode'] == 'raw' and originals[9]['mode'] == 'envelope'
    # The old TP request used auto, which selects two min/max arrays here.
    # Compare the real backend contract without changing production sources.
    automatic = request.get(f'tests/{TEST}/filter', params={
        'cols': LOAD, 'type': 'despike', 'tp_id': 9, 'px': 674, 'display': 'auto',
        'window_s': .025, 'max_spike_s': .003, 'threshold': 3.5,
        'abs_floor': 2, 'replacement': 'linear'})
    assert automatic.ok, automatic.text()
    assert automatic.json()['mode'] == 'envelope', automatic.json()['mode']
    return originals


def seed(page, catalog):
    settings = {'scatterX': LOAD, 'scatterY': REFERENCE, 'clustering': False,
                'gridColumns': COLS, 'defaultViewMode': 'tp'}
    # Deliberately omit plotShowOriginal: an older session must default false.
    session = {'version': 1, 'sources': catalog, 'currentTest': TEST, 'xAxis': LOAD, 'yAxis': REFERENCE,
               'axesUserSet': True, 'selections': [
                   {'test': TEST, 'tpId': p['id'], 'hidden': False} for p in POINTS],
               'plotConfigs': COLS, 'plotsUserEdited': True, 'plotDensity': 'quad',
               'plotFilters': [{}, FILTER, {}, {}], 'scatterCollapsed': True, 'viewMode': 'tp'}
    page.add_init_script(f'''if (!sessionStorage.getItem('overlay-seeded')) {{
        localStorage.clear(); sessionStorage.setItem('overlay-seeded', 'true');
        localStorage.setItem('ptt.settings.v1', {json.dumps(json.dumps(settings))});
        localStorage.setItem('ptt.analysis-session.v1', {json.dumps(json.dumps(session))});
    }}''')


def plot(page, col=LOAD, full=False):
    return page.get_by_role('group', name=f'{col} {"full test" if full else "time"} plot', exact=True)


def toggle(page, col=LOAD, full=False):
    open_menu(page, col, full)
    return page.get_by_role('menuitemcheckbox', name=re.compile('^Show original with filtered'))


def open_menu(page, col=LOAD, full=False):
    button = plot(page, col, full).get_by_role('button', name=f'Plot actions for {col}', exact=True)
    button.focus()
    page.keyboard.press('Enter')
    expect(page.get_by_role('menu', name=f'Plot actions for {col}', exact=True)).to_be_visible()


def assert_toggle(page, col=LOAD, full=False, checked=False, enabled=True):
    control = toggle(page, col, full)
    expect(control).to_have_attribute('aria-checked', str(checked).lower())
    expect(control).to_have_attribute('aria-disabled', str(not enabled).lower())
    page.keyboard.press('Escape')


def filter_dialog(page, col=LOAD, full=False):
    open_menu(page, col, full)
    page.get_by_role('menuitem', name='Filter settings…', exact=True).click()
    dialog = page.get_by_role('dialog', name=f'Filter settings for {col}', exact=True)
    expect(dialog).to_be_visible()
    return dialog


def close_filter(page, dialog):
    page.keyboard.press('Escape')
    expect(dialog).to_have_count(0)


def set_threshold(page, value, full=False):
    dialog = filter_dialog(page, full=full)
    dialog.get_by_label('Threshold (MAD)', exact=True).fill(value)
    close_filter(page, dialog)


def snapshot(page, col=LOAD, full=False):
    target = plot(page, col, full)
    target.locator('.uplot').wait_for()
    wait_for_chart_layout(page)
    return target.locator('.uplot').evaluate('''el => {
        const u = el.__verificationPlot;
        return {mode: u.mode, data: u.data, series: u.series.slice(1).map((s, i) => ({
            label: s.label, color: typeof s.stroke === 'function' ? s.stroke(u, i+1) : s.stroke,
            width: s.width, dash: s.dash, spanGaps: s.spanGaps})),
            bands: u.bands.map(b => ({series: b.series, fill: typeof b.fill === 'function' ? b.fill(u, b) : b.fill})),
            x: [u.scales.x.min,u.scales.x.max], y: [u.scales.y.min,u.scales.y.max]};
    }''')


def assert_distinct(actual, full=False):
    originals = [s for s in actual['series'] if 'original' in s['label'].lower()]
    filtered = [s for s in actual['series'] if 'filtered' in s['label'].lower()]
    assert originals and filtered, actual['series']
    assert max(s['width'] for s in originals) < min(s['width'] for s in filtered)
    assert all(s['dash'] for s in originals), originals
    assert all(not s['dash'] for s in filtered), filtered
    assert all(not s['spanGaps'] for s in actual['series'])
    if not full:
        for point in POINTS:
            a = [s for s in originals if point['name'] in s['label']]
            b = [s for s in filtered if point['name'] in s['label']]
            if a and b:
                # Original opacity may use the same TP hue with an alpha suffix.
                assert all(s['color'][:7] == b[0]['color'][:7] for s in a), (a, b)


def assert_tp_data(actual, originals, responses, col=LOAD, visible=None):
    assert actual['mode'] == 2
    visible = POINTS if visible is None else visible
    filtered = [s for s in actual['series'] if 'filtered' in s['label'].lower()]
    assert len(filtered) == len(visible), actual['series']
    assert not actual['bands'], actual['bands']
    for point in visible:
        assert sum(point['name'] in s['label'] for s in filtered) == 1, filtered
    assert all(not re.search(r'\b(min|max)\b', s['label']) for s in filtered), filtered
    for index, series in enumerate(actual['series'], 1):
        point = next(p for p in POINTS if p['name'] in series['label'])
        times, values = actual['data'][index]
        if 'original' in series['label'].lower():
            original = originals[point['id']]['series'][col]
            assert times == original['t'] and values == original['y'], series
        else:
            match = next(r for r in reversed(responses) if r['query'].get('tp_id') == [str(point['id'])]
                         and r['query']['cols'] == [col] and r['status'] == 200)
            body = match['body']
            assert match['query'].get('display') == ['line'], match['query']
            assert body['mode'] == 'raw', body['mode']
            assert (body['i0'], body['i1']) == (point['start_idx'], point['end_idx'])
            assert math.isclose(body['time_origin_s'], originals[point['id']]['time_origin_s'], abs_tol=1e-6), (
                body['time_origin_s'], originals[point['id']]['time_origin_s'])
            assert 't0' not in match['query'] and 't1' not in match['query'], match['query']
            expected_t = body.get('relative_t') or [t-body['time_origin_s'] for t in body['t'] if t is not None]
            assert len(times) == len(expected_t) and all(math.isclose(a,b,abs_tol=1e-9)
                for a,b in zip(times,expected_t)), (point['id'], times[:4], expected_t[:4])
            expected = body['series'][col]
            assert values == [v for t,v in zip(body['t'],expected) if t is not None], series
            if point['id'] == 9:
                assert body['level'] > 1 and len(times) <= 8000, 'Long TP did not use bounded line reduction'
                assert body['n_raw'] == point['end_idx'] - point['start_idx']
            assert all(a < b for a,b in zip(times, times[1:])), point


def browser_toggle(page, requests, col=LOAD, full=False, expected=True):
    page.wait_for_timeout(450)
    before = len(requests)
    control = toggle(page, col, full)
    control.focus()
    page.keyboard.press('Enter')
    assert_toggle(page, col, full, checked=expected)
    expect(plot(page, col, full)).to_have_attribute('data-filter-display', 'overlay' if expected else 'filtered')
    page.wait_for_timeout(600)
    assert len(requests) == before, 'Display-only toggle refetched filters'


def check_layout(target):
    assert target.evaluate('''el => {
        const r=el.getBoundingClientRect();
        return r.left >= 0 && r.right <= innerWidth+1 && el.scrollWidth <= el.clientWidth+1 &&
            [...el.querySelectorAll('[data-plot-toolbar] button')]
                .every(b => {const q=b.getBoundingClientRect();return q.left >= r.left && q.right<=r.right+1;});
    }'''), 'Plot or header actions overflow'


def settled_grid(page, full=False):
    for col, display in ((LOAD,'overlay'),(SECOND,'filtered'),(REFERENCE,'original'),(PHASE,'original')):
        expect(plot(page,col,full)).to_have_attribute('data-filter-display',display)
        plot(page,col,full).locator('.uplot').wait_for()
    wait_for_chart_layout(page)
    page.mouse.move(5,5)
    page.wait_for_timeout(350)


def gesture(page, full=False, alt=False, pan=False):
    over = plot(page, full=full).locator('.u-over')
    over.scroll_into_view_if_needed()
    box = over.bounding_box()
    page.mouse.move(box['x']+box['width']*.4, box['y']+box['height']*.5)
    if alt:
        page.keyboard.down('Alt')
    if pan:
        page.keyboard.down('Shift')
        page.mouse.down()
        page.mouse.move(box['x']+box['width']*.6, box['y']+box['height']*.5,steps=8)
        page.mouse.up()
        page.keyboard.up('Shift')
    else:
        page.mouse.wheel(0,-120)
    if alt:
        page.keyboard.up('Alt')
    page.wait_for_timeout(450)


def dataset_hashes(dataset):
    return {str(p.relative_to(dataset)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in dataset.rglob('*') if p.is_file() and
            (p.suffix in ('.csv','.parquet','.npy') or p.name in ('meta.json','testpoints.json'))}


def run_checks(web, api, dataset, temporary, output):
    extension = temporary/'extension'
    extension.mkdir()
    (extension/'manifest.json').write_text(json.dumps({'manifest_version':3,
        'name':'Filter overlay zoom checks','version':'1.0','permissions':['tabs'],
        'background':{'service_worker':'background.js'}}))
    (extension/'background.js').write_text('chrome.runtime.onInstalled.addListener(() => {});')
    with sync_playwright() as playwright:
        request = playwright.request.new_context(base_url=api+'/')
        originals = make_data(request)
        catalog = request.get('analysis-sources').json()['sources']
        hashes_before = dataset_hashes(dataset)
        points_before = request.get(f'tests/{TEST}/testpoints').json()
        context = playwright.chromium.launch_persistent_context(str(temporary/'profile'),
            channel='chromium',headless=True,no_viewport=True,args=[
                f'--disable-extensions-except={extension}',f'--load-extension={extension}',
                '--window-size=1440,1000'])
        page = context.pages[0]
        page.set_default_timeout(10000)
        errors, requests, responses, writes, held, windows, held_original = [], [], [], [], [], [], []
        state = {'failure':'','hold':False,'hold_original':False,'misalign':False}
        page.on('pageerror',lambda error:errors.append(error.stack or str(error)))
        page.on('console',lambda message:errors.append(message.text) if message.type=='error' else None)
        page.on('request',lambda req:requests.append(req.url) if '/filter?' in req.url else None)
        page.on('request',lambda req:writes.append((req.method,req.url))
                if req.url.startswith(api) and req.method not in ('GET','HEAD','OPTIONS') else None)

        def filter_route(route):
            query=parse_qs(urlparse(route.request.url).query)
            own=query.get('cols')==[LOAD]
            fail=own and (state['failure']=='all' or
                           (state['failure']=='partial' and query.get('tp_id')==['9']))
            if fail:
                responses.append({'query':query,'status':503})
                route.fulfill(status=503,json={'detail':'Isolated filter failure'})
                return
            response=route.fetch()
            body=response.json()
            if own and state['misalign'] and 'tp_id' not in query:
                body['t'][0] += .0001
                route.fulfill(status=200,json=body)
                return
            responses.append({'query':query,'status':response.status,'body':body})
            if own and state['hold'] and query.get('tp_id')==['7']:
                state['hold']=False
                held.append((route,response))
                return
            route.fulfill(response=response)

        def data_route(route):
            response=route.fetch()
            query=parse_qs(urlparse(route.request.url).query)
            windows.append({'query':query,'body':response.json()})
            route.fulfill(response=response)

        def original_route(route):
            response=route.fetch()
            if state['hold_original']:
                state['hold_original']=False
                held_original.append((route,response))
            else:
                route.fulfill(response=response)

        context.route('**/src/utils/uplotSync.ts*',instrument)
        context.route('**/filter?*',filter_route)
        context.route(f'**/tests/{TEST}/data?*',data_route)
        context.route(f'**/tests/{TEST}/testpoints/7/data?*',original_route)
        try:
            seed(page, catalog)
            worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            page.goto(web)
            page.wait_for_load_state('networkidle')
            expect(plot(page)).to_have_attribute('data-filter-display','original')
            assert_toggle(page, enabled=False)
            # Reproduce the user's action through the current compact UI.
            dialog = filter_dialog(page)
            dialog.get_by_role('combobox').first.select_option('despike')
            for label, value in (('Window (ms)', '25'), ('Max spike (ms)', '3'),
                                 ('Threshold (MAD)', '3.5'), ('Min jump (units)', '2')):
                dialog.get_by_label(label, exact=True).fill(value)
            dialog.get_by_role('combobox').nth(1).select_option('linear')
            close_filter(page, dialog)
            for col in (LOAD,SECOND):
                assert_toggle(page,col)
                expect(plot(page,col)).to_have_attribute('data-filter-display','filtered')
            assert_toggle(page,REFERENCE,enabled=False)
            expect(plot(page,REFERENCE)).to_have_attribute('data-filter-display','original')
            initial=snapshot(page)
            assert all('filtered' in s['label'] for s in initial['series'])
            assert_tp_data(initial,originals,responses)
            assert all(r.get('body',{}).get('mode')=='raw' for r in responses)
            assert any(r.get('body',{}).get('replacement_counts',{}).get(LOAD,0)>0 for r in responses)
            page.mouse.move(5,5)
            page.get_by_role('button', name='Analyze', exact=True).focus()
            capture_browser_view(context.new_cdp_session(page),output/'tp-despike-single-lines.png')
            browser_toggle(page,requests)
            actual=snapshot(page)
            assert_distinct(actual)
            assert_tp_data(actual,originals,responses)
            assert_toggle(page,SECOND)
            expect(plot(page,SECOND)).to_have_attribute('data-filter-display','filtered')
            stats=plot(page).get_by_role('button',name=re.compile('^Statistics for load_N:'))
            expect(stats).to_contain_text('Original means')
            stats.click()
            panel=page.get_by_role('dialog',name='Statistics for load_N',exact=True)
            expect(panel).to_contain_text('Original data · complete test points')
            page.keyboard.press('Escape')
            print('PASS: adding Despike through menu/dialog draws one line per 600/20000-sample TP; bounded line reduction, exact saved rows/time origins, independent slots, optional original overlay and original statistics',flush=True)

            page.get_by_role('button',name=re.compile('Expand selection tray')).click()
            page.get_by_role('button',name=f'Hide Long spike from {TEST}',exact=True).click()
            expect(plot(page)).to_have_attribute('data-filter-display','overlay')
            page.wait_for_timeout(800)
            assert all('Long spike' not in s['label'] for s in snapshot(page)['series'])
            page.get_by_role('button',name=f'Show Long spike from {TEST}',exact=True).click()
            page.wait_for_timeout(900)
            assert_tp_data(snapshot(page),originals,responses)
            page.wait_for_function("JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).plotShowOriginal[0] === true")
            page.reload()
            page.wait_for_load_state('networkidle')
            assert_toggle(page,checked=True)
            assert_toggle(page,SECOND)
            assert_tp_data(snapshot(page),originals,responses)
            print('PASS: TP hide/reveal and independent original-display persistence on reload',flush=True)

            # Filter failures never label a raw fallback as filtered. Partial
            # overlay may show all original TPs and only successful filters.
            plot(page).get_by_role('button',name=f'Expand {LOAD}',exact=True).click()
            state['failure']='partial'
            set_threshold(page,'4')
            expect(plot(page)).to_contain_text('1 of 2 filtered test-point')
            partial=snapshot(page)
            assert any('Short spike' in s['label'] and 'filtered' in s['label'] for s in partial['series'])
            assert not any('Long spike' in s['label'] and 'filtered' in s['label'] for s in partial['series'])
            state['failure']=''
            plot(page).get_by_role('button',name='Retry',exact=True).click()
            expect(plot(page)).not_to_contain_text('1 of 2 filtered test-point')
            expect(plot(page)).to_have_attribute('data-filter-display','overlay')
            assert_tp_data(snapshot(page),originals,responses)
            state['failure']='all'
            set_threshold(page,'4.5')
            expect(plot(page).get_by_role('alert')).to_contain_text('could not be applied')
            expect(plot(page)).to_have_attribute('data-filter-display','original')
            assert not any('filtered' in s['label'] for s in snapshot(page)['series'])
            state['failure']=''
            plot(page).get_by_role('button',name='Retry',exact=True).click()
            expect(plot(page)).to_have_attribute('data-filter-display','overlay')
            print('PASS: partial/all filter failure, honest raw fallback and explicit Retry',flush=True)

            state['hold']=True
            set_threshold(page,'5')
            page.wait_for_timeout(700)
            assert held, 'Delayed filter response was not held'
            set_threshold(page,'5.5')
            expect(plot(page)).to_have_attribute('data-filter-display','overlay')
            newest=snapshot(page)
            for route,response in held:
                # An AbortController cancellation may already have invalidated
                # the request. Both rejection and ignored late data are valid.
                try:
                    route.fulfill(response=response)
                except Exception as error:
                    assert 'closed' in str(error).lower() or 'invalid' in str(error).lower(),str(error)
            held.clear()
            page.wait_for_timeout(450)
            assert snapshot(page)['data']==newest['data']
            plot(page).get_by_role('button',name=f'Minimize {LOAD}',exact=True).click()
            print('PASS: delayed obsolete TP filter cannot replace the current result',flush=True)

            page.wait_for_load_state('networkidle')
            expect(plot(page,SECOND)).to_have_attribute('data-filter-display','filtered')
            before=snapshot(page)
            count=len(requests)
            gesture(page,alt=True)
            yzoom=snapshot(page)
            assert yzoom['y']!=before['y'] and yzoom['x']==before['x']
            gesture(page)
            xzoom=snapshot(page)
            assert xzoom['x']!=before['x'] and xzoom['y']==yzoom['y']
            gesture(page,pan=True)
            assert snapshot(page)['x']!=xzoom['x']
            assert len(requests)==count,'TP pan/zoom refetched filters'
            page.get_by_role('button',name='Reset zoom',exact=True).click()
            print('PASS: overlay TP independent Y wheel, linked X wheel/pan and reset without filter requests',flush=True)

            cdp=context.new_cdp_session(page)
            window=cdp.send('Browser.getWindowForTarget')['windowId']
            baseline_dpr=page.evaluate('devicePixelRatio')
            for width,factor in [(1100,1),(1440,1.25),(1440,1.5)]:
                cdp.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'width':width,'height':1000}})
                set_browser_zoom(worker,page,factor)
                page.wait_for_function('dpr=>Math.abs(devicePixelRatio-dpr)<.001',arg=baseline_dpr*factor)
                settled_grid(page)
                check_layout(plot(page))
                capture_browser_view(cdp,output/f'tp-grid-{width}-{factor}.png')
                plot(page).get_by_role('button',name=f'Expand {LOAD}',exact=True).focus()
                page.keyboard.press('Enter')
                check_layout(plot(page))
                assert_distinct(snapshot(page))
                capture_browser_view(cdp,output/f'tp-expanded-{width}-{factor}.png')
                plot(page).get_by_role('button',name=f'Minimize {LOAD}',exact=True).focus()
                page.keyboard.press('Enter')
            page.get_by_role('group',name='Plot layout').get_by_role('button',name='9',exact=True).click()
            settled_grid(page)
            check_layout(plot(page))
            capture_browser_view(cdp,output/'tp-nine-150.png')
            browser_toggle(page,requests,expected=False)
            browser_toggle(page,requests)
            page.get_by_role('group',name='Plot layout').get_by_role('button',name='4',exact=True).click()
            set_browser_zoom(worker,page,1)
            print('PASS: TP overlay 1100px resize, 9-plot narrow headers, keyboard maximize/restore and actual 125%/150% browser zoom',flush=True)

            page.get_by_role('button',name='Full test',exact=True).click()
            expect(plot(page,full=True)).to_have_attribute('data-filter-display','overlay')
            assert_toggle(page,SECOND,True)
            for mode in ('Line','Min/max'):
                page.get_by_role('group',name='Full-test trace style').get_by_role('button',name=mode,exact=True).click()
                expect(plot(page,full=True)).to_have_attribute('data-filter-display','overlay')
                full=snapshot(page,full=True)
                assert_distinct(full,True)
                assert len(full['series'])==(2 if mode=='Line' else 4),full['series']
                assert len(full['bands'])==(0 if mode=='Line' else 2),full['bands']
                body=next(r['body'] for r in reversed(responses) if r['status']==200 and
                    r['query'].get('cols')==[LOAD] and 'tp_id' not in r['query'])
                assert body['mode']==('raw' if mode=='Line' else 'envelope')
                assert full['data'][0]==body['t']
                raw=next(r['body'] for r in reversed(windows) if r['query'].get('cols')==[LOAD])
                assert (body['i0'],body['i1'],body['t'])==(raw['i0'],raw['i1'],raw['t'])
                for index,s in enumerate(full['series'],1):
                    expected=(body if 'filtered' in s['label'] else raw)['series'][LOAD]
                    if body['mode']=='envelope':
                        expected=expected['max' if ' max' in s['label'] else 'min']
                    assert full['data'][index]==expected
                browser_toggle(page,requests,full=True,expected=False)
                only=snapshot(page,full=True)
                assert all('filtered' in s['label'] for s in only['series'])
                browser_toggle(page,requests,full=True)
            # Full-test range changes fetch matching original and filtered
            # windows. An overlay must never align arrays by length alone.
            before=snapshot(page,full=True)
            gesture(page,full=True)
            expect(plot(page,full=True)).to_have_attribute('data-filter-display','overlay')
            assert snapshot(page,full=True)['x']!=before['x']
            gesture(page,full=True,pan=True)
            expect(plot(page,full=True)).to_have_attribute('data-filter-display','overlay')
            page.get_by_role('button',name='Reset zoom',exact=True).click()
            expect(plot(page,full=True)).to_have_attribute('data-filter-display','overlay')
            print('PASS: Full test Line/envelope original+filtered series/bands, no toggle refetch, matching zoom/pan windows',flush=True)

            state['failure']='all'
            plot(page,full=True).get_by_role('button',name=f'Expand {LOAD}',exact=True).click()
            set_threshold(page,'6',full=True)
            expect(plot(page,full=True).get_by_role('alert')).to_contain_text('Could not apply the filter')
            expect(plot(page,full=True)).to_have_attribute('data-filter-display','original')
            state['failure']=''
            plot(page,full=True).get_by_role('button',name='Retry',exact=True).click()
            expect(plot(page,full=True)).to_have_attribute('data-filter-display','overlay')
            state['misalign']=True
            set_threshold(page,'6.5',full=True)
            expect(plot(page,full=True).get_by_role('alert')).to_be_visible()
            expect(plot(page,full=True)).to_have_attribute('data-filter-display','original')
            state['misalign']=False
            plot(page,full=True).get_by_role('button',name='Retry',exact=True).click()
            expect(plot(page,full=True)).to_have_attribute('data-filter-display','overlay')
            plot(page,full=True).get_by_role('button',name=f'Minimize {LOAD}',exact=True).click()
            for factor in (1.25,1.5):
                set_browser_zoom(worker,page,factor)
                settled_grid(page,True)
                check_layout(plot(page,full=True))
                capture_browser_view(cdp,output/f'full-grid-{factor}.png')
                plot(page,full=True).get_by_role('button',name=f'Expand {LOAD}',exact=True).focus()
                page.keyboard.press('Enter')
                wait_for_chart_layout(page)
                check_layout(plot(page,full=True))
                capture_browser_view(cdp,output/f'full-expanded-{factor}.png')
                plot(page,full=True).get_by_role('button',name=f'Minimize {LOAD}',exact=True).click()
            page.get_by_role('group',name='Plot layout').get_by_role('button',name='9',exact=True).click()
            settled_grid(page,True)
            check_layout(plot(page,full=True))
            capture_browser_view(cdp,output/'full-nine-150.png')
            page.get_by_role('group',name='Plot layout').get_by_role('button',name='4',exact=True).click()
            set_browser_zoom(worker,page,1)
            print('PASS: Full test failure and misaligned-time rejection/retry; 9-plot headers and real 125%/150% zoom/maximize/restore',flush=True)

            page.get_by_role('button',name='Test points',exact=True).click()
            page.get_by_role('button',name=re.compile('Expand selection tray')).click()
            page.get_by_role('button',name=f'Hide Long spike from {TEST}',exact=True).click()
            page.wait_for_function("JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).selections.some(s=>s.tpId===9&&s.hidden)")
            state['hold_original']=True
            page.reload()
            expect(plot(page)).to_contain_text('Loading original traces')
            expect(plot(page)).to_have_attribute('data-filter-display','filtered')
            assert held_original
            for route,response in held_original:
                route.fulfill(response=response)
            held_original.clear()
            expect(plot(page)).to_have_attribute('data-filter-display','overlay')
            print('PASS: delayed original trace is labeled as loading while current filtered data remains visible',flush=True)

            assert not writes,writes
            assert dataset_hashes(dataset)==hashes_before,'Stored data or metadata changed'
            assert request.get(f'tests/{TEST}/testpoints').json()==points_before
            unexpected=[e for e in errors if not any(text in e for text in
                ('503','Isolated filter failure','filter failed for'))]
            assert not unexpected,unexpected
            evidence={'filter_requests':len(requests),'source_files_unchanged':sorted(hashes_before),
                'browser_mutations':writes,'unexpected_browser_errors':unexpected,
                'response_metadata':[{'query':r['query'],'status':r['status'],
                    'window':{k:v for k,v in r.get('body',{}).items() if k not in ('t','series')}} for r in responses]}
            (output/'results.json').write_text(json.dumps(evidence,indent=2))
            print('PASS: no browser writes, unchanged original/stored data/metadata/testpoints, no unexpected browser errors',flush=True)
        except Exception:
            page.screenshot(path=str(output/'failure.png'),full_page=True)
            (output/'failure-state.json').write_text(json.dumps({'errors':errors,'requests':requests,
                'buttons':page.get_by_role('button').all_text_contents()},indent=2))
            raise
        finally:
            context.close()
            request.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port',type=int,default=3120)
    parser.add_argument('--backend-port',type=int,default=8120)
    args=parser.parse_args()
    output=ROOT/'data/verification/filter-overlay'
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-filter-overlay-') as directory:
        temporary=Path(directory)
        with servers(temporary,output,args.frontend_port,args.backend_port) as (web,api,dataset):
            run_checks(web,api,dataset,temporary,output)
    print('PASS: temporary fixtures/profile removed and both owned servers stopped',flush=True)


if __name__=='__main__':
    main()
