"""Isolated Uploads preprocessing browser verification with native numerical checks.

Prepare before starting servers (Python 3.13 executes the scientific fixture):
  python -X utf8 scripts/verify_preprocess.py --prepare --data-dir <empty-temp-dir>
Start backend with KIHA_DATA_DIR=<same-dir>, then run against its frontend:
  python -X utf8 scripts/verify_preprocess.py --url <frontend> --api <api-base> --data-dir <same-dir>
Repeat those server arguments with --followup for read-only plot/desktop checks,
then --lifecycle for one-shot native failure/retry and dropped-response cases.
The fixture marker, catalog and source fingerprints are checked before any write.
No existing user recordings are used or modified. Servers are owned by the caller.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from verify_browser_zoom import capture_browser_view, set_browser_zoom

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'preprocess_source'
OUTPUT = 'preprocess_filtered'
MARKER = 'preprocess-browser-fixture.json'
SELECTED = ['thrust_n', 'torque_nm', 'rpm']


def fingerprints(folder):
    # Ordinary analysis requests may populate this derived cache. Source data,
    # definitions, provenance and pyramid artifacts must remain byte-identical.
    return {str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(folder.rglob('*')) if path.is_file() and path.name != 'tp_stats.json'}


def prepare(dataset, python):
    dataset = dataset.resolve()
    assert dataset != (ROOT / 'data').resolve(), 'Never prepare inside the user data directory'
    dataset.mkdir(parents=True, exist_ok=True)
    assert not list(dataset.iterdir()), 'Fixture destination must be empty'
    generator = r'''import csv, json, math, os
from pathlib import Path
import numpy as np
from scipy import signal
from scipy.ndimage import uniform_filter1d
from app.ingest import ingest_csv
from app.analysis_sources import catalog
from app.store import write_json_atomic

root = Path(os.environ['KIHA_DATA_DIR'])
name = 'preprocess_source'
columns = ['time_s', 'thrust_n', 'torque_nm', 'rpm'] + ['sensor_%02d_temperature_c' % i for i in range(30)]
path = root / 'fixture-original.csv'
with path.open('w', newline='', encoding='utf-8') as stream:
    writer = csv.writer(stream); writer.writerow(columns)
    for i in range(1200):
        t = i / 200
        writer.writerow([t, 10 + math.sin(2*math.pi*2*t) + .6*math.cos(2*math.pi*45*t),
            3 + math.sin(t) + .3*math.cos(2*math.pi*35*t), 1500 + 30*t + math.cos(2*math.pi*3*t)] +
            [20 + j + t*.01 for j in range(30)])
meta = ingest_csv(path, name, copy_raw=True, time_mode='column', time_column='time_s')
folder = root / 'tests' / name
points = {'version':1, 'test':name, 'test_points':[
    {'id':2,'name':'Steady alpha','label':'nominal','start_s':1,'end_s':2,'start_idx':200,'end_idx':400,'notes':'Preserve A'},
    {'id':7,'name':'Steady beta','label':'loaded','start_s':3,'end_s':4.5,'start_idx':600,'end_idx':900,'notes':'Preserve B'}]}
write_json_atomic(folder/'testpoints.json', points)
source = catalog()['sources'][0]
with path.open(newline='', encoding='utf-8') as stream: rows=list(csv.DictReader(stream))
values = {key: np.array([float(row[key]) for row in rows]) for key in columns}
expected = {
    'thrust_n':signal.sosfiltfilt(signal.butter(4, 8, btype='lowpass', fs=200, output='sos'), values['thrust_n']).tolist(),
    'torque_nm':uniform_filter1d(values['torque_nm'], size=5, mode='nearest').tolist(),
    'rpm':signal.detrend(values['rpm'], type='linear').tolist()}
(root/'expected.json').write_text(json.dumps({'columns':columns,'rows':1200,'source':source,'values':expected,'points':points}),encoding='utf-8')
'''
    env = {**os.environ, 'KIHA_DATA_DIR': str(dataset), 'PYTHONPATH': str(ROOT / 'backend')}
    subprocess.run([str(python), '-B', '-c', generator], cwd=ROOT / 'backend', env=env, check=True)
    marker = {'version': 1, 'source': SOURCE, 'dataset': str(dataset),
              'fingerprints': fingerprints(dataset / 'tests' / SOURCE)}
    (dataset / MARKER).write_text(json.dumps(marker, indent=2), encoding='utf-8')
    print(f'Prepared isolated fixture: {dataset}', flush=True)


def get_json(api, path):
    with urlopen(api.rstrip('/') + '/' + path.lstrip('/'), timeout=15) as response:
        return json.load(response)


def read_csv(content):
    return list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))


def verify_csv(data, expected, original):
    rows = read_csv(data)
    originals = read_csv(original)
    assert len(rows) == expected['rows'] == len(originals)
    assert list(rows[0]) == expected['columns']
    for index, (row, raw) in enumerate(zip(rows, originals)):
        for column in expected['columns']:
            want = expected['values'][column][index] if column in SELECTED else float(raw[column])
            got = float(row[column])
            assert math.isclose(got, want, rel_tol=2e-10, abs_tol=2e-10), (index, column, got, want)
    assert any(abs(float(row['thrust_n']) - float(raw['thrust_n'])) > .1
               for row, raw in zip(rows, originals))


def verify(args):
    dataset = args.data_dir.resolve()
    marker = json.loads((dataset / MARKER).read_text(encoding='utf-8'))
    expected = json.loads((dataset / 'expected.json').read_text(encoding='utf-8'))
    assert marker['dataset'] == str(dataset) and marker['source'] == SOURCE
    assert fingerprints(dataset / 'tests' / SOURCE) == marker['fingerprints'], 'Fixture already changed'
    catalog = get_json(args.api, '/analysis-sources')['sources']
    assert [item['name'] for item in catalog] == [SOURCE], 'Use a fresh isolated fixture catalog'
    assert catalog[0]['id'] == expected['source']['id'], 'Backend must use the prepared fixture'
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    original = (dataset / 'tests' / SOURCE / 'raw.csv').read_bytes()
    report = {'checks': [], 'pageErrors': [], 'consoleErrors': [], 'posts': [], 'blockedWrites': []}

    def passed(label):
        report['checks'].append(label)
        print('PASS:', label, flush=True)

    with tempfile.TemporaryDirectory(prefix='ptt-preprocess-browser-') as temporary, sync_playwright() as p:
        temporary = Path(temporary)
        extension = temporary / 'extension'; extension.mkdir()
        (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Preprocessing browser verification',
            'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}), encoding='utf-8')
        (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});', encoding='utf-8')
        context = p.chromium.launch_persistent_context(str(temporary / 'profile'), channel='chromium', headless=True,
            no_viewport=True, accept_downloads=True, args=[f'--disable-extensions-except={extension}',
            f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]; page.set_default_timeout(20000)
        page.on('pageerror', lambda e: report['pageErrors'].append(str(e)))
        page.on('console', lambda e: report['consoleErrors'].append(e.text) if e.type == 'error' else None)
        injected = {'post_error': False, 'snapshot_error': False, 'status_error': False,
                    'hold_post': False, 'held': None, 'failed_job': None}

        def route_api(route):
            req = route.request; parsed = urlparse(req.url)
            path = unquote(parsed.path).split('/api', 1)[-1]
            if req.method == 'GET' and path == '/tests' and injected['status_error']:
                route.fulfill(status=503, json={'detail': 'Fixture status polling temporarily unavailable'})
                return
            if req.method == 'GET' and path == '/tests' and injected['failed_job']:
                response = route.fetch()
                route.fulfill(response=response, json=[*response.json(), injected['failed_job']])
                return
            if injected['snapshot_error'] and req.method == 'GET' and path == f'/tests/{SOURCE}/preprocess':
                route.fulfill(status=503, json={'detail': 'Fixture source check temporarily unavailable'})
                return
            if req.method not in ('GET', 'HEAD', 'OPTIONS'):
                if req.method != 'POST' or path != f'/tests/{SOURCE}/preprocess':
                    report['blockedWrites'].append({'method': req.method, 'url': req.url})
                    route.abort(); return
                payload = req.post_data_json
                assert payload['name'] in (OUTPUT, 'preprocess_failure_fixture') and payload['source_id'] == expected['source']['id']
                report['posts'].append(payload)
                if payload['name'] == 'preprocess_failure_fixture':
                    injected['failed_job'] = {'name': payload['name'], 'status': 'error',
                        'error': 'Fixture native worker failure', 'preprocessing': {
                            'source': expected['source'], 'filters': payload['filters']}}
                    route.fulfill(status=202, json={'name': payload['name'], 'status': 'rebuilding'})
                    return
                if injected['post_error']:
                    injected['post_error'] = False
                    route.fulfill(status=409, json={'detail': 'Fixture conflict; retry the retained recipe'})
                    return
                if injected['hold_post']:
                    injected['hold_post'] = False; injected['held'] = route
                    return
            route.continue_()

        context.route('**/api/**', route_api)
        page.add_init_script("""if(!sessionStorage.getItem('preprocess-seeded')) {
            sessionStorage.setItem('preprocess-seeded','1');localStorage.clear();
            localStorage.setItem('ptt.theme.v1','light');
            localStorage.setItem('ptt.settings.v1',JSON.stringify({scatterX:'rpm',scatterY:'thrust_n',clustering:false,gridColumns:['thrust_n']}));
        }""")
        cdp = context.new_cdp_session(page)
        try:
            page.goto(args.url); page.wait_for_load_state('networkidle')
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            window = cdp.send('Browser.getWindowForTarget')['windowId']
            baseline = page.evaluate('devicePixelRatio')
            page.get_by_role('navigation', name='Main navigation').get_by_role('button', name='Uploads', exact=True).click()
            opener = page.get_by_role('button', name='Pre-process '+SOURCE, exact=True)
            opener.focus(); page.keyboard.press('Enter')
            dialog = page.get_by_role('dialog', name='Pre-process test', exact=True)
            expect(dialog).to_be_visible()
            expect(dialog.get_by_role('textbox', name='Filtered test name', exact=True)).to_be_visible()
            save = dialog.get_by_role('button', name='Save filtered copy', exact=True)
            expect(save).to_be_disabled()
            page.keyboard.press('Escape'); expect(dialog).not_to_be_visible(); expect(opener).to_be_focused()
            passed('per-test keyboard opener, Escape, focus return and empty recipe guard')

            injected['snapshot_error'] = True
            opener.click(); expect(dialog).to_be_visible()
            expect(dialog.get_by_text('Fixture source check temporarily unavailable', exact=False)).to_be_visible()
            injected['snapshot_error'] = False
            dialog.get_by_role('button', name='Retry loading', exact=True).click()
            search = dialog.get_by_role('searchbox', name='Search parameters', exact=True)
            expect(search).to_be_visible()
            passed('source snapshot failure and loading retry')
            search.fill('torque')
            expect(dialog.get_by_role('button', name='Configure torque_nm', exact=True)).to_be_visible()
            expect(dialog.get_by_role('button', name='Configure thrust_n', exact=True)).to_have_count(0)
            search.fill('no_such_parameter')
            expect(dialog.get_by_role('button', name=re.compile('^Configure '))).to_have_count(0)
            search.fill('')
            expect(dialog.get_by_role('button', name='Configure time_s', exact=True)).to_have_count(0)

            def configure(column, kind, **fields):
                search.fill(column)
                dialog.get_by_role('button', name='Configure '+column, exact=True).click()
                dialog.get_by_role('combobox', name='Filter', exact=True).select_option(kind)
                for field, value in fields.items():
                    dialog.get_by_role('textbox', name=field, exact=True).fill(str(value))
                search.fill('')

            configure('thrust_n', 'lowpass', **{'Order': 4, 'Cutoff (Hz)': 100})
            expect(save).to_be_disabled()
            dialog.get_by_role('textbox', name='Cutoff (Hz)', exact=True).fill('8')
            expect(save).to_be_enabled()
            name = dialog.get_by_role('textbox', name='Filtered test name', exact=True)
            for invalid in ('', '../bad', 'bad name', SOURCE):
                name.fill(invalid); expect(save).to_be_disabled()
            name.fill(OUTPUT)
            configure('torque_nm', 'moving_avg', **{'Window (s)': .025})
            configure('rpm', 'detrend')
            dialog.get_by_role('checkbox', name='Configured only', exact=True).check()
            expect(dialog.get_by_role('button', name=re.compile('^Configure '))).to_have_count(3)
            dialog.get_by_role('checkbox', name='Configured only', exact=True).uncheck()
            dialog.get_by_role('button', name='Configure thrust_n', exact=True).click()
            expect(dialog.get_by_role('combobox', name='Filter', exact=True)).to_have_value('lowpass')
            expect(dialog.get_by_role('textbox', name='Cutoff (Hz)', exact=True)).to_have_value('8')
            dialog.get_by_role('button', name='Clear all filters', exact=True).click()
            expect(save).to_be_disabled()
            configure('thrust_n', 'lowpass', **{'Order': 4, 'Cutoff (Hz)': 8})
            configure('torque_nm', 'moving_avg', **{'Window (s)': .025})
            configure('rpm', 'detrend')
            dialog.get_by_role('button', name='Configure thrust_n', exact=True).click()
            passed('parameter search, time exclusion, name/Nyquist validation, retained per-parameter recipe and clear all')

            for theme in ('light', 'dark'):
                if (page.get_by_role('switch', name='Dark mode').get_attribute('aria-checked') == 'true') != (theme == 'dark'):
                    # Native dialog intentionally blocks the background theme switch.
                    page.evaluate("theme=>{document.documentElement.dataset.theme=theme;localStorage.setItem('ptt.theme.v1',theme)}", theme)
                for width, height, zoom in ((1440, 1000, 1), (1440, 1000, 1.25), (1440, 1000, 1.5), (1050, 700, 1), (1050, 700, 1.5)):
                    cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': height}})
                    set_browser_zoom(worker, page, zoom)
                    page.wait_for_function('dpr=>Math.abs(devicePixelRatio-dpr)<.01', arg=baseline*zoom)
                    expect(save).to_be_in_viewport()
                    assert dialog.evaluate('''d=>{const r=d.getBoundingClientRect();return r.left>=-1&&r.top>=-1&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1&&d.scrollWidth<=d.clientWidth+1}'''), (theme, width, height, zoom)
                    assert dialog.evaluate('''d=>[...d.querySelectorAll('input,select,button')].filter(e=>e.getClientRects().length).every(e=>{const r=e.getBoundingClientRect(),a=d.getBoundingClientRect();return r.width>0&&r.left>=a.left-1&&r.right<=a.right+1})'''), (theme, width, height, zoom)
                    capture_browser_view(cdp, output/f'dialog-{theme}-{width}-{zoom}.png')
            passed('light/dark modal bounds, visible footer and real 100/125/150% zoom across desktop sizes')
            cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': 1440, 'height': 1000}})
            set_browser_zoom(worker, page, 1)

            injected['post_error'] = True
            save.click()
            expect(dialog.get_by_text('Fixture conflict; retry the retained recipe', exact=False)).to_be_visible()
            expect(name).to_have_value(OUTPUT); expect(save).to_be_enabled()
            assert len(report['posts']) == 1
            passed('submission failure keeps recipe and destination for retry')
            injected['hold_post'] = True
            save.click()
            for _ in range(200):
                if injected['held']: break
                page.wait_for_timeout(20)
            assert injected['held'] is not None
            expect(name).to_be_disabled()
            page.keyboard.press('Enter')
            assert len(report['posts']) == 2, 'Duplicate submission while pending'
            injected['status_error'] = True
            injected['held'].continue_(); injected['held'] = None
            refresh = dialog.get_by_role('button', name='Refresh status', exact=True)
            expect(refresh).to_be_visible()
            expect(dialog.get_by_text('Creating filtered copy', exact=True)).to_be_visible()
            injected['status_error'] = False
            refresh.click()
            analyze = dialog.get_by_role('button', name='Analyze filtered test', exact=True)
            expect(analyze).to_be_visible(timeout=60000)
            passed('pending duplicate-submit guard, status polling failure/retry and native background completion')

            child = get_json(args.api, f'/tests/{OUTPUT}')
            assert child['n_rows'] == expected['rows'] and child['columns'] == expected['columns']
            recipe = child['preprocessing']
            assert recipe['method'] == 'whole_native_recording' and recipe['source']['id'] == expected['source']['id']
            assert {entry['column'] for entry in recipe['filters']} == set(SELECTED)
            points = get_json(args.api, f'/tests/{OUTPUT}/testpoints')
            assert points['test'] == OUTPUT and points['test_points'] == expected['points']['test_points']
            with urlopen(args.api.rstrip('/')+f'/tests/{OUTPUT}/export', timeout=15) as response:
                verify_csv(response.read(), expected, original)
            identities = get_json(args.api, '/analysis-sources')['sources']
            assert len({item['id'] for item in identities}) == 2
            assert fingerprints(dataset/'tests'/SOURCE) == marker['fingerprints']
            passed('complete native SciPy parity, unchanged time/unselected columns, copied testpoints, unique identity and unchanged source hashes')

            # Continue below once the saved-details and download controls are rendered.
            dialog.get_by_role('button', name='Close pre-process', exact=True).click()
            details_button = page.get_by_role('button', name='View preprocessing for '+OUTPUT, exact=True)
            expect(details_button).to_be_visible(); details_button.click()
            details = page.get_by_role('dialog', name='Pre-processing details', exact=True)
            expect(details).to_be_visible()
            expect(details.get_by_role('button', name='Save filtered copy', exact=True)).to_have_count(0)
            for label, check in [('Filtered CSV', lambda content: verify_csv(content, expected, original)),
                                 ('Original uploaded CSV', lambda content: content == original)]:
                with page.expect_download() as download_info:
                    details.get_by_role('link', name=label, exact=True).click()
                download = download_info.value
                target = output/(label.lower().replace(' ', '-')+'.csv'); download.save_as(target)
                result = check(target.read_bytes())
                assert result is not False, label
            page.keyboard.press('Escape'); expect(details).not_to_be_visible(); expect(details_button).to_be_focused()
            passed('saved recipe details, explicit original/filtered downloads and source byte preservation')

            opener.click(); expect(dialog).to_be_visible()
            configure('thrust_n', 'lowpass', **{'Order': 4, 'Cutoff (Hz)': 8})
            dialog.get_by_role('textbox', name='Filtered test name', exact=True).fill('preprocess_failure_fixture')
            dialog.get_by_role('button', name='Save filtered copy', exact=True).click()
            expect(dialog.get_by_text('Filtered copy failed', exact=True)).to_be_visible()
            expect(dialog.get_by_text('Fixture native worker failure', exact=False)).to_be_visible()
            dialog.get_by_role('button', name='Retry with a new copy', exact=True).click()
            expect(dialog.get_by_role('button', name='Save filtered copy', exact=True)).to_be_enabled()
            expect(dialog.get_by_role('combobox', name='Filter', exact=True)).to_have_value('lowpass')
            assert dialog.get_by_role('textbox', name='Filtered test name', exact=True).input_value() != 'preprocess_failure_fixture'
            page.keyboard.press('Escape')
            injected['failed_job'] = None
            passed('injected worker error is visible and retry retains filters with a fresh copy name')

            page.reload(); page.wait_for_load_state('networkidle')
            page.get_by_role('navigation', name='Main navigation').get_by_role('button', name='Uploads', exact=True).click()
            expect(page.get_by_role('button', name='Pre-process '+SOURCE, exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='View preprocessing for '+OUTPUT, exact=True)).to_be_visible()
            assert fingerprints(dataset/'tests'/SOURCE) == marker['fingerprints']
            assert not report['pageErrors'], report['pageErrors']
            assert not report['blockedWrites'], report['blockedWrites']
            assert all('409' in value or '503' in value for value in report['consoleErrors']), report['consoleErrors']
            passed('original and filtered tests survive reload without changing original files')
        except Exception:
            capture_browser_view(cdp, output/'failure.png')
            print(page.locator('body').aria_snapshot()[-14000:], flush=True)
            raise
        finally:
            (output/'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            context.close()
    print(f"PASS {len(report['checks'])} groups. Evidence: {output}", flush=True)


def followup(args):
    """Read-only layering and dense-dialog checks after a successful native run."""
    dataset = args.data_dir.resolve()
    marker = json.loads((dataset / MARKER).read_text(encoding='utf-8'))
    expected = json.loads((dataset / 'expected.json').read_text(encoding='utf-8'))
    assert marker['dataset'] == str(dataset)
    assert fingerprints(dataset/'tests'/SOURCE) == marker['fingerprints']
    saved_before = fingerprints(dataset/'tests'/OUTPUT)
    assert saved_before
    catalog = get_json(args.api, '/analysis-sources')['sources']
    assert {item['name'] for item in catalog} == {SOURCE, OUTPUT}
    report = {'checks': [], 'pageErrors': [], 'blockedWrites': []}
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    base = {'version': 1, 'sources': catalog, 'currentTest': OUTPUT,
        'xAxis': 'rpm', 'yAxis': 'thrust_n', 'axesUserSet': True, 'selections': [],
        'plotConfigs': ['thrust_n'], 'plotsUserEdited': True, 'plotDensity': 'single',
        'scatterCollapsed': True, 'viewMode': 'full', 'fullPlotMode': 'line',
        'fullFlightComparison': {'flights': [{'test': OUTPUT, 'color': '#d55e00', 'hidden': False, 'offset': 0}],
                                 'timeBasis': 'stored'}}

    def passed(label):
        report['checks'].append(label); print('PASS:', label, flush=True)

    with tempfile.TemporaryDirectory(prefix='ptt-preprocess-followup-') as temp, sync_playwright() as p:
        temp = Path(temp); extension = temp/'extension'; extension.mkdir()
        (extension/'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Preprocess dense dialog QA',
            'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
        (extension/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
        context = p.chromium.launch_persistent_context(str(temp/'profile'), channel='chromium', headless=True,
            no_viewport=True, args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}',
                                    '--window-size=1440,1000'])
        page = context.pages[0]; page.set_default_timeout(20000)
        page.on('pageerror', lambda e: report['pageErrors'].append(str(e)))

        def guard(route):
            if route.request.method not in ('GET', 'HEAD', 'OPTIONS'):
                report['blockedWrites'].append(route.request.url); route.abort()
            else:
                route.continue_()
        context.route('**/api/**', guard)
        page.add_init_script("if(!sessionStorage.getItem('followup-seeded')){sessionStorage.setItem('followup-seeded','1');localStorage.clear();localStorage.setItem('ptt.analysis-session.v1',"+json.dumps(json.dumps(base))+");localStorage.setItem('ptt.theme.v1','light');}")
        cdp = context.new_cdp_session(page)
        try:
            page.goto(args.url); page.wait_for_load_state('networkidle')
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            window = cdp.send('Browser.getWindowForTarget')['windowId']
            baseline = page.evaluate('devicePixelRatio')
            plot = page.locator('.analyze-plots-pane [role="group"][aria-label$=" plot"]').first
            expect(plot.locator('.uplot').first).to_be_visible()
            plot.get_by_role('button', name=re.compile('^Plot actions for ')).click()
            page.get_by_role('menuitem', name='Filter settings…', exact=True).click()
            plot_filter = page.get_by_role('dialog', name=re.compile('^Filter settings for '))
            plot_filter.get_by_role('combobox', name='Filter', exact=True).select_option('moving_avg')

            def requested(response):
                parsed = urlparse(response.url)
                query = parse_qs(parsed.query)
                return parsed.path.endswith(f'/tests/{OUTPUT}/filter') and query.get('window_s') == ['0.045']
            with page.expect_response(requested) as response_info:
                plot_filter.get_by_role('textbox', name='Window (s)', exact=True).fill('.045')
            response = response_info.value
            assert response.ok, response.status
            filtered = response.json()
            assert filtered['mode'] == 'raw' and filtered['n_raw'] == expected['rows']
            stored = expected['values']['thrust_n']
            layered = [sum(stored[max(0, min(len(stored)-1, i+offset))] for offset in range(-4, 5))/9
                       for i in range(len(stored))]
            assert len(filtered['series']['thrust_n']) == len(layered)
            for got, want in zip(filtered['series']['thrust_n'], layered):
                assert math.isclose(got, want, rel_tol=1e-6, abs_tol=1e-6), (got, want)
            page.keyboard.press('Escape')
            expect(plot_filter).not_to_be_visible()
            page.wait_for_load_state('networkidle')
            assert fingerprints(dataset/'tests'/OUTPUT) == saved_before
            assert fingerprints(dataset/'tests'/SOURCE) == marker['fingerprints']
            passed('plot filter applies additional numeric processing to the saved filtered test without persisting it')

            page.get_by_role('navigation', name='Main navigation').get_by_role('button', name='Uploads', exact=True).click()
            page.get_by_role('button', name='View preprocessing for '+OUTPUT, exact=True).click()
            details = page.get_by_role('dialog', name='Pre-processing details', exact=True)
            expect(details.get_by_role('region', name='Saved preprocessing recipe')).to_contain_text('Low-pass')
            expect(details.get_by_role('region', name='Saved preprocessing recipe')).to_contain_text('8 Hz')
            page.keyboard.press('Escape')
            passed('saved preprocessing recipe remains distinct from the additional plot filter')

            for theme in ('light', 'dark'):
                switch = page.get_by_role('switch', name='Dark mode', exact=True)
                if (switch.get_attribute('aria-checked') == 'true') != (theme == 'dark'):
                    switch.click()
                page.get_by_role('button', name='Pre-process '+SOURCE, exact=True).click()
                dialog = page.get_by_role('dialog', name='Pre-process test', exact=True)
                expect(dialog.get_by_role('combobox', name='Filter', exact=True)).to_be_visible()
                dialog.get_by_role('combobox', name='Filter', exact=True).select_option('despike')
                for zoom in (1, 1.25, 1.5):
                    cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': 1050, 'height': 700}})
                    set_browser_zoom(worker, page, zoom)
                    page.wait_for_function('dpr=>Math.abs(devicePixelRatio-dpr)<.01', arg=baseline*zoom)
                    for label in ('Window (ms)', 'Max spike (ms)', 'Threshold (MAD)', 'Min jump (units)', 'Filtered test name'):
                        control = dialog.get_by_role('textbox', name=label, exact=True)
                        control.scroll_into_view_if_needed(); expect(control).to_be_in_viewport()
                        control.focus(); expect(control).to_be_focused()
                    expect(dialog.get_by_role('button', name='Save filtered copy', exact=True)).to_be_in_viewport()
                    capture_browser_view(cdp, output/f'dense-scrolled-{theme}-{zoom}.png')
                # Native Escape works while the scroll body holds keyboard focus.
                page.keyboard.press('Escape'); expect(dialog).not_to_be_visible()
                expect(page.get_by_role('button', name='Pre-process '+SOURCE, exact=True)).to_be_focused()
            passed('dense Despike editor, actual theme switch, scrolling and keyboard access in 1050x700 at 100/125/150% zoom')
            assert fingerprints(dataset/'tests'/OUTPUT) == saved_before
            assert fingerprints(dataset/'tests'/SOURCE) == marker['fingerprints']
            assert not report['pageErrors'] and not report['blockedWrites'], report
        except Exception:
            capture_browser_view(cdp, output/'followup-failure.png')
            print(page.locator('body').aria_snapshot()[-12000:], flush=True)
            raise
        finally:
            (output/'followup-results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            context.close()
    print(f"PASS {len(report['checks'])} follow-up groups. Evidence: {output}", flush=True)


def lifecycle(args):
    """Native worker failure/retry and a lost POST response, on marked fixtures only."""
    dataset = args.data_dir.resolve()
    marker = json.loads((dataset/MARKER).read_text(encoding='utf-8'))
    assert marker['dataset'] == str(dataset)
    assert fingerprints(dataset/'tests'/SOURCE) == marker['fingerprints']
    catalog = get_json(args.api, '/analysis-sources')['sources']
    assert {item['name'] for item in catalog} == {SOURCE, OUTPUT}, 'Run lifecycle only once after the main verifier'
    gap_source, failure, retry, lost = 'preprocess_gapped', 'preprocess_failed_native', 'preprocess_retried_native', 'preprocess_lost_response'
    generator = r'''import csv, math, os
from pathlib import Path
from app.ingest import ingest_csv
from app.analysis_sources import catalog
root=Path(os.environ['KIHA_DATA_DIR'])
path=root/'gapped-original.csv'
with path.open('w',newline='',encoding='utf-8') as stream:
    w=csv.writer(stream);w.writerow(['time_s','thrust_n'])
    for i in range(1200):
        t=i/200 if i<10 else 1+(i-10)/200
        w.writerow([t,10+math.sin(t)])
meta=ingest_csv(path,'preprocess_gapped',copy_raw=True,time_mode='column',time_column='time_s')
assert meta['time_gap_ranges'] and meta['time_gap_ranges'][0][0]==10
catalog()
'''
    subprocess.run([str(args.python), '-B', '-c', generator], cwd=ROOT/'backend', check=True,
        env={**os.environ, 'KIHA_DATA_DIR': str(dataset), 'PYTHONPATH': str(ROOT/'backend')})
    gap_before = fingerprints(dataset/'tests'/gap_source)
    source_ids = {item['name']: item['id'] for item in get_json(args.api, '/analysis-sources')['sources']}
    report = {'checks': [], 'posts': [], 'pageErrors': [], 'blockedWrites': []}
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)

    def passed(label):
        report['checks'].append(label); print('PASS:', label, flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chromium', headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 950})
        page = context.new_page(); page.set_default_timeout(20000)
        page.on('pageerror', lambda e: report['pageErrors'].append(str(e)))

        def guard(route):
            request = route.request; path = unquote(urlparse(request.url).path)
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                payload = request.post_data_json
                source = gap_source if payload.get('name') in (failure, retry) else SOURCE
                if request.method != 'POST' or not path.endswith(f'/tests/{source}/preprocess') or payload.get('name') not in (failure, retry, lost):
                    report['blockedWrites'].append(request.url); route.abort(); return
                assert payload['source_id'] == source_ids[source]
                report['posts'].append(payload)
                if payload['name'] == lost:
                    response = route.fetch()
                    assert response.status == 202, response.text()
                    # The server accepted and persisted the job, but this browser
                    # receives a dropped response. It must recover the same copy.
                    route.abort('failed'); return
            route.continue_()
        context.route('**/api/**', guard)
        try:
            page.goto(args.url); page.wait_for_load_state('networkidle')
            page.get_by_role('navigation', name='Main navigation').get_by_role('button', name='Uploads', exact=True).click()
            page.get_by_role('button', name='Pre-process '+gap_source, exact=True).click()
            dialog = page.get_by_role('dialog', name='Pre-process test', exact=True)
            select = dialog.get_by_role('combobox', name='Filter', exact=True)
            select.select_option('lowpass')
            dialog.get_by_role('textbox', name='Order', exact=True).fill('4')
            dialog.get_by_role('textbox', name='Cutoff (Hz)', exact=True).fill('8')
            dialog.get_by_role('textbox', name='Filtered test name', exact=True).fill(failure)
            dialog.get_by_role('button', name='Save filtered copy', exact=True).click()
            expect(dialog.get_by_text('Filtered copy failed', exact=True)).to_be_visible(timeout=60000)
            expect(dialog.get_by_role('alert')).to_contain_text('too short')
            failed = next(item for item in get_json(args.api, '/tests') if item['name'] == failure)
            assert failed['status'] == 'error'
            assert not (dataset/'tests'/failure/'data.parquet').exists()
            expect(page.locator(f'[aria-label="Download original CSV for {failure}"]')).to_have_count(0)
            expect(page.locator(f'[aria-label="Download original CSV for {gap_source}"]')).to_have_count(1)
            assert fingerprints(dataset/'tests'/gap_source) == gap_before
            passed('real background worker rejects a too-short acquisition segment and publishes an error without partial data')

            dialog.get_by_role('button', name='Retry with a new copy', exact=True).click()
            select.select_option('moving_avg')
            dialog.get_by_role('textbox', name='Window (s)', exact=True).fill('.005')
            dialog.get_by_role('textbox', name='Filtered test name', exact=True).fill(retry)
            dialog.get_by_role('button', name='Save filtered copy', exact=True).click()
            expect(dialog.get_by_role('button', name='Analyze filtered test', exact=True)).to_be_visible(timeout=60000)
            with urlopen(args.api.rstrip('/')+f'/tests/{gap_source}/export') as response:
                source_csv = response.read()
            with urlopen(args.api.rstrip('/')+f'/tests/{retry}/export') as response:
                retry_csv = response.read()
            assert source_csv == retry_csv, 'One-sample averaging preserves every native row and known gap'
            assert fingerprints(dataset/'tests'/gap_source) == gap_before
            assert next(item for item in get_json(args.api, '/tests') if item['name'] == failure)['status'] == 'error'
            page.screenshot(path=str(output/'native-retry-complete.png'))
            page.keyboard.press('Escape')
            passed('native failure retries into a new successful copy while preserving gap rows and the failed record')

            page.get_by_role('button', name='Pre-process '+SOURCE, exact=True).click()
            select.select_option('lowpass')
            dialog.get_by_role('textbox', name='Order', exact=True).fill('4')
            dialog.get_by_role('textbox', name='Cutoff (Hz)', exact=True).fill('8')
            dialog.get_by_role('textbox', name='Filtered test name', exact=True).fill(lost)
            dialog.get_by_role('button', name='Save filtered copy', exact=True).click()
            expect(dialog.get_by_role('button', name='Analyze filtered test', exact=True)).to_be_visible(timeout=60000)
            assert sum(item['name'] == lost for item in report['posts']) == 1
            matches = [item for item in get_json(args.api, '/tests') if item['name'] == lost]
            assert len(matches) == 1 and matches[0]['status'] == 'ready'
            assert fingerprints(dataset/'tests'/SOURCE) == marker['fingerprints']
            assert fingerprints(dataset/'tests'/gap_source) == gap_before
            assert not report['pageErrors'] and not report['blockedWrites'], report
            page.screenshot(path=str(output/'lost-response-recovered.png'))
            passed('dropped successful POST response recovers the matching saved recipe with one submission and one output')
        except Exception:
            page.screenshot(path=str(output/'lifecycle-failure.png'))
            print(page.locator('body').aria_snapshot()[-14000:], flush=True)
            raise
        finally:
            (output/'lifecycle-results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            browser.close()
    print(f"PASS {len(report['checks'])} lifecycle groups. Evidence: {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--followup', action='store_true', help='Read-only plot-layering and dense-dialog checks after the main verifier')
    parser.add_argument('--lifecycle', action='store_true', help='One-shot native failed-worker/retry and dropped-response cases on the same disposable fixture')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--python', type=Path, default=ROOT/'backend/.venv/Scripts/python.exe')
    parser.add_argument('--url', default='http://127.0.0.1:8097/ptt/')
    parser.add_argument('--api', default='http://127.0.0.1:8098/api')
    parser.add_argument('--output', type=Path, default=Path(tempfile.gettempdir())/'ptt-preprocess-verification')
    args = parser.parse_args()
    if args.prepare:
        prepare(args.data_dir, args.python)
    elif args.followup:
        followup(args)
    elif args.lifecycle:
        lifecycle(args)
    else:
        verify(args)


if __name__ == '__main__':
    main()
