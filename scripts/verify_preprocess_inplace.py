"""Native, isolated verification of same-flight preprocessing.

Run with global Python (Playwright installed) after `npm run build`:
  python -X utf8 scripts/verify_preprocess_inplace.py
The script owns hidden backend/preview processes, a temporary Chromium profile,
and a marked temporary KIHA_DATA_DIR. Python 3.13 performs scientific work.
All browser API calls are rerouted to the owned backend; writes fail closed.
The fixture-only backend delays a worker while a gate file exists, permitting
deterministic close-while-running checks. No product hooks or user data are used.
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
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
FLIGHT = 'a_inplace_flight'
DATASHEET = 's200_fixture'
GAPPED = 'z_inplace_gapped'
MARKER = 'inplace-browser-fixture.json'


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*')) if p.is_file() and p.name != 'tp_stats.json'}


def prepare(directory, python):
    assert directory.resolve() != (ROOT / 'data').resolve()
    assert not list(directory.iterdir()), 'Fixture destination must be empty'
    generator = r'''import csv,json,math,os
from pathlib import Path
import numpy as np
from scipy import signal
from scipy.ndimage import uniform_filter1d
from app.ingest import ingest_csv
from app.analysis_sources import catalog
from app.store import write_json_atomic
root=Path(os.environ['KIHA_DATA_DIR'])
columns=['time_s','thrust_n','torque_nm','rpm']+['sensor_%02d_temperature_c'%i for i in range(18)]
expected={}
for name in ('a_inplace_flight','s200_fixture','z_inplace_gapped'):
    path=root/(name+'.csv')
    with path.open('w',newline='',encoding='utf-8') as stream:
        writer=csv.writer(stream);writer.writerow(columns)
        for i in range(1200):
            t=i/200
            if name=='z_inplace_gapped' and i>=10:t=1+(i-10)/200
            writer.writerow([t,10+math.sin(2*math.pi*2*t)+.6*math.cos(2*math.pi*45*t),
                3+math.sin(t)+.3*math.cos(2*math.pi*35*t),1500+30*t+math.cos(2*math.pi*3*t)]+
                [20+j+t*.01 for j in range(18)])
    meta=ingest_csv(path,name,copy_raw=True,time_mode='column',time_column='time_s')
    folder=root/'tests'/name
    points={'version':1,'test':name,'test_points':[] if name=='s200_fixture' else [
        {'id':2,'name':'Steady alpha','label':'nominal','start_s':1,'end_s':2,'start_idx':200,'end_idx':400,'notes':'Preserve A'},
        {'id':7,'name':'Steady beta','label':'loaded','start_s':3,'end_s':4.5,'start_idx':600,'end_idx':900,'notes':'Preserve B'}]}
    write_json_atomic(folder/'testpoints.json',points)
    if name=='a_inplace_flight':
        with path.open(newline='',encoding='utf-8') as stream: rows=list(csv.DictReader(stream))
        values={key:np.array([float(row[key]) for row in rows]) for key in columns}
        expected={'columns':columns,'rows':1200,'points':points,'original':{key:val.tolist() for key,val in values.items()},
            'low8':signal.sosfiltfilt(signal.butter(4,8,btype='lowpass',fs=200,output='sos'),values['thrust_n']).tolist(),
            'low4':signal.sosfiltfilt(signal.butter(4,4,btype='lowpass',fs=200,output='sos'),values['thrust_n']).tolist(),
            'moving':{key:uniform_filter1d(values[key],size=5,mode='nearest').tolist() for key in ('torque_nm','rpm')}}
expected['catalog']=catalog()['sources']
(root/'expected.json').write_text(json.dumps(expected),encoding='utf-8')
'''
    subprocess.run([str(python), '-B', '-c', generator], check=True, cwd=ROOT / 'backend',
                   env={**os.environ, 'KIHA_DATA_DIR': str(directory), 'PYTHONPATH': str(ROOT / 'backend')})
    marker = {'dataset': str(directory.resolve()), 'hashes': {
        name: hashes(directory / 'tests' / name) for name in (FLIGHT, DATASHEET, GAPPED)}}
    (directory / MARKER).write_text(json.dumps(marker), encoding='utf-8')


def serve(directory):
    """Run only against this script's marked disposable fixture."""
    marker = json.loads((directory / MARKER).read_text(encoding='utf-8'))
    assert marker['dataset'] == str(directory.resolve())
    assert os.environ['KIHA_DATA_DIR'] == str(directory.resolve())
    sys.path.insert(0, str(ROOT / 'backend'))
    from app import preprocess
    import run
    actual = preprocess.build_pyramid

    def gated(*args, **kwargs):
        deadline = time.monotonic() + 25
        while (directory / 'hold-worker').exists():
            if time.monotonic() > deadline:
                raise RuntimeError('Fixture worker gate timed out')
            time.sleep(.05)
        return actual(*args, **kwargs)

    preprocess.build_pyramid = gated
    run.main()


