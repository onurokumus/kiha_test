"""Native read-only original/filtered comparison checks on disposable recordings.

Run after the production frontend build:
  python -X utf8 scripts/verify_preprocess_comparison.py
Reuses the same-flight verifier's native fixture preparation and guarded backend.
Owns hidden servers, a private browser profile and marked temporary datasets.
Browser writes fail closed; only fixture setup submits preprocessing requests.
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
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from verify_preprocess_inplace import (ROOT, FLIGHT, DATASHEET, GAPPED, MARKER,
                                      prepare, serve, get, hashes)

LARGE = 'b_comparison_large'
LEGACY = 'y_comparison_legacy'


def prepare_comparison(directory, python):
    prepare(directory, python)
    generator = r'''import csv,json,math,os
from pathlib import Path
import numpy as np
from scipy import signal
from app.ingest import ingest_csv
from app.analysis_sources import catalog
root=Path(os.environ['KIHA_DATA_DIR'])
path=root/'b_comparison_large.csv'
with path.open('w',newline='',encoding='utf-8') as stream:
    writer=csv.writer(stream);writer.writerow(['time_s','thrust_n','rpm'])
    for i in range(24000):
        t=i/200
        writer.writerow([t,10+math.sin(2*math.pi*2*t)+.6*math.cos(2*math.pi*45*t),1500+30*t])
ingest_csv(path,'b_comparison_large',copy_raw=True,time_mode='column',time_column='time_s')
ingest_csv(root/'a_inplace_flight.csv','y_comparison_legacy',copy_raw=True,time_mode='column',time_column='time_s')
legacy_meta=root/'tests/y_comparison_legacy/meta.json'
meta=json.loads(legacy_meta.read_text(encoding='utf-8'))
meta['preprocessing']={'version':1,'source':{'name':'unavailable-original'},'filters':[],
    'warnings':[],'created_at':'2026-10-09T10:00:00Z','completed_at':'2026-10-09T10:00:00Z'}
legacy_meta.write_text(json.dumps(meta),encoding='utf-8')
with path.open(newline='',encoding='utf-8') as stream: rows=list(csv.DictReader(stream))
original=np.array([float(row['thrust_n']) for row in rows])
expected=json.loads((root/'expected.json').read_text(encoding='utf-8'))
expected['large']={'rows':24000,'original':original.tolist(),
    'low8':signal.sosfiltfilt(signal.butter(4,8,btype='lowpass',fs=200,output='sos'),original).tolist()}
expected['catalog']=catalog()['sources']
(root/'expected.json').write_text(json.dumps(expected),encoding='utf-8')
'''
    subprocess.run([str(python), '-B', '-c', generator], check=True, cwd=ROOT / 'backend',
                   env={**os.environ, 'KIHA_DATA_DIR': str(directory), 'PYTHONPATH': str(ROOT / 'backend')})
    marker = json.loads((directory / MARKER).read_text(encoding='utf-8'))
    marker['hashes'][LARGE] = hashes(directory / 'tests' / LARGE)
    marker['hashes'][LEGACY] = hashes(directory / 'tests' / LEGACY)
    (directory / MARKER).write_text(json.dumps(marker), encoding='utf-8')


def seed_saved_filters(api, directory):
    marker = json.loads((directory / MARKER).read_text(encoding='utf-8'))
    assert marker['dataset'] == str(directory.resolve())
    for name in (FLIGHT, LARGE):
        assert hashes(directory / 'tests' / name) == marker['hashes'][name]
        before = get(api, f'/tests/{name}/preprocess')
        payload = {'request_id': str(uuid4()), 'source_id': before['source']['id'],
                   'source_revision': before['source']['revision'],
                   'filters': [{'column': 'thrust_n', 'filter': {'kind': 'lowpass', 'order': 4, 'f1': 8}}]}
        request = Request(api + f'/tests/{name}/preprocess', data=json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json'}, method='POST')
        with urlopen(request, timeout=15) as response: assert response.status == 202
        deadline = time.monotonic() + 60
        while True:
            status = next(test for test in get(api, '/tests') if test['name'] == name)
            operation = status.get('preprocessing_operation', {})
            if operation.get('state') == 'completed': break
            assert operation.get('state') != 'failed', operation
            assert time.monotonic() < deadline, status
            time.sleep(.1)
    return {name: hashes(directory / 'tests' / name) for name in marker['hashes']}


def check_response(body, expected):
    """Independent numerical oracle for paired samples and returned summaries."""
    assert body['source']['name'] in (FLIGHT, LARGE)
    assert len(body['t']) > 0
    assert body['column'] in ('thrust_n', 'rpm', 'torque_nm')
    large = body['source']['name'] == LARGE
    original = (expected['large']['original'] if large else expected['original']['thrust_n'])
    filtered = expected['large']['low8'] if large else expected['low8']
    if body['column'] != 'thrust_n':
        original = expected['original'][body['column']]
        filtered = original
    if body['mode'] == 'raw':
        assert body['level'] == 1 and len(body['t']) == len(body['original']) == len(body['filtered'])
        pairs = []
        for t, before, after in zip(body['t'], body['original'], body['filtered']):
            index = round(t * 200)
            assert math.isclose(before, original[index], rel_tol=0, abs_tol=5.1e-7), (index, before, original[index])
            assert math.isclose(after, filtered[index], rel_tol=0, abs_tol=5.1e-7), (index, after, filtered[index])
            pairs.append(after - before)
        summary = body['summary']
        assert summary['finite_pairs'] == len(pairs) and summary['missing_pairs'] == 0
        # Statistics use full precision; display traces may be rounded.
        full_deltas = [filtered[round(t * 200)] - original[round(t * 200)] for t in body['t']]
        assert math.isclose(summary['rms_difference'], math.sqrt(sum(d*d for d in full_deltas)/len(full_deltas)), abs_tol=1e-8)
        assert math.isclose(summary['max_abs_difference'], max(abs(d) for d in full_deltas), abs_tol=1e-8)
    else:
        assert body['mode'] == 'envelope' and body['level'] > 1 and body['summary'] is None
        for key, values in [('original', original), ('filtered', filtered)]:
            assert set(body[key]) == {'min', 'max'}
            assert len(body[key]['min']) == len(body['t']) == len(body[key]['max'])
            # Each pyramid bin retains the native extremes instead of striding.
            for t, low, high in zip(body['t'], body[key]['min'], body[key]['max']):
                center = round(t * 200)
                start = (center // body['level']) * body['level']
                window = values[start:start + body['level']]
                assert math.isclose(low, min(window), abs_tol=5.1e-7), (key, t, low, min(window))
                assert math.isclose(high, max(window), abs_tol=5.1e-7), (key, t, high, max(window))


def verify(args, directory, initial_hashes):
    from playwright.sync_api import expect, sync_playwright
    from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout
    expected = json.loads((directory / 'expected.json').read_text(encoding='utf-8'))
    report = {'checks': [], 'requests': [], 'comparisons': [], 'pageErrors': [],
              'consoleErrors': [], 'blockedWrites': [], 'layouts': []}
    output = args.output.resolve()
    inject = {'error': None, 'hold': None}
    delayed = []

    def passed(label):
        report['checks'].append(label); print('PASS:', label, flush=True)

    with tempfile.TemporaryDirectory(prefix='ptt-comparison-profile-') as profile, sync_playwright() as p:
        extension = Path(profile) / 'extension'; extension.mkdir()
        (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Preprocessing comparison QA',
            'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
        (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
        context = p.chromium.launch_persistent_context(str(Path(profile) / 'profile'), channel='chromium', headless=True,
            no_viewport=True, args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]; page.set_default_timeout(20000)
        page.on('pageerror', lambda e: report['pageErrors'].append(str(e)))
        page.on('console', lambda e: report['consoleErrors'].append(e.text) if e.type == 'error' else None)

        def route_api(route):
            request = route.request; parsed = urlparse(request.url)
            path = unquote(parsed.path).split('/api', 1)[-1]
            report['requests'].append({'method': request.method, 'path': path, 'query': parsed.query})
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                report['blockedWrites'].append({'method': request.method, 'path': path}); route.abort(); return
            comparison = path.endswith('/preprocess/compare')
            if comparison and inject['error']:
                code, message = inject['error']; route.fulfill(status=code, json={'detail': message}); return
            response = route.fetch(url=args.api + path + ('?' + parsed.query if parsed.query else ''), timeout=60000)
            if comparison and response.ok:
                body = response.json(); check_response(body, expected)
                report['comparisons'].append({'name': body['source']['name'], 'column': body['column'],
                    'mode': body['mode'], 'level': body['level'], 'n_raw': body['n_raw'],
                    'first': body['t'][0], 'last': body['t'][-1], 'summary': body['summary']})
                if inject['hold'] == body['column']:
                    inject['hold'] = None; delayed.append((route, response)); return
            route.fulfill(response=response)

        context.route('**/api/**', route_api)
        seed = {'scatterX': 'rpm', 'scatterY': 'thrust_n', 'clustering': False, 'gridColumns': ['thrust_n'], 'datasheetZone': DATASHEET}
        page.add_init_script("if(!sessionStorage.getItem('comparison-seeded')){sessionStorage.setItem('comparison-seeded','1');localStorage.clear();"
            + "localStorage.setItem('ptt.theme.v1','light');localStorage.setItem('ptt.settings.v1'," + json.dumps(json.dumps(seed)) + ");}")
        cdp = context.new_cdp_session(page)
        try:
            page.goto(args.url); page.wait_for_load_state('networkidle')
            report['bundle'] = page.locator('script[type="module"][src]').get_attribute('src')
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            window = cdp.send('Browser.getWindowForTarget')['windowId']; dpr = page.evaluate('devicePixelRatio')
            nav = page.get_by_role('navigation', name='Main navigation')
            nav.get_by_role('button', name='Uploads', exact=True).click()
            (output / 'initial-aria.txt').write_text(page.locator('body').aria_snapshot(), encoding='utf-8')
            opener = page.get_by_role('button', name=f'Compare original and filtered data for {FLIGHT}', exact=True)
            for name in (DATASHEET, GAPPED, LEGACY):
                expect(page.get_by_role('button', name=f'Compare original and filtered data for {name}', exact=True)).to_have_count(0)
            opener.focus(); page.keyboard.press('Enter')
            dialog = page.get_by_role('dialog', name='Pre-process flight', exact=True)
            panel = dialog.get_by_role('tabpanel', name='Compare data', exact=True)
            parameter = panel.get_by_role('button', name='Comparison parameter', exact=True)
            plot = panel.get_by_role('region', name='Original and filtered comparison plot', exact=True)
            reset = panel.get_by_role('button', name='Reset comparison zoom', exact=True)

            def settle():
                page.wait_for_load_state('networkidle')
                expect(parameter).to_be_visible()
                expect(plot.locator('.uplot')).to_be_visible()
                wait_for_chart_layout(page)

            def select_parameter(name):
                parameter.click()
                dialog.get_by_role('option', name=re.compile('^' + re.escape(name) + '(?:\\s|$)')).click()
                settle()

            def drag(left, right, button='left', modifier=None):
                over = plot.locator('.u-over'); over.scroll_into_view_if_needed(); bounds = over.bounding_box()
                if modifier: page.keyboard.down(modifier)
                page.mouse.move(bounds['x'] + bounds['width'] * left, bounds['y'] + bounds['height'] * .45)
                page.mouse.down(button=button)
                page.mouse.move(bounds['x'] + bounds['width'] * right, bounds['y'] + bounds['height'] * .6, steps=9)
                page.mouse.up(button=button)
                if modifier: page.keyboard.up(modifier)
                settle()

            def hover_oracle(column='thrust_n'):
                over = plot.locator('.u-over'); over.scroll_into_view_if_needed(); bounds = over.bounding_box()
                page.mouse.move(bounds['x'] + bounds['width'] * .42, bounds['y'] + bounds['height'] * .5)
                readout = panel.locator('[aria-label="Comparison cursor values"]')
                page.wait_for_function('''() => {
                    const el=document.querySelector('[aria-label="Comparison cursor values"]');
                    return el && !el.textContent.includes('Move over the plot') && el.querySelector('strong')?.textContent!=='—';
                }''')
                rows = readout.evaluate('el=>[...el.children].map(n=>({text:n.textContent,value:n.querySelector("strong")?.textContent}))')
                index = round(float(rows[0]['text'].split(' ')[0].replace(',', '')) * 200)
                before = expected['original'][column][index]
                after = expected['low8'][index] if column == 'thrust_n' else before
                for row, value in zip(rows[1:], (before, after, after - before)):
                    assert math.isclose(float(row['value'].replace(',', '')), value, abs_tol=5e-5), (rows, before, after)
                return rows

            settle()
            expect(dialog.get_by_role('tab', name='Compare data', exact=True)).to_have_attribute('aria-selected', 'true')
            assert report['comparisons'][-1]['name'] == FLIGHT and report['comparisons'][-1]['column'] == 'thrust_n'
            assert report['comparisons'][-1]['mode'] == 'raw' and report['comparisons'][-1]['n_raw'] == 1200
            (output / 'comparison-aria.txt').write_text(dialog.aria_snapshot(), encoding='utf-8')
            capture_browser_view(cdp, output / 'native-overlay-light.png')
            report['nativeCursor'] = hover_oracle()
            passed('row Compare opens saved original and filtered native overlays; unavailable rows omit Compare')

            before = len(report['comparisons'])
            trace_group = panel.get_by_role('group', name='Comparison traces', exact=True)
            canvases = []
            for label in ('Original', 'Filtered', 'Both'):
                trace_group.get_by_role('button', name=label, exact=True).click()
                expect(trace_group.get_by_role('button', name=label, exact=True)).to_have_attribute('aria-pressed', 'true')
                settle()
                canvases.append(hashlib.sha256(plot.locator('canvas').first.screenshot()).hexdigest())
            assert len(report['comparisons']) == before, 'Visibility must not refilter or request data'
            assert len(set(canvases)) == 3, 'Each trace visibility mode must change the plotted image'
            select_parameter('rpm')
            assert report['comparisons'][-1]['column'] == 'rpm'
            assert report['comparisons'][-1]['summary']['rms_difference'] == 0
            select_parameter('thrust_n')
            passed('Both, Original and Filtered display choices work; untouched parameters are numerically identical')

            inject['hold'] = 'rpm'
            parameter.click(); dialog.get_by_role('option', name=re.compile('^rpm(?:\\s|$)')).click()
            deadline = time.monotonic() + 5
            while not delayed:
                assert time.monotonic() < deadline, 'Expected held comparison request'; page.wait_for_timeout(25)
            expect(plot.locator('.uplot')).to_have_count(0)
            select_parameter('torque_nm')
            for held_route, held_response in delayed: held_route.fulfill(response=held_response)
            delayed.clear(); settle()
            expect(parameter).to_contain_text('torque_nm')
            hover_oracle('torque_nm')
            select_parameter('thrust_n')
            passed('late parameter response cannot replace a newer selection, and cursor original/filtered/difference values use aligned native samples')

            drag(.2, .55)
            zoom = report['comparisons'][-1]
            assert zoom['n_raw'] < 600
            drag(.45, .6, modifier='Shift')
            assert report['comparisons'][-1]['first'] != zoom['first']
            plot.focus(); page.keyboard.press('Home'); settle()
            assert report['comparisons'][-1]['n_raw'] == 1200
            plot.focus(); page.keyboard.press('+'); settle()
            assert report['comparisons'][-1]['n_raw'] < 1200
            plot.focus(); page.keyboard.press('ArrowRight'); settle()
            reset.click(); settle()
            passed('drag zoom, Shift pan, keyboard zoom/pan/Home and reset refetch aligned native original and filtered windows')

            with page.expect_download() as download:
                panel.get_by_role('button', name='Export comparison PNG', exact=True).click()
            png = output / 'comparison.png'; download.value.save_as(png)
            assert png.read_bytes().startswith(b'\x89PNG\r\n\x1a\n') and png.stat().st_size > 10000
            passed('comparison exports a real PNG without changing the saved flight')

            # Draft filters remain editable, but comparison always reads saved samples.
            dialog.get_by_role('tab', name='Filters', exact=True).click()
            dialog.get_by_role('checkbox', name='Select thrust_n for bulk filtering', exact=True).check()
            select_all = dialog.get_by_role('checkbox', name='Select all parameters', exact=True)
            assert select_all.evaluate('el=>el.indeterminate')
            dialog.get_by_role('tab', name='Compare data', exact=True).click(); settle()
            dialog.get_by_role('tab', name='Filters', exact=True).click()
            assert select_all.evaluate('el=>el.indeterminate'), 'Partial selection must survive tab remount'
            dialog.get_by_role('checkbox', name='Select thrust_n for bulk filtering', exact=True).uncheck()
            dialog.get_by_role('button', name='Configure thrust_n', exact=True).click()
            editor = dialog.get_by_role('region', name='Parameter filter settings', exact=True)
            cutoff = editor.get_by_role('textbox', name='Cutoff (Hz)', exact=True)
            cutoff.fill('4'); cutoff.press('Tab')
            expect(dialog.get_by_role('button', name='Apply preprocessing', exact=True)).to_be_enabled()
            dialog.get_by_role('tab', name='Compare data', exact=True).focus()
            page.keyboard.press('Enter'); settle()
            expect(panel.get_by_text(re.compile('unapplied|unsaved', re.I))).to_be_visible()
            assert report['comparisons'][-1]['summary']['rms_difference'] > 0
            passed('comparison reads the saved 8 Hz result even while an unapplied 4 Hz filter draft exists')

            inject['error'] = (503, 'Fixture comparison temporarily unavailable')
            select_parameter_error = parameter
            select_parameter_error.click(); dialog.get_by_role('option', name=re.compile('^rpm(?:\\s|$)')).click()
            expect(panel.get_by_text('Fixture comparison temporarily unavailable', exact=False)).to_be_visible()
            expect(plot.locator('.uplot')).to_have_count(0)
            inject['error'] = None
            panel.get_by_role('button', name='Retry comparison', exact=True).click(); settle()
            assert report['comparisons'][-1]['column'] == 'rpm'
            inject['error'] = (409, 'This flight changed. Reload saved data before comparing.')
            parameter.click(); dialog.get_by_role('option', name=re.compile('^thrust_n(?:\\s|$)')).click()
            expect(panel.get_by_text('This flight changed.', exact=False)).to_be_visible()
            inject['error'] = None
            panel.get_by_role('button', name='Reload saved data', exact=True).click(); settle()
            dialog.get_by_role('tab', name='Filters', exact=True).click()
            expect(cutoff).to_have_value('4')
            dialog.get_by_role('tab', name='Compare data', exact=True).click(); settle()
            passed('failed and stale reads hide prior traces, retry or reload safely, and preserve unapplied filter edits')

            for theme in ('light', 'dark'):
                page.keyboard.press('Escape'); expect(dialog).not_to_be_visible(); expect(opener).to_be_focused()
                toggle = page.get_by_role('switch', name='Dark mode', exact=True)
                if (toggle.get_attribute('aria-checked') == 'true') != (theme == 'dark'): toggle.click()
                opener.click(); settle()
                for zoom in (1, 1.25, 1.5):
                    width, height = (1440, 1000) if zoom == 1 else (1100, 780)
                    cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': height}})
                    set_browser_zoom(worker, page, zoom)
                    page.wait_for_function('v=>Math.abs(devicePixelRatio-v)<.01', arg=dpr * zoom)
                    settle()
                    for expanded in (False, True):
                        if expanded:
                            expand = dialog.get_by_role('button', name='Expand comparison', exact=True)
                            expand.focus(); page.keyboard.press('Enter'); settle()
                        bounds = dialog.evaluate('''d=>{const r=d.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:innerWidth,height:innerHeight,overflow:d.scrollWidth-d.clientWidth}}''')
                        assert bounds['x'] >= 0 and bounds['y'] >= 0 and bounds['right'] <= bounds['width'] + 1 and bounds['bottom'] <= bounds['height'] + 1 and bounds['overflow'] <= 1, bounds
                        report['layouts'].append({'theme': theme, 'zoom': zoom, 'expanded': expanded, **bounds})
                        parameter.scroll_into_view_if_needed(); parameter.focus(); expect(parameter).to_be_focused(); expect(parameter).to_be_in_viewport()
                        expect(dialog.get_by_role('button', name='Back to filters', exact=True)).to_be_in_viewport()
                        capture_browser_view(cdp, output / f'comparison-{theme}-{zoom}-{expanded}.png')
                        plot.evaluate('el=>el.scrollIntoView({block:"center"})'); plot.focus(); expect(plot).to_be_focused()
                        over_bounds = plot.locator('.u-over').bounding_box()
                        assert over_bounds['width'] >= 250 and over_bounds['height'] >= 75, over_bounds
                        plot_bounds = plot.bounding_box(); panel_bounds = panel.bounding_box()
                        assert plot_bounds['y'] >= panel_bounds['y'] - 1 and plot_bounds['y'] + plot_bounds['height'] <= panel_bounds['y'] + panel_bounds['height'] + 1, (plot_bounds, panel_bounds)
                        capture_browser_view(cdp, output / f'plot-{theme}-{zoom}-{expanded}.png')
                        if expanded:
                            dialog.get_by_role('button', name='Restore comparison size', exact=True).click(); settle()
                set_browser_zoom(worker, page, 1)
                cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': 1440, 'height': 1000}})
            passed('both themes and actual 100/125/150% browser zoom preserve desktop layout, maximize/restore and keyboard access')

            page.keyboard.press('Escape'); expect(dialog).not_to_be_visible()
            page.get_by_role('button', name=f'Compare original and filtered data for {LARGE}', exact=True).click(); settle()
            assert report['comparisons'][-1]['name'] == LARGE and report['comparisons'][-1]['mode'] == 'envelope'
            capture_browser_view(cdp, output / 'large-envelope-dark.png')
            drag(.35, .45)
            assert report['comparisons'][-1]['mode'] == 'raw' and report['comparisons'][-1]['n_raw'] < 6000
            capture_browser_view(cdp, output / 'large-native-zoom-dark.png')
            reset.click(); settle()
            assert report['comparisons'][-1]['mode'] == 'envelope'
            passed('large recordings retain original and filtered min/max envelopes, then fetch native paired samples and exact statistics after zoom')
            assert all(hashes(directory / 'tests' / name) == prior for name, prior in initial_hashes.items())
            assert not report['blockedWrites'] and not report['pageErrors'], report
            assert all(any(code in line for code in ('409', '503')) for line in report['consoleErrors']), report['consoleErrors']
            assert len(get(args.api, '/tests')) == 5
            passed('comparison leaves every stored file unchanged, creates no extra flight and emits no unexpected browser errors or writes')
        except Exception:
            capture_browser_view(cdp, output / 'failure.png')
            state = page.locator('body').aria_snapshot()
            (output / 'failure-aria.txt').write_text(state, encoding='utf-8')
            print(state[-8500:], flush=True)
            raise
        finally:
            (output / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            context.close()
    print(f"PASS {len(report['checks'])} browser groups. Evidence: {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, default=ROOT / 'backend/.venv/Scripts/python.exe')
    parser.add_argument('--api', default='http://127.0.0.1:8128/api')
    parser.add_argument('--url', default='http://127.0.0.1:8127/ptt/')
    parser.add_argument('--output', type=Path, default=Path(tempfile.gettempdir()) / 'ptt-preprocess-comparison-verification')
    parser.add_argument('--serve', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve: serve(args.serve); return
    args.output.mkdir(parents=True, exist_ok=True)
    # Fail rather than reuse any unrelated process occupying the intended ports.
    for port in (urlparse(args.api).port, urlparse(args.url).port):
        with socket.socket() as sock: sock.bind(('127.0.0.1', port))
    processes = []
    with tempfile.TemporaryDirectory(prefix='ptt-comparison-native-') as temporary:
        directory = Path(temporary)
        prepare_comparison(directory, args.python)
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
                initial_hashes = seed_saved_filters(args.api, directory)
                verify(args, directory, initial_hashes)
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                        try: process.wait(timeout=10)
                        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
    print('Owned backend, preview, profile and isolated datasets cleaned up.', flush=True)


if __name__ == '__main__':
    main()
