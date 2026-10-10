"""Native, read-only XY original/filtered comparison on isolated recordings.

Run after the production build:
  python -X utf8 scripts/verify_preprocess_xy_comparison.py
Uses Python 3.13 for native fixture ingestion/filtering and global Playwright.
Owns temporary datasets, private Chromium profile and hidden backend/preview.
Only marked fixture setup can write; every browser API write fails closed.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
import traceback
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from verify_preprocess_inplace import ROOT, FLIGHT, DATASHEET, GAPPED, MARKER, get, hashes, serve
from verify_preprocess_comparison import LARGE, LEGACY, prepare_comparison


def prepare_xy(directory, python):
    prepare_comparison(directory, python)
    # Two independently changing axes make accidental coordinate mixing visible.
    generator = r'''import csv,json,math,os
from pathlib import Path
import numpy as np
from scipy import signal
from app.ingest import ingest_csv
root=Path(os.environ['KIHA_DATA_DIR'])
path=root/'b_comparison_large.csv'
with path.open('w',newline='',encoding='utf-8') as stream:
    writer=csv.writer(stream);writer.writerow(['time_s','thrust_n','rpm'])
    for i in range(24000):
        t=i/200
        writer.writerow([t,10+math.sin(2*math.pi*2*t)+.6*math.cos(2*math.pi*45*t),
            1500+100*math.sin(2*math.pi*.7*t)+40*math.cos(2*math.pi*31*t)])
ingest_csv(path,'b_comparison_large',copy_raw=True,time_mode='column',time_column='time_s')
with path.open(newline='',encoding='utf-8') as stream: rows=list(csv.DictReader(stream))
original={key:np.array([float(row[key]) for row in rows]) for key in ('time_s','thrust_n','rpm')}
expected=json.loads((root/'expected.json').read_text(encoding='utf-8'))
filtered={key:values.copy() for key,values in original.items()}
for key,cutoff in [('thrust_n',8),('rpm',6)]:
    filtered[key]=signal.sosfiltfilt(signal.butter(4,cutoff,btype='lowpass',fs=200,output='sos'),original[key])
expected['xy']={
    'a_inplace_flight':{'original':expected['original'],
        'filtered':dict(expected['original'],thrust_n=expected['low8'],torque_nm=expected['moving']['torque_nm'])},
    'b_comparison_large':{'original':{k:v.tolist() for k,v in original.items()},
        'filtered':{k:v.tolist() for k,v in filtered.items()}}}
(root/'expected.json').write_text(json.dumps(expected),encoding='utf-8')
'''
    subprocess.run([str(python), '-B', '-c', generator], check=True, cwd=ROOT / 'backend',
        env={**os.environ, 'KIHA_DATA_DIR': str(directory), 'PYTHONPATH': str(ROOT / 'backend')})
    marker = json.loads((directory / MARKER).read_text(encoding='utf-8'))
    marker['hashes'][LARGE] = hashes(directory / 'tests' / LARGE)
    (directory / MARKER).write_text(json.dumps(marker), encoding='utf-8')


def seed_xy(api, directory):
    marker = json.loads((directory / MARKER).read_text(encoding='utf-8'))
    assert marker['dataset'] == str(directory.resolve())
    for name in (FLIGHT, LARGE):
        assert hashes(directory / 'tests' / name) == marker['hashes'][name]
        snapshot = get(api, f'/tests/{name}/preprocess')
        second = ({'column': 'torque_nm', 'filter': {'kind': 'moving_avg', 'window_s': .025}}
                  if name == FLIGHT else {'column': 'rpm', 'filter': {'kind': 'lowpass', 'order': 4, 'f1': 6}})
        payload = {'request_id': str(uuid4()), 'source_id': snapshot['source']['id'],
            'source_revision': snapshot['source']['revision'], 'filters': [
                {'column': 'thrust_n', 'filter': {'kind': 'lowpass', 'order': 4, 'f1': 8}}, second]}
        request = Request(api + f'/tests/{name}/preprocess', data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        with urlopen(request, timeout=15) as response: assert response.status == 202
        deadline = time.monotonic() + 60
        while True:
            state = next(test for test in get(api, '/tests') if test['name'] == name)
            operation = state.get('preprocessing_operation', {})
            if operation.get('state') == 'completed': break
            assert operation.get('state') != 'failed', operation
            assert time.monotonic() < deadline, state
            time.sleep(.1)
    return {name: hashes(directory / 'tests' / name) for name in marker['hashes']}


def check_xy(body, expected):
    """Independent SciPy oracle proves common native-row X/Y/source pairing."""
    oracle = expected['xy'][body['source']['name']]
    count = len(body['indices'])
    assert count == body['n_sampled'] == len(body['t']) and count > 0
    assert body['indices'] == sorted(set(body['indices']))
    assert all(body['i0'] <= index < body['i1'] for index in body['indices'])
    assert body['n_raw'] == body['i1'] - body['i0']
    assert body['fallback_indices'] == []  # These fixtures have dense finite pairs.
    assert body['indices'] == list(range(body['i0'], body['i1'], body['stride']))
    assert body['mode'] == ('raw' if body['stride'] == 1 else 'sampled')
    for offset, index in enumerate(body['indices']):
        assert math.isclose(body['t'][offset], oracle['original']['time_s'][index], abs_tol=1e-12)
        for source in ('original', 'filtered'):
            for axis in ('x', 'y'):
                assert len(body[source][axis]) == count
                actual = body[source][axis][offset]
                target = oracle[source][body[axis]][index]
                assert math.isclose(actual, target, rel_tol=2e-10, abs_tol=2e-10), (source, axis, index, actual, target)
    for summary in body['summary'].values():
        assert summary['finite_pairs'] == count and summary['missing_pairs'] == 0
        assert summary['native_finite_pairs'] == body['n_raw'] and summary['native_missing_pairs'] == 0


def verify(args, directory, initial_hashes):
    from playwright.sync_api import expect, sync_playwright
    from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout
    expected = json.loads((directory / 'expected.json').read_text(encoding='utf-8'))
    report = {'checks': [], 'requests': [], 'xy': [], 'time': [], 'pageErrors': [],
              'consoleErrors': [], 'blockedWrites': [], 'layouts': []}
    output = args.output.resolve()
    inject = {'error': None, 'hold': None}
    delayed = []

    def passed(label):
        report['checks'].append(label); print('PASS:', label, flush=True)

    with tempfile.TemporaryDirectory(prefix='ptt-xy-comparison-profile-') as profile, sync_playwright() as p:
        extension = Path(profile) / 'extension'; extension.mkdir()
        (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3,
            'name': 'Preprocessing XY comparison QA', 'version': '1.0', 'permissions': ['tabs'],
            'background': {'service_worker': 'background.js'}}))
        (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
        context = p.chromium.launch_persistent_context(str(Path(profile) / 'profile'), channel='chromium',
            headless=True, no_viewport=True, accept_downloads=True,
            args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]; page.set_default_timeout(20000)
        page.on('pageerror', lambda e: report['pageErrors'].append(str(e)))
        page.on('console', lambda e: report['consoleErrors'].append(e.text) if e.type == 'error' else None)

        def route_api(route):
            request = route.request; parsed = urlparse(request.url)
            path = unquote(parsed.path).split('/api', 1)[-1]
            report['requests'].append({'method': request.method, 'path': path, 'query': parsed.query})
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                report['blockedWrites'].append({'method': request.method, 'path': path}); route.abort(); return
            xy = path.endswith('/preprocess/compare/xy')
            if xy and inject['error']:
                code, message = inject['error']; route.fulfill(status=code, json={'detail': message}); return
            response = route.fetch(url=args.api + path + ('?' + parsed.query if parsed.query else ''), timeout=60000)
            if xy and response.ok:
                body = response.json(); check_xy(body, expected)
                report['xy'].append(body)
                if inject['hold'] == (body['x'], body['y']):
                    inject['hold'] = None; delayed.append((route, response)); return
            if path.endswith('/preprocess/compare') and response.ok:
                report['time'].append(response.json())
            route.fulfill(response=response)

        context.route('**/api/**', route_api)
        seed = {'scatterX': 'rpm', 'scatterY': 'thrust_n', 'clustering': False,
                'gridColumns': ['thrust_n'], 'datasheetZone': DATASHEET}
        session = {'version': 1, 'sources': get(args.api, '/analysis-sources')['sources'], 'currentTest': FLIGHT,
            'xAxis': 'rpm', 'yAxis': 'thrust_n', 'axesUserSet': True,
            'selections': [{'test': FLIGHT, 'tpId': 2, 'hidden': False, 'color': '#d55e00'}],
            'plotConfigs': ['thrust_n'], 'plotsUserEdited': True, 'plotDensity': 'single',
            'viewMode': 'xy', 'xySource': 'tp', 'scatterCollapsed': True}
        page.add_init_script("if(!sessionStorage.getItem('xy-comparison-seeded')){sessionStorage.setItem('xy-comparison-seeded','1');localStorage.clear();"
            + "localStorage.setItem('ptt.theme.v1','light');localStorage.setItem('ptt.settings.v1'," + json.dumps(json.dumps(seed)) + ");"
            + "localStorage.setItem('ptt.analysis-session.v1'," + json.dumps(json.dumps(session)) + ");}")
        cdp = context.new_cdp_session(page)
        try:
            page.goto(args.url); page.wait_for_load_state('networkidle')
            report['bundle'] = page.locator('script[type="module"][src]').get_attribute('src')
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            window = cdp.send('Browser.getWindowForTarget')['windowId']; dpr = page.evaluate('devicePixelRatio')
            page.get_by_role('navigation', name='Main navigation').get_by_role('button', name='Uploads', exact=True).click()
            for name in (DATASHEET, GAPPED, LEGACY):
                expect(page.get_by_role('button', name=f'Compare original and filtered data for {name}', exact=True)).to_have_count(0)
            opener = page.get_by_role('button', name=f'Compare original and filtered data for {FLIGHT}', exact=True)
            opener.focus(); page.keyboard.press('Enter')
            dialog = page.get_by_role('dialog', name='Pre-process flight', exact=True)
            panel = dialog.get_by_role('tabpanel', name='Compare data', exact=True)
            mode = panel.get_by_role('group', name='Comparison view', exact=True)
            xselect = panel.get_by_role('button', name='Comparison X variable', exact=True)
            yselect = panel.get_by_role('button', name='Comparison Y variable', exact=True)
            xyplot = panel.get_by_role('region', name='Original and filtered XY comparison plot', exact=True)
            timeplot = panel.get_by_role('region', name='Original and filtered comparison plot', exact=True)
            xyreset = panel.get_by_role('button', name='Reset XY comparison zoom', exact=True)

            def settle(plot=None):
                page.wait_for_load_state('networkidle')
                target = plot if plot is not None else xyplot
                expect(target.locator('.uplot')).to_be_visible()
                wait_for_chart_layout(page)

            def choose_mode(label):
                mode.get_by_role('button', name=label, exact=True).click()
                expect(mode.get_by_role('button', name=label, exact=True)).to_have_attribute('aria-pressed', 'true')
                settle(timeplot if label == 'Time' else xyplot)

            def select_axis(axis, name, keyboard=False, wait=True):
                trigger = xselect if axis == 'x' else yselect
                trigger.click()
                search = page.get_by_role('combobox').last
                search.fill(name)
                option = page.get_by_role('option', name=re.compile('^' + re.escape(name) + '(?:\\s|$)'))
                expect(option).to_be_visible()
                if keyboard: search.press('ArrowDown'); search.press('Enter')
                else: option.click()
                if wait: settle()

            def canvas_hash(plot=xyplot):
                return hashlib.sha256(plot.locator('canvas').first.screenshot()).hexdigest()

            def drag(plot, start=(.2, .3), end=(.6, .65), modifier=None):
                over = plot.locator('.u-over'); over.scroll_into_view_if_needed(); bounds = over.bounding_box()
                if modifier: page.keyboard.down(modifier)
                page.mouse.move(bounds['x'] + bounds['width'] * start[0], bounds['y'] + bounds['height'] * start[1])
                page.mouse.down()
                page.mouse.move(bounds['x'] + bounds['width'] * end[0], bounds['y'] + bounds['height'] * end[1], steps=9)
                page.mouse.up()
                if modifier: page.keyboard.up(modifier)
                settle(plot)

            def wheel_consumed(plot, **modifiers):
                return plot.locator('.u-over').evaluate('''(el,mods)=>{
                    const r=el.getBoundingClientRect();
                    const event=new WheelEvent('wheel',{bubbles:true,cancelable:true,
                        clientX:r.x+r.width*.5,clientY:r.y+r.height*.5,deltaY:-100,...mods});
                    el.dispatchEvent(event);return event.defaultPrevented;
                }''', modifiers)

            def hover_oracle():
                over = xyplot.locator('.u-over'); over.scroll_into_view_if_needed(); bounds = over.bounding_box()
                page.mouse.move(bounds['x'] + bounds['width'] * .42, bounds['y'] + bounds['height'] * .5)
                readout = panel.locator('[aria-label="XY comparison cursor values"]')
                expect(readout).to_contain_text('Row')
                values = readout.evaluate('el=>[...el.children].map(n=>n.textContent)')
                match = re.search(r'([\d.,-]+) s · Row ([\d,]+)', values[0]); assert match, values
                index = int(match[2].replace(',', ''))
                body = report['xy'][-1]; oracle = expected['xy'][body['source']['name']]
                assert math.isclose(float(match[1].replace(',', '')), oracle['original']['time_s'][index], abs_tol=1e-6)
                for text, source in zip(values[1:], ('original', 'filtered')):
                    match = re.search(r'X ([\d.,-]+) · Y ([\d.,-]+)', text); assert match, values
                    for axis, value in zip(('x', 'y'), (match[1], match[2])):
                        target = oracle[source][body[axis]][index]
                        assert math.isclose(float(value.replace(',', '')), target, rel_tol=1e-6, abs_tol=1e-5), (values, target)
                match = re.search(r'ΔX ([\d.,-]+) · ΔY ([\d.,-]+)', values[3]); assert match, values
                for axis, value in zip(('x', 'y'), (match[1], match[2])):
                    target = oracle['filtered'][body[axis]][index] - oracle['original'][body[axis]][index]
                    assert math.isclose(float(value.replace(',', '')), target, rel_tol=1e-6, abs_tol=1e-5), (values, target)
                report.setdefault('cursorReadouts', []).append(values)

            settle(timeplot)
            choose_mode('XY scatter')
            select_axis('x', 'torque_nm', keyboard=True)
            select_axis('y', 'thrust_n')
            assert report['xy'][-1]['x'] == 'torque_nm' and report['xy'][-1]['y'] == 'thrust_n'
            assert report['xy'][-1]['mode'] == 'raw' and report['xy'][-1]['n_raw'] == 1200
            for source in ('x', 'y'):
                body = report['xy'][-1]
                assert any(a != b for a, b in zip(body['original'][source], body['filtered'][source]))
            (output / 'xy-aria.txt').write_text(dialog.aria_snapshot(), encoding='utf-8')
            capture_browser_view(cdp, output / 'xy-native-light.png')
            hover_oracle()
            passed('XY opens from saved comparison with styled searchable keyboard X/Y selectors and independent native values on both filtered axes')

            before = len(report['xy']); canvases = []
            traces = panel.get_by_role('group', name='Comparison traces', exact=True)
            for label in ('Original', 'Filtered', 'Both'):
                traces.get_by_role('button', name=label, exact=True).click(); settle()
                expect(traces.get_by_role('button', name=label, exact=True)).to_have_attribute('aria-pressed', 'true')
                canvases.append(canvas_hash())
            assert len(set(canvases)) == 3 and len(report['xy']) == before
            panel.get_by_role('button', name='Swap comparison axes', exact=True).click(); settle()
            assert report['xy'][-1]['x'] == 'thrust_n' and report['xy'][-1]['y'] == 'torque_nm'
            select_axis('x', 'time_s')
            assert report['xy'][-1]['original']['x'] == report['xy'][-1]['filtered']['x'] == report['xy'][-1]['t']
            panel.get_by_role('button', name='Swap comparison axes', exact=True).click(); settle()
            assert report['xy'][-1]['y'] == 'time_s'
            select_axis('x', 'time_s')
            assert report['xy'][-1]['original']['x'] == report['xy'][-1]['original']['y']
            select_axis('x', 'torque_nm'); select_axis('y', 'thrust_n')
            passed('distinct Both/Original/Filtered scatter, axis swap, measured time on either axis and same-variable axes preserve paired rows')

            before = len(report['xy']); original_canvas = canvas_hash()
            drag(xyplot)
            assert canvas_hash() != original_canvas
            shifted = canvas_hash(); drag(xyplot, (.4, .45), (.6, .55), modifier='Shift')
            assert canvas_hash() != shifted
            xyplot.focus(); page.keyboard.press('Home'); settle()
            baseline = canvas_hash(); xyplot.focus(); page.keyboard.press('+'); settle()
            assert canvas_hash() != baseline
            for key in ('ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', '-'):
                xyplot.focus(); page.keyboard.press(key); settle()
            xyreset.click(); settle()
            assert len(report['xy']) == before, 'XY value-axis navigation must not change the recording interval'
            baseline = canvas_hash()
            assert not wheel_consumed(xyplot) and not wheel_consumed(xyplot, ctrlKey=True) and not wheel_consumed(xyplot, metaKey=True)
            assert canvas_hash() == baseline, 'Page scroll/browser zoom modifiers must not zoom the plot'
            assert wheel_consumed(xyplot, shiftKey=True); settle()
            assert canvas_hash() != baseline
            xyreset.click(); settle(); hover_oracle()
            with page.expect_download() as download:
                panel.get_by_role('button', name='Export XY comparison PNG', exact=True).click()
            png = output / 'xy-comparison.png'; download.value.save_as(png)
            assert png.read_bytes().startswith(b'\x89PNG\r\n\x1a\n') and png.stat().st_size > 10000
            passed('XY drag/Shift-pan, keyboard zoom/four-way pan/Home, reset and native PNG work without refetching or changing the interval')

            choose_mode('Time'); drag(timeplot, (.2, .4), (.6, .6))
            interval = report['time'][-1]
            assert interval['n_raw'] < 700
            choose_mode('XY scatter')
            assert (report['xy'][-1]['i0'], report['xy'][-1]['i1']) == (interval['i0'], interval['i1'])
            drag(xyplot); choose_mode('Time')
            assert (report['time'][-1]['i0'], report['time'][-1]['i1']) == (interval['i0'], interval['i1'])
            choose_mode('XY scatter')
            panel.get_by_role('button', name='Reset interval', exact=True).click(); settle()
            assert report['xy'][-1]['n_raw'] == 1200
            passed('Time zoom selects the shared recording interval; XY axis zoom preserves it and Reset interval restores the full flight')

            inject['hold'] = ('rpm', 'thrust_n')
            select_axis('x', 'rpm', wait=False)
            deadline = time.monotonic() + 5
            while not delayed:
                assert time.monotonic() < deadline, 'Expected held XY request'; page.wait_for_timeout(25)
            expect(xyplot.locator('.uplot')).to_have_count(0)
            select_axis('x', 'time_s')
            for held_route, held_response in delayed: held_route.fulfill(response=held_response)
            delayed.clear(); settle()
            expect(xselect).to_contain_text('time_s')
            inject['error'] = (503, 'Fixture XY comparison temporarily unavailable')
            select_axis('x', 'torque_nm', wait=False)
            expect(panel.get_by_text('Fixture XY comparison temporarily unavailable', exact=False)).to_be_visible()
            expect(xyplot.locator('.uplot')).to_have_count(0)
            inject['error'] = None
            panel.get_by_role('button', name='Retry XY comparison', exact=True).click(); settle()
            assert report['xy'][-1]['x'] == 'torque_nm'
            inject['error'] = (409, 'This flight changed. Reload saved data before comparing.')
            select_axis('x', 'rpm', wait=False)
            expect(panel.get_by_text('This flight changed.', exact=False)).to_be_visible()
            inject['error'] = None
            panel.get_by_role('button', name='Reload saved data', exact=True).click()
            # Reload remounts the saved comparison after its source check; wait
            # for that completed transition instead of sampling the old XY DOM.
            expect(mode.get_by_role('button', name='Time', exact=True)).to_have_attribute('aria-pressed', 'true')
            settle(timeplot); choose_mode('XY scatter')
            passed('late XY responses cannot replace new axes; failed/stale reads hide old points and recover with Retry or Reload')

            for theme in ('light', 'dark'):
                page.keyboard.press('Escape'); expect(dialog).not_to_be_visible(); expect(opener).to_be_focused()
                toggle = page.get_by_role('switch', name='Dark mode', exact=True)
                if (toggle.get_attribute('aria-checked') == 'true') != (theme == 'dark'): toggle.click()
                opener.click(); settle(timeplot); choose_mode('XY scatter')
                select_axis('x', 'torque_nm'); select_axis('y', 'thrust_n')
                for zoom in (1, 1.25, 1.5):
                    width, height = (1440, 1000) if zoom == 1 else (1100, 780)
                    cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': height}})
                    set_browser_zoom(worker, page, zoom)
                    page.wait_for_function('v=>Math.abs(devicePixelRatio-v)<.01', arg=dpr * zoom); settle()
                    for expanded in (False, True):
                        if expanded:
                            dialog.get_by_role('button', name='Expand comparison', exact=True).focus(); page.keyboard.press('Enter'); settle()
                        bounds = dialog.evaluate('''d=>{const r=d.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:innerWidth,height:innerHeight,overflow:d.scrollWidth-d.clientWidth}}''')
                        assert bounds['x'] >= 0 and bounds['y'] >= 0 and bounds['right'] <= bounds['width'] + 1 and bounds['bottom'] <= bounds['height'] + 1 and bounds['overflow'] <= 1, bounds
                        report['layouts'].append({'theme': theme, 'zoom': zoom, 'expanded': expanded, **bounds})
                        xselect.scroll_into_view_if_needed(); xselect.focus(); page.keyboard.press('Enter')
                        expect(page.get_by_role('listbox', name='Comparison X variable', exact=True)).to_be_visible()
                        menu = page.get_by_role('listbox', name='Comparison X variable', exact=True)
                        menu.evaluate('''async el=>{
                            await Promise.all(el.closest('[data-placement]').getAnimations({subtree:true})
                                .map(animation=>animation.finished.catch(()=>{})));
                            await new Promise(resolve=>requestAnimationFrame(resolve));
                        }''')
                        menu_bounds = menu.bounding_box(); viewport = page.evaluate('({width:innerWidth,height:innerHeight})')
                        assert menu_bounds['x'] >= 0 and menu_bounds['x'] + menu_bounds['width'] <= viewport['width'] + 1
                        capture_browser_view(cdp, output / f'xy-selector-{theme}-{zoom}-{expanded}.png')
                        page.keyboard.press('Escape'); expect(xselect).to_be_focused()
                        expect(dialog).to_be_visible()
                        xyplot.evaluate('el=>el.scrollIntoView({block:"center"})'); xyplot.focus(); expect(xyplot).to_be_focused()
                        over_bounds = xyplot.locator('.u-over').bounding_box()
                        assert over_bounds['width'] >= 250 and over_bounds['height'] >= 75, over_bounds
                        capture_browser_view(cdp, output / f'xy-plot-{theme}-{zoom}-{expanded}.png')
                        if expanded: dialog.get_by_role('button', name='Restore comparison size', exact=True).click(); settle()
                set_browser_zoom(worker, page, 1)
                cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': 1440, 'height': 1000}})
            passed('both themes, resized desktop and actual 100/125/150% browser zoom retain styled selectors, keyboard focus and expand/restore')

            page.keyboard.press('Escape'); expect(dialog).not_to_be_visible()
            page.get_by_role('button', name=f'Compare original and filtered data for {LARGE}', exact=True).click()
            settle(timeplot); choose_mode('XY scatter'); select_axis('x', 'rpm'); select_axis('y', 'thrust_n')
            body = report['xy'][-1]
            assert body['source']['name'] == LARGE and body['mode'] == 'sampled' and body['n_sampled'] <= 6000
            capture_browser_view(cdp, output / 'xy-large-sampled-dark.png')
            choose_mode('Time'); drag(timeplot, (.35, .4), (.45, .6)); interval = report['time'][-1]
            choose_mode('XY scatter'); body = report['xy'][-1]
            assert body['mode'] == 'raw' and body['n_raw'] < 6000 and body['i0'] == interval['i0']
            capture_browser_view(cdp, output / 'xy-large-native-dark.png')
            passed('large scatter reduction uses shared native row indices across both axes and sources; narrowing the Time interval returns all native pairs')
            page.keyboard.press('Escape'); expect(dialog).not_to_be_visible()
            page.get_by_role('navigation', name='Main navigation').get_by_role('button', name='Analyze', exact=True).click()
            page.get_by_role('button', name='X variable', exact=True).click()
            page.get_by_role('option', name=re.compile('^rpm(?:\\s|$)')).click()
            main_plot = page.get_by_role('group', name='thrust_n versus rpm XY plot', exact=True)
            settle(main_plot)
            baseline = canvas_hash(main_plot)
            assert not wheel_consumed(main_plot, ctrlKey=True) and not wheel_consumed(main_plot, metaKey=True)
            assert canvas_hash(main_plot) == baseline
            assert wheel_consumed(main_plot); settle(main_plot)
            assert canvas_hash(main_plot) != baseline
            drag(main_plot, (.4, .4), (.55, .6), modifier='Shift')
            main_plot.locator('.u-over').dblclick(); settle(main_plot)
            passed('existing Analyze XY retains plain-wheel zoom, Shift-pan and reset; Ctrl/Meta wheel remains reserved for the browser')
            assert all(hashes(directory / 'tests' / name) == prior for name, prior in initial_hashes.items())
            assert not report['blockedWrites'] and not report['pageErrors'], report
            assert all(any(code in line for code in ('409', '503')) for line in report['consoleErrors']), report['consoleErrors']
            assert len(get(args.api, '/tests')) == 5
            passed('all five fixture flights stay byte-identical; no extra flights, browser writes or unexpected errors')
        except Exception:
            traceback.print_exc()
            try:
                capture_browser_view(cdp, output / 'failure.png')
                state = page.locator('body').aria_snapshot()
                (output / 'failure-aria.txt').write_text(state, encoding='utf-8')
                print(state[-9000:], flush=True)
            except Exception as capture_error:
                print('Failure evidence unavailable:', capture_error, flush=True)
            raise
        finally:
            # Keep compact evidence rather than repeating every native sample.
            report['xy'] = [{key: value for key, value in body.items()
                             if key not in ('original', 'filtered', 't', 'indices')} for body in report['xy']]
            report['time'] = [{key: value for key, value in body.items()
                               if key not in ('original', 'filtered', 't')} for body in report['time']]
            (output / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            context.close()
    print(f"PASS {len(report['checks'])} browser groups. Evidence: {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, default=ROOT / 'backend/.venv/Scripts/python.exe')
    parser.add_argument('--api', default='http://127.0.0.1:8130/api')
    parser.add_argument('--url', default='http://127.0.0.1:8129/ptt/')
    parser.add_argument('--output', type=Path, default=Path(tempfile.gettempdir()) / 'ptt-preprocess-xy-comparison-verification')
    parser.add_argument('--serve', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve: serve(args.serve); return
    args.output.mkdir(parents=True, exist_ok=True)
    for port in (urlparse(args.api).port, urlparse(args.url).port):
        with socket.socket() as sock: sock.bind(('127.0.0.1', port))
    processes = []
    with tempfile.TemporaryDirectory(prefix='ptt-xy-comparison-native-') as temporary:
        directory = Path(temporary)
        prepare_xy(directory, args.python)
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with (args.output / 'backend.log').open('w') as backend_log, (args.output / 'preview.log').open('w') as preview_log:
            try:
                processes.append(subprocess.Popen([str(args.python), '-B', str(Path(__file__).resolve()), '--serve', str(directory)],
                    cwd=ROOT / 'backend', env={**os.environ, 'KIHA_DATA_DIR': str(directory),
                    'KIHA_PORT': str(urlparse(args.api).port), 'PYTHONPATH': str(ROOT / 'backend')},
                    stdout=backend_log, stderr=subprocess.STDOUT, creationflags=flags))
                config = {'configFile': False, 'root': str(ROOT / 'frontend'), 'base': '/ptt/',
                    'preview': {'host': '127.0.0.1', 'port': urlparse(args.url).port, 'strictPort': True}}
                preview_js = ('const c=' + json.dumps(config) + ';c.preview.proxy={"/ptt/api":{target:'
                    + json.dumps(args.api[:-4]) + ',rewrite:p=>p.slice(4)}};import('
                    + json.dumps((ROOT / 'frontend/node_modules/vite/dist/node/index.js').as_uri())
                    + ').then(v=>v.preview(c));')
                processes.append(subprocess.Popen([shutil.which('node'), '-e', preview_js],
                    cwd=ROOT / 'frontend', stdout=preview_log, stderr=subprocess.STDOUT, creationflags=flags))
                for address in (args.api + '/health', args.url):
                    deadline = time.monotonic() + 30
                    while True:
                        try:
                            with urlopen(address, timeout=1): break
                        except Exception:
                            assert time.monotonic() < deadline, 'Owned server did not start: ' + address
                            time.sleep(.2)
                assert all(process.poll() is None for process in processes), 'Owned server exited; check log'
                verify(args, directory, seed_xy(args.api, directory))
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                        try: process.wait(timeout=10)
                        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
    print('Owned backend, preview, profile and isolated datasets cleaned up.', flush=True)


if __name__ == '__main__':
    main()