def get(api, path):
    with urlopen(api + path, timeout=15) as response:
        return json.load(response)


def export(api, name):
    with urlopen(api + f'/tests/{name}/export', timeout=15) as response:
        return response.read()


def check_csv(data, expected, cutoff=None, bulk=False):
    rows = list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
    assert len(rows) == expected['rows'] and list(rows[0]) == expected['columns']
    for index, row in enumerate(rows):
        for column in expected['columns']:
            values = (expected['low' + str(cutoff)] if cutoff and column == 'thrust_n'
                      else expected['moving'][column] if bulk and column in ('torque_nm', 'rpm')
                      else expected['original'][column])
            assert math.isclose(float(row[column]), values[index], rel_tol=2e-10, abs_tol=2e-10), (index, column)


def verify(args, directory):
    from playwright.sync_api import expect, sync_playwright
    from verify_browser_zoom import capture_browser_view, set_browser_zoom
    expected = json.loads((directory / 'expected.json').read_text(encoding='utf-8'))
    marker = json.loads((directory / MARKER).read_text(encoding='utf-8'))
    catalog = get(args.api, '/analysis-sources')['sources']
    assert {(s['name'], s['id']) for s in catalog} == {(s['name'], s['id']) for s in expected['catalog']}, 'Backend must serve this isolated fixture'
    assert all(hashes(directory / 'tests' / name) == marker['hashes'][name] for name in marker['hashes'])
    source = next(s for s in expected['catalog'] if s['name'] == FLIGHT)
    report = {'checks': [], 'posts': [], 'requests': [], 'traces': [], 'pageErrors': [], 'consoleErrors': [], 'blockedWrites': [], 'layouts': []}
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    inject = {'load_failure': False, 'post_failure': False, 'drop_response': False}

    def passed(label):
        report['checks'].append(label); print('PASS:', label, flush=True)

    session = {'version': 1, 'sources': expected['catalog'], 'currentTest': FLIGHT,
        'xAxis': 'rpm', 'yAxis': 'thrust_n', 'axesUserSet': True,
        'selections': [{'test': FLIGHT, 'tpId': 2, 'hidden': False, 'color': '#d55e00'}],
        'plotConfigs': ['thrust_n'], 'plotsUserEdited': True, 'plotDensity': 'single',
        'viewMode': 'tp', 'fullPlotMode': 'line'}
    with tempfile.TemporaryDirectory(prefix='ptt-inplace-profile-') as profile, sync_playwright() as p:
        extension = Path(profile) / 'extension'; extension.mkdir()
        (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Same-flight preprocessing QA',
            'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
        (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
        context = p.chromium.launch_persistent_context(str(Path(profile) / 'profile'), channel='chromium', headless=True,
            no_viewport=True, accept_downloads=True, args=[f'--disable-extensions-except={extension}',
                f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]; page.set_default_timeout(20000)
        page.on('pageerror', lambda e: report['pageErrors'].append(str(e)))
        page.on('console', lambda e: report['consoleErrors'].append(e.text) if e.type == 'error' else None)

        def route_api(route):
            request = route.request; parsed = urlparse(request.url)
            path = unquote(parsed.path).split('/api', 1)[-1]
            report['requests'].append({'method': request.method, 'path': path})
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                if request.method != 'POST' or path not in [f'/tests/{n}/preprocess' for n in (FLIGHT, GAPPED)]:
                    report['blockedWrites'].append({'method': request.method, 'path': path}); route.abort(); return
                payload = request.post_data_json; report['posts'].append({'path': path, **payload})
                assert 'name' not in payload and payload.get('request_id') and payload['source_id'] in [s['id'] for s in expected['catalog']]
                if inject['post_failure']:
                    inject['post_failure'] = False
                    route.fulfill(status=409, json={'detail': 'Fixture conflict; the saved recording is unchanged.'}); return
            if inject['load_failure'] and path == f'/tests/{FLIGHT}/preprocess':
                route.fulfill(status=503, json={'detail': 'Fixture source check unavailable'}); return
            response = route.fetch(url=args.api + path + ('?' + parsed.query if parsed.query else ''), timeout=60000)
            if path == f'/tests/{FLIGHT}/testpoints/2/data' and response.ok:
                report['traces'].append(response.json()['series']['thrust_n']['y'])
            if inject['drop_response'] and request.method == 'POST':
                inject['drop_response'] = False; assert response.status == 202
                route.abort('failed'); return
            route.fulfill(response=response)

        context.route('**/api/**', route_api)
        seed = {'scatterX': 'rpm', 'scatterY': 'thrust_n', 'clustering': False, 'gridColumns': ['thrust_n'], 'datasheetZone': DATASHEET}
        page.add_init_script("if(!sessionStorage.getItem('inplace-seeded')){sessionStorage.setItem('inplace-seeded','1');localStorage.clear();"
            + "localStorage.setItem('ptt.theme.v1','light');localStorage.setItem('ptt.settings.v1'," + json.dumps(json.dumps(seed)) + ");"
            + "localStorage.setItem('ptt.analysis-session.v1'," + json.dumps(json.dumps(session)) + ");}")
        cdp = context.new_cdp_session(page)
        try:
            page.goto(args.url); page.wait_for_load_state('networkidle')
            report['bundle'] = page.locator('script[type="module"][src]').get_attribute('src')
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            window = cdp.send('Browser.getWindowForTarget')['windowId']; dpr = page.evaluate('devicePixelRatio')
            expect(page.locator('.uplot').first).to_be_visible()
            assert any(r['path'] == f'/tests/{FLIGHT}/testpoints/2/data' for r in report['requests'])
            nav = page.get_by_role('navigation', name='Main navigation')
            nav.get_by_role('button', name='Uploads', exact=True).click()
            opener = page.get_by_role('button', name='Pre-process ' + FLIGHT, exact=True)
            expect(page.get_by_role('button', name='Pre-process ' + DATASHEET, exact=True)).to_have_count(0)
            opener.focus(); page.keyboard.press('Enter')
            dialog = page.get_by_role('dialog', name='Pre-process flight', exact=True)
            search = dialog.get_by_role('searchbox', name='Search parameters', exact=True)
            editor = dialog.get_by_role('region', name='Parameter filter settings', exact=True)
            kind = editor.get_by_role('combobox', name='Filter', exact=True)
            apply = dialog.get_by_role('button', name='Apply preprocessing', exact=True)
            expect(search).to_be_visible(); expect(apply).to_be_disabled()
            (output / 'initial-aria.txt').write_text(dialog.aria_snapshot(), encoding='utf-8')
            expect(dialog.get_by_role('textbox', name='Filtered test name')).to_have_count(0)
            expect(dialog.get_by_role('button', name='Configure time_s')).to_have_count(0)
            page.keyboard.press('Escape'); expect(dialog).not_to_be_visible(); expect(opener).to_be_focused()
            passed('loaded TP analysis, datasheet exclusion, protected time and keyboard dialog with no copy-name field')

            inject['load_failure'] = True; opener.click()
            expect(dialog.get_by_text('Fixture source check unavailable', exact=False)).to_be_visible()
            inject['load_failure'] = False; dialog.get_by_role('button', name='Retry loading', exact=True).click()
            expect(search).to_be_visible()
            search.fill('sensor_0')
            dialog.get_by_role('button', name='Select visible', exact=True).click()
            selected = dialog.get_by_role('button', name='Edit filter for selected parameters', exact=True)
            expect(selected).to_contain_text('10 selected')
            search.fill('torque'); expect(selected).to_contain_text('10 hidden')
            dialog.get_by_role('checkbox', name='Select all parameters', exact=True).check()
            expect(selected).to_contain_text('21 selected')
            dialog.get_by_role('button', name='Clear selection', exact=True).click(); search.fill('')

            def configure(column, value, **fields):
                search.fill(column); dialog.get_by_role('button', name='Configure ' + column, exact=True).click()
                kind.select_option(value)
                for label, val in fields.items(): editor.get_by_role('textbox', name=label, exact=True).fill(str(val))
                search.fill('')

            configure('thrust_n', 'lowpass', **{'Order': 4, 'Cutoff (Hz)': 8})
            for col in ('torque_nm', 'rpm'):
                dialog.get_by_role('checkbox', name=f'Select {col} for bulk filtering', exact=True).check()
            kind.select_option('moving_avg'); editor.get_by_role('textbox', name='Window (s)', exact=True).fill('.025')
            expect(apply).to_be_disabled()
            editor.get_by_role('button', name='Apply filter to selected', exact=True).click()
            expect(apply).to_be_enabled()
            dialog.get_by_role('button', name='Clear selection', exact=True).click()
            inject['post_failure'] = True; apply.click()
            expect(dialog.get_by_text('Fixture conflict; the saved recording is unchanged.', exact=False)).to_be_visible()
            expect(apply).to_be_enabled(); check_csv(export(args.api, FLIGHT), expected)
            passed('load/submit retry keeps original recording and configured independent/bulk filters')

            # Close after acceptance while the worker holds the native staging gate.
            traces_before = len(report['traces'])
            (directory / 'hold-worker').write_text('test-only delay')
            apply.click()
            expect(dialog.get_by_text('Updating flight', exact=True)).to_be_visible()
            dialog.get_by_role('button', name='Close pre-process', exact=True).click()
            expect(dialog).not_to_be_visible()
            assert get(args.api, '/tests')[0]['status'] == 'rebuilding'
            (directory / 'hold-worker').unlink()
            expect(opener).to_be_visible(timeout=60000)
            page.wait_for_function('document.body.innerText.includes("Filtered")')
            check_csv(export(args.api, FLIGHT), expected, 8, True)
            expect(page.get_by_role('link', name=f'Download original CSV for {FLIGHT}', exact=True)).to_have_count(0)
            with page.expect_download() as downloading:
                page.get_by_role('link', name=f'Download current CSV for {FLIGHT}', exact=True).click()
            download = output / 'current-filtered.csv'; downloading.value.save_as(download)
            check_csv(download.read_bytes(), expected, 8, True)
            assert len(get(args.api, '/tests')) == 3
            identity = next(s for s in get(args.api, '/analysis-sources')['sources'] if s['name'] == FLIGHT)
            assert identity['id'] == source['id'] and identity['revision'] != source['revision']
            assert get(args.api, f'/tests/{FLIGHT}/testpoints') == expected['points']
            for file in ('raw.csv', 'source_identity.json', 'testpoints.json'):
                assert hashes(directory / 'tests' / FLIGHT)[file] == marker['hashes'][FLIGHT][file]
            passed('close during processing completes under the same row/name/identity with exact native filters and preserved source files')

            nav.get_by_role('button', name='Analyze', exact=True).click()
            expect(page.locator('.uplot').first).to_be_visible()
            page.wait_for_timeout(500)
            assert len(report['traces']) > traces_before, 'Retained TP selection must fetch rebuilt samples'
            # Display traces intentionally round to six decimals; CSV above is full precision.
            assert all(math.isclose(got, want, rel_tol=0, abs_tol=5.00001e-7)
                       for got, want in zip(report['traces'][-1], expected['low8'][200:400]))
            expect(page.get_by_role('button', name=f'Hide Steady alpha from {FLIGHT}', exact=True)).to_be_visible()
            passed('returning to previously loaded Analyze preserves the TP selection and refetches its changed samples')
            nav.get_by_role('button', name='Uploads', exact=True).click(); opener.click(); expect(search).to_be_visible()
            configure('thrust_n', 'lowpass', **{'Order': 4, 'Cutoff (Hz)': 4})
            expect(dialog.get_by_role('button', name='Configure torque_nm')).to_contain_text('Moving average')
            apply.click()
            expect(dialog.get_by_text('Pre-processing saved', exact=True)).to_be_visible(timeout=60000)
            check_csv(export(args.api, FLIGHT), expected, 4, True)
            page.keyboard.press('Escape')
            passed('reopening restores saved independent settings and updates by filtering hidden original samples once')

            opener.click(); expect(search).to_be_visible()
            dialog.get_by_role('button', name='Clear all filters', exact=True).click()
            restore = dialog.get_by_role('button', name='Restore original data', exact=True)
            expect(restore).to_be_enabled(); restore.click()
            expect(dialog.get_by_text('Original data restored', exact=True)).to_be_visible(timeout=60000)
            check_csv(export(args.api, FLIGHT), expected)
            assert not get(args.api, f'/tests/{FLIGHT}').get('preprocessing')
            assert len(get(args.api, '/tests')) == 3
            page.keyboard.press('Escape')
            with page.expect_download() as downloading:
                page.get_by_role('link', name=f'Download current CSV for {FLIGHT}', exact=True).click()
            download = output / 'current-restored.csv'; downloading.value.save_as(download)
            check_csv(download.read_bytes(), expected)
            passed('removing every saved filter restores original samples under the same flight without an extra row')

            # A native too-short continuous region produces a real worker failure.
            page.get_by_role('button', name='Pre-process ' + GAPPED, exact=True).click(); expect(search).to_be_visible()
            configure('thrust_n', 'moving_avg', **{'Window (s)': .025}); apply.click()
            expect(dialog.get_by_text('Pre-processing saved', exact=True)).to_be_visible(timeout=60000)
            prior = export(args.api, GAPPED); page.keyboard.press('Escape')
            page.get_by_role('button', name='Pre-process ' + GAPPED, exact=True).click(); expect(search).to_be_visible()
            configure('thrust_n', 'lowpass', **{'Order': 4, 'Cutoff (Hz)': 8}); apply.click()
            expect(dialog.get_by_text('Pre-processing failed', exact=True)).to_be_visible(timeout=60000)
            assert export(args.api, GAPPED) == prior
            assert next(s for s in get(args.api, '/tests') if s['name'] == GAPPED)['status'] == 'ready'
            page.keyboard.press('Escape')
            expect(page.get_by_text('Pre-process failed', exact=True)).to_be_visible()
            page.get_by_role('button', name='Pre-process ' + GAPPED, exact=True).click()
            expect(search).to_be_visible(); expect(dialog.get_by_text('Pre-processing failed', exact=True)).to_be_visible()
            configure('thrust_n', 'moving_avg', **{'Window (s)': .015}); apply.click()
            expect(dialog.get_by_text('Pre-processing saved', exact=True)).to_be_visible(timeout=60000)
            page.keyboard.press('Escape')
            passed('real native worker failure preserves previous active data and ready status; edited retry succeeds')

            opener.click(); expect(search).to_be_visible()
            configure('thrust_n', 'lowpass', **{'Order': 4, 'Cutoff (Hz)': 8})
            inject['drop_response'] = True; count = len(report['posts']); apply.click()
            expect(dialog.get_by_text('Pre-processing saved', exact=True)).to_be_visible(timeout=60000)
            assert len(report['posts']) == count + 1
            check_csv(export(args.api, FLIGHT), expected, 8)
            page.keyboard.press('Escape')
            passed('lost accepted POST response reconciles by operation ID without duplicate preprocessing')

            for theme in ('light', 'dark'):
                toggle = page.get_by_role('switch', name='Dark mode', exact=True)
                if (toggle.get_attribute('aria-checked') == 'true') != (theme == 'dark'): toggle.click()
                opener.click(); expect(search).to_be_visible()
                dialog.get_by_role('checkbox', name='Select all parameters', exact=True).check()
                kind.select_option('despike')
                for zoom in (1, 1.25, 1.5):
                    width, height = (1440, 1000) if zoom == 1 else (1100, 780)
                    cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': height}})
                    set_browser_zoom(worker, page, zoom)
                    page.wait_for_function('v=>Math.abs(devicePixelRatio-v)<.01', arg=dpr * zoom)
                    bounds = dialog.evaluate('''d=>{const r=d.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:innerWidth,height:innerHeight,overflow:d.scrollWidth-d.clientWidth}}''')
                    assert bounds['x'] >= 0 and bounds['y'] >= 0 and bounds['right'] <= bounds['width'] + 1 and bounds['bottom'] <= bounds['height'] + 1 and bounds['overflow'] <= 1, bounds
                    report['layouts'].append({'theme': theme, 'zoom': zoom, **bounds})
                    search.scroll_into_view_if_needed(); capture_browser_view(cdp, output / f'selection-{theme}-{zoom}.png')
                    for label in ('Window (ms)', 'Max spike (ms)', 'Threshold (MAD)', 'Min jump (units)'):
                        field = editor.get_by_role('textbox', name=label, exact=True)
                        field.scroll_into_view_if_needed(); field.focus(); expect(field).to_be_focused(); expect(field).to_be_in_viewport()
                    bulk_apply = editor.get_by_role('button', name='Apply filter to selected', exact=True)
                    bulk_apply.scroll_into_view_if_needed(); bulk_apply.focus()
                    expect(bulk_apply).to_be_focused(); expect(bulk_apply).to_be_in_viewport()
                    expect(apply).to_be_in_viewport()
                    capture_browser_view(cdp, output / f'editor-{theme}-{zoom}.png')
                page.keyboard.press('Escape'); expect(dialog).not_to_be_visible(); expect(opener).to_be_focused()
                set_browser_zoom(worker, page, 1)
                cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': 1440, 'height': 1000}})
            passed('both themes, desktop resize, actual 100/125/150% zoom, dense bulk filter keyboard access and visible footer')

            page.reload(); page.wait_for_load_state('networkidle')
            nav.get_by_role('button', name='Uploads', exact=True).click(); opener.click(); expect(search).to_be_visible()
            expect(kind).to_have_value('lowpass')
            check_csv(export(args.api, FLIGHT), expected, 8)
            assert hashes(directory / 'tests' / DATASHEET) == marker['hashes'][DATASHEET]
            assert not report['blockedWrites'] and not report['pageErrors'], report
            assert all(any(token in line for token in ('409', '503', 'net::ERR_FAILED')) for line in report['consoleErrors']), report['consoleErrors']
            passed('saved preprocessing survives reload; datasheet source unchanged and no unexpected browser errors/writes')
        except Exception:
            capture_browser_view(cdp, output / 'failure.png')
            state = page.locator('body').aria_snapshot()
            (output / 'failure-aria.txt').write_text(state, encoding='utf-8')
            print(state[-6500:], flush=True)
            raise
        finally:
            (directory / 'hold-worker').unlink(missing_ok=True)
            (output / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            context.close()
    print(f"PASS {len(report['checks'])} browser groups. Evidence: {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, default=ROOT / 'backend/.venv/Scripts/python.exe')
    parser.add_argument('--api', default='http://127.0.0.1:8118/api')
    parser.add_argument('--url', default='http://127.0.0.1:8117/ptt/')
    parser.add_argument('--output', type=Path, default=Path(tempfile.gettempdir()) / 'ptt-preprocess-inplace-verification')
    parser.add_argument('--serve', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve: serve(args.serve); return
    args.output.mkdir(parents=True, exist_ok=True)
    processes = []
    with tempfile.TemporaryDirectory(prefix='ptt-inplace-native-') as temporary:
        directory = Path(temporary)
        prepare(directory, args.python)
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with (args.output / 'backend.log').open('w') as backend_log, (args.output / 'preview.log').open('w') as preview_log:
            try:
                processes.append(subprocess.Popen([str(args.python), '-B', str(Path(__file__).resolve()), '--serve', str(directory)],
                    cwd=ROOT / 'backend', env={**os.environ, 'KIHA_DATA_DIR': str(directory),
                    'KIHA_PORT': str(urlparse(args.api).port), 'PYTHONPATH': str(ROOT / 'backend')},
                    stdout=backend_log, stderr=subprocess.STDOUT, creationflags=flags))
                # Chromium's native download requests bypass Playwright routing.
                # Own a preview proxy to the isolated backend as well; never the
                # repository's default :8000 proxy, which may be a user's server.
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
                assert all(process.poll() is None for process in processes), 'An owned server exited; check its log'
                verify(args, directory)
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                        try: process.wait(timeout=10)
                        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
    print('Owned backend, preview, profile and isolated datasets cleaned up.', flush=True)


if __name__ == '__main__':
    main()
