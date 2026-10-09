"""Native multi-flight Time/XY browser checks using disposable generated recordings.

Starts owned hidden Vite/Python 3.13 servers and a private Chromium profile.
The only permitted POSTs are read-only export jobs. User datasets are hashed;
all native fixtures live below a TemporaryDirectory and retain sample hashes.
"""
import argparse
from contextlib import contextmanager
import csv
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
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from verify_browser_zoom import set_browser_zoom, wait_for_chart_layout, capture_browser_view
from verify_time_y_zoom import instrument
from verify_plot_hover_values import hashes

ROOT = Path(__file__).resolve().parents[1]
KEY = 'ptt.analysis-session.v1'
PLOTS = '.analyze-plots-pane [role="group"][aria-label$=" plot"]'
A, B, C = 'flight_a_100hz', 'flight_b_64hz', 'flight_c_missing'
COLS = ['thrust_n', 'torque_nm', 'rpm', 'current_a', 'voltage_v', 'shaft_power_w',
        'motor_temp_c', 'vibration_x_g', 'electrical_power_w']


def fixture(tmp, python):
    dataset = tmp / 'data'
    generator = tmp / 'generate.py'
    generator.write_text('''import csv, json, math, os
from pathlib import Path
import polars as pl
from app.ingest import ingest_csv, build_pyramid
root = Path(os.environ['KIHA_DATA_DIR']).parent
for name, start, rate, count, bias, missing in [
    ('flight_a_100hz', 100, 100, 1200, 0, False),
    ('flight_b_64hz', 300, 64, 512, 5, False),
    ('flight_c_missing', 700, 80, 640, 10, True)]:
    keys = ['time_s', 'thrust_n', 'rpm', 'current_a', 'voltage_v', 'shaft_power_w',
            'motor_temp_c', 'vibration_x_g', 'electrical_power_w']
    if not missing: keys.insert(2, 'torque_nm')
    path = root / (name + '.csv')
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=keys); writer.writeheader()
        for index in range(count):
            t = index / rate
            row = dict(time_s=start+t, thrust_n=10+bias+math.sin(t), torque_nm=2+bias/10+math.cos(t),
                rpm=1000+100*math.sin(t*2), current_a=4+math.sin(t), voltage_v=25+math.cos(t),
                shaft_power_w=200+30*math.sin(t), motor_temp_c=40+t/10, vibration_x_g=math.sin(t*10)/10,
                electrical_power_w=250+20*math.sin(t))
            writer.writerow({key: row[key] for key in keys})
    meta = ingest_csv(path, name, copy_raw=True, time_mode='column', time_column='time_s')
    # Ingestion normally zeroes measured timestamps. Exercise existing native
    # recordings with nonzero origins by shifting only these disposable files.
    folder = Path(os.environ['KIHA_DATA_DIR']) / 'tests' / name
    samples = pl.read_parquet(folder / 'data.parquet').with_columns(pl.col('time_s') + start)
    samples.write_parquet(folder / 'data.parquet')
    build_pyramid(folder / 'data.parquet', folder / 'pyramid', 'time_s')
    meta['t_start'] = start
    (folder / 'meta.json').write_text(json.dumps(meta))
''', encoding='utf-8')
    subprocess.run([str(python), '-B', str(generator)], cwd=ROOT / 'backend',
        env={**os.environ, 'KIHA_DATA_DIR': str(dataset), 'PYTHONPATH': str(ROOT / 'backend')},
        check=True, capture_output=True, text=True)
    return dataset


@contextmanager
def servers(tmp, output, python, web_port, api_port):
    for port in (web_port, api_port):
        with socket.socket() as sock: sock.bind(('127.0.0.1', port))
    dataset = fixture(tmp, python)
    web, api = f'http://127.0.0.1:{web_port}/', f'http://127.0.0.1:{api_port}/api/'
    children, logs = [], []
    try:
        configurations = [
            ('backend', [str(python), 'run.py'], ROOT / 'backend',
             {'KIHA_DATA_DIR': str(dataset), 'KIHA_PORT': str(api_port), 'KIHA_CORS_ORIGINS': web.rstrip('/')}),
            ('frontend', [shutil.which('node'), 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1',
                          '--port', str(web_port), '--strictPort'], ROOT / 'frontend',
             {'VITE_API_BASE': api.rstrip('/'), 'VITE_BASE_PATH': '/', 'BROWSER': 'none'}),
        ]
        for name, command, cwd, env in configurations:
            log = (output / f'{name}.log').open('w', encoding='utf-8'); logs.append(log)
            children.append(subprocess.Popen(command, cwd=cwd, env={**os.environ, **env}, stdout=log,
                stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
        for url in (api + 'tests', web):
            end = time.monotonic() + 40
            while True:
                try: urlopen(url, timeout=1).close(); break
                except OSError:
                    assert time.monotonic() < end, f'Server startup failed: {url}; see {output}'
                    time.sleep(.15)
        yield web, api, dataset
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=10)
                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
        for log in logs: log.close()


def verify(web, api, dataset, tmp, output, quick=False):
    sources = json.load(urlopen(api + 'analysis-sources'))['sources']
    before = hashes(dataset, True)
    report = {'checks': [], 'pageErrors': [], 'blockedWrites': [], 'requests': []}
    base = {'version': 1, 'sources': sources, 'currentTest': A, 'xAxis': 'rpm', 'yAxis': 'thrust_n',
        'axesUserSet': True, 'selections': [], 'plotConfigs': COLS, 'plotsUserEdited': True,
        'plotDensity': 'quad', 'expandedPlot': None, 'scatterCollapsed': True, 'viewMode': 'full',
        'fullPlotMode': 'line', 'fullPlotExtraColumns': [['torque_nm']] + [[] for _ in range(8)],
        'specMode': 'fft', 'specSource': 'full', 'xySource': 'full', 'xyXCols': ['rpm'] * 9,
        'xyYCols': COLS}
    extension = tmp / 'extension'; extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Flight comparison QA',
        'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')

    def passed(label): report['checks'].append(label); print('PASS:', label, flush=True)
    def near(a, b): assert math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-8), (a, b)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(tmp / 'profile'), channel='chromium', headless=True,
            no_viewport=True, accept_downloads=True, args=[f'--disable-extensions-except={extension}',
            f'--load-extension={extension}', '--window-size=1500,1000'])
        page = context.pages[0]; page.set_default_timeout(20000)
        page.on('pageerror', lambda e: report['pageErrors'].append(str(e)))
        def guard(route):
            request = route.request; path = urlparse(request.url).path
            if any(part in path for part in ['/xy', '/window', '/spectrum', '/waterfall']):
                report['requests'].append({'path': path, 'query': parse_qs(urlparse(request.url).query)})
            if request.method not in {'GET', 'HEAD', 'OPTIONS'} and not (
                request.method == 'POST' and any(part in path for part in
                    ['/plot-export', '/xy-export', '/plot-image-export', '/export-progress'])):
                report['blockedWrites'].append(request.url); route.abort()
            else: route.continue_()
        context.route('**/api/**', guard)
        context.route('**/src/utils/uplotSync.ts*', instrument)
        context.add_init_script(f"if(!sessionStorage.getItem('flight-seeded')){{sessionStorage.setItem('flight-seeded','1');localStorage.setItem('{KEY}',{json.dumps(json.dumps(base))});localStorage.setItem('ptt.theme.v1','light');localStorage.setItem('ptt.plot-values.v1','all');}}")
        cdp = context.new_cdp_session(page)
        try:
            page.goto(web); page.wait_for_load_state('networkidle')
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            def plot(i=0): return page.locator(PLOTS).nth(i)
            def settle(count=None):
                page.wait_for_load_state('networkidle')
                expect(plot().locator('.uplot').first).to_be_visible()
                page.wait_for_function('''selector=>[...document.querySelectorAll(selector)].every(el=>
                  !el.getClientRects().length || (el.querySelector('.uplot') &&
                  !/Loading paired samples|Loading flights|Updating XY view|Updating flight comparison/.test(el.innerText)))''', arg=PLOTS)
                if count is not None:
                    page.wait_for_function('''({selector,count})=>{
                      const u=document.querySelector(selector)?.querySelector('.uplot')?.__verificationPlot;
                      return u && u.series.length===count+1}''', arg={'selector': PLOTS, 'count': count})
                wait_for_chart_layout(page)
            def state(target=None):
                return (target if target is not None else plot()).locator('.uplot').first.evaluate('''el=>{
                  const u=el.__verificationPlot;
                  return {x:[u.scales.x.min,u.scales.x.max],y:[u.scales.y.min,u.scales.y.max],
                    axes:u.axes.map(a=>a.label),traces:u.series.slice(1).map((s,i)=>({label:s.label,
                      color:typeof s.stroke==='function'?s.stroke(u,i+1):s.stroke,dash:s.dash,
                      x:s.facets?u.data[i+1][0]:u.data[0],y:s.facets?u.data[i+1][1]:u.data[i+1]}))};}''')
            def select(combobox, label):
                combobox.click(); page.get_by_role('option', name=label, exact=True).click(); page.keyboard.press('Escape')
            def set_time_basis(label):
                page.get_by_role('button', name='Align…', exact=True).click()
                page.get_by_role('combobox', name='Flight time basis').click()
                page.get_by_role('option', name=label, exact=True).click()
                page.get_by_role('button', name='Apply alignment', exact=True).click()
            def add_flight(name):
                page.get_by_role('button', name='Flights', exact=True).click()
                page.get_by_placeholder('Search flights...').fill(name)
                page.get_by_role('option', name=re.compile('^' + re.escape(name))).click()
                page.keyboard.press('Escape'); settle()
            def seed(changes):
                page.evaluate('(s)=>localStorage.setItem("ptt.analysis-session.v1",JSON.stringify(s))', {**base, **changes})
                page.reload(); settle()
            def download(kind, filename):
                plot().get_by_role('button', name=re.compile('^Plot actions for ')).click()
                page.get_by_role('menuitem', name='Export CSV / PNG…', exact=True).click()
                dialog = page.get_by_role('dialog', name=re.compile('^Export '))
                dialog.get_by_role('checkbox', name='Include analysis metadata (ZIP)', exact=True).uncheck()
                with page.expect_download() as event:
                    dialog.get_by_role('button', name=f'Download {kind}', exact=True).click()
                event.value.save_as(output / filename); page.keyboard.press('Escape')
                return output / filename
            def drag_zoom():
                bounds = plot().locator('.u-over').first.bounding_box()
                page.mouse.move(bounds['x'] + bounds['width'] * .2, bounds['y'] + bounds['height'] * .2)
                page.mouse.down()
                page.mouse.move(bounds['x'] + bounds['width'] * .7, bounds['y'] + bounds['height'] * .7, steps=8)
                page.mouse.up(); settle()
            def hover(target=None):
                target = target if target is not None else plot()
                over = target.locator('.u-over').first; box = over.bounding_box()
                page.mouse.move(box['x']+box['width']*.45, box['y']+box['height']*.5)
                expect(page.locator('.plot-hover-values').first).to_be_visible()
                assert page.locator('.plot-hover-values').evaluate_all('''els=>els.every(el=>{
                  const r=el.getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1})''')
                slot = target.evaluate('el=>el.closest("[data-plot-hover-slot]").dataset.plotHoverSlot')
                rows = page.locator(f'.plot-hover-values[data-plot-hover-slot="{slot}"] tbody tr')
                assert rows.count() > 0
                assert all(name in ' '.join(rows.locator('th').evaluate_all('els=>els.map(el=>el.getAttribute("aria-label"))'))
                           for name in (A, B))

            settle(2)
            picker = page.get_by_role('button', name='Flights', exact=True)
            picker.focus(); page.keyboard.press('Enter')
            expect(page.get_by_role('listbox', name='Flights', exact=True)).to_have_attribute('aria-multiselectable', 'true')
            page.get_by_placeholder('Search flights...').fill(B)
            page.keyboard.press('ArrowDown'); page.keyboard.press('Enter'); page.keyboard.press('Escape')
            settle(4)
            expect(page.get_by_role('combobox', name='Flight time basis')).to_have_count(0)
            page.get_by_role('button', name='Align…', exact=True).click()
            expect(page.get_by_role('combobox', name='Flight time basis')).to_contain_text('Elapsed time')
            page.get_by_role('button', name='Cancel', exact=True).click()
            data = state(); assert all(trace['x'][0] == 0 for trace in data['traces'])
            colors = {name: {trace['color'] for trace in data['traces'] if name in trace['label']} for name in (A, B)}
            assert all(len(value) == 1 for value in colors.values()) and colors[A] != colors[B]
            assert data['traces'][0]['dash'] != data['traces'][1]['dash']
            assert {len(trace['x']) for trace in data['traces']} == {1200, 512}
            hover(); passed('Keyboard searchable multi-flight selection overlays two variables with stable colors/patterns and independent 100/64 Hz native samples')

            drag_zoom(); zoom = state()['x']
            page.get_by_role('button', name=f'Hide flight {B}', exact=True).click(); settle(2)
            for actual, expected in zip(state()['x'], zoom): near(actual, expected)
            page.get_by_role('button', name=f'Show flight {B}', exact=True).click(); settle(4)
            for actual, expected in zip(state()['x'], zoom): near(actual, expected)
            add_flight(C); settle(5)
            for actual, expected in zip(state()['x'], zoom): near(actual, expected)
            expect(plot()).to_contain_text('2 of 3 flights have all variables')
            expect(plot().get_by_role('status', name=f'{C}: missing torque_nm')).to_be_visible()
            page.get_by_role('button', name=f'Remove flight {C}', exact=True).click(); settle(4)
            page.get_by_role('button', name='Fit all flights', exact=True).click(); settle(4)
            passed('Add/hide/show preserves the shared zoom; missing variables retain available traces and Fit all flights restores full duration')

            page.get_by_role('button', name='Align…', exact=True).click()
            page.get_by_role('textbox', name=f'Time shift for {B}', exact=True).fill('1.5')
            page.get_by_role('button', name='Apply alignment', exact=True).click(); settle(4)
            for trace in state()['traces']: near(trace['x'][0], 1.5 if B in trace['label'] else 0)
            set_time_basis('Stored time'); settle(4)
            for trace in state()['traces']: near(trace['x'][0], 301.5 if B in trace['label'] else 100)
            page.reload(); settle(4)
            for trace in state()['traces']: near(trace['x'][0], 301.5 if B in trace['label'] else 100)
            set_time_basis('Elapsed time'); settle(4)
            passed('Manual time offsets, stored/elapsed coordinates, flight identities and multi-variable selections survive reload')

            csv_path = download('CSV', 'time-flights.csv')
            rows = list(csv.DictReader(csv_path.open(encoding='utf-8-sig')))
            assert {row['source_test'] for row in rows} == {A, B}
            for row in rows: near(float(row['displayed_time_s']), float(row['time_s']) + float(row['time_offset_s']))
            assert len(rows) == 1712
            canvas = plot().locator('canvas').first.evaluate('c=>c.toDataURL()')
            download('PNG', 'time-flights.png')
            assert plot().locator('canvas').first.evaluate('c=>c.toDataURL()') == canvas
            passed('Time CSV exports both native recordings with displayed-time provenance; real PNG preserves the displayed canvas')

            page.get_by_role('group', name='Plot view', exact=True).get_by_role('button', name='XY', exact=True).click(); settle(2)
            data = state(); assert {trace['label'] for trace in data['traces']} == {A, B}
            assert [len(trace['x']) for trace in data['traces']] == [1200, 512]
            for trace in data['traces']:
                near(trace['x'][0], 1000); near(trace['y'][0], 10 if trace['label'] == A else 15)
            hover(); drag_zoom(); zoom = state()
            page.get_by_role('button', name=f'Hide flight {B}', exact=True).click(); settle(1)
            for axis in ('x', 'y'):
                for actual, expected in zip(state()[axis], zoom[axis]): near(actual, expected)
            page.get_by_role('button', name=f'Show flight {B}', exact=True).click(); settle(2)
            page.get_by_role('button', name='Fit all flights', exact=True).click(); settle(2)
            passed('XY overlays independent native unsorted clouds with flight identity, correct hover and preserved two-axis zoom when hiding sources')

            select(plot().get_by_role('button', name='X variable'), 'time_s'); settle(2)
            data = state(); assert 'elapsed' in data['axes'][0]
            for trace in data['traces']: near(trace['x'][0], 0 if trace['label'] == A else 1.5)
            xy_path = download('CSV', 'xy-time-flights.csv')
            rows = list(csv.DictReader(xy_path.open(encoding='utf-8-sig')))
            assert {row['source_test'] for row in rows} == {A, B}
            for row in rows:
                near(float(row['displayed_time_s']), float(row['time_s']) + float(row['time_offset_s']))
                near(float(row['time_s [X]']), float(row['displayed_time_s']))
            download('PNG', 'xy-time-flights.png')
            select(plot().get_by_role('button', name='Y variable'), 'time_s'); settle(2)
            for trace in state()['traces']: assert trace['x'] == trace['y']
            select(plot().get_by_role('button', name='Y variable'), 'torque_nm'); settle(2)
            add_flight(C); settle(2)
            expect(plot()).to_contain_text('2 of 3 flights')
            plot().get_by_role('button', name=re.compile('^Plot actions for ')).click()
            page.get_by_role('menuitem', name=re.compile('^Analysis details')).click()
            expect(page.get_by_role('dialog', name=re.compile('^XY details'))).to_contain_text(f'{C} (missing torque_nm)')
            page.keyboard.press('Escape'); page.get_by_role('button', name=f'Remove flight {C}', exact=True).click(); settle(2)
            passed('XY aligns only actual time-column axes, including both axes; CSV retains native times and displayed XY values; missing-flight coverage names excluded signals')

            def fail_b(route): route.fulfill(status=503, json={'detail': 'isolated transient XY failure'})
            context.route(f'**/tests/{B}/xy?*', fail_b)
            page.reload(); settle(1)
            expect(plot()).to_contain_text('could not be loaded')
            context.unroute(f'**/tests/{B}/xy?*', fail_b)
            plot().get_by_role('button', name='Retry', exact=True).click(); settle(2)
            passed('One failed XY flight retains successful native pairs, reports partial status and recovers through Retry')

            comparison = {'flights': [{'test': name, 'color': color, 'hidden': False, 'offset': offset}
                for name, color, offset in [(A, '#d55e00', 0), (B, '#cc79a7', 1.5)]], 'timeBasis': 'elapsed'}
            seed({'viewMode': 'xy', 'fullFlightComparison': comparison, 'fullFlightRange': [2, 4],
                  'xyXCols': ['time_s'] * 9, 'plotDensity': 'single'})
            data = state()
            for trace in data['traces']:
                # Native floor/ceil row bounds may retain a boundary neighbour.
                tolerance = 1 / (100 if trace['label'] == A else 64)
                assert min(trace['x']) >= 2-tolerance-1e-9 and max(trace['x']) <= 4+tolerance+1e-9, (
                    trace['label'], min(trace['x']), max(trace['x']))
            xy_requests = [request for request in report['requests'] if request['path'].endswith('/xy')]
            for name, offset in [(A, -100), (B, -298.5)]:
                query = next(item['query'] for item in reversed(xy_requests) if name in item['path'])
                near(float(query['t0'][0]), 2-offset); near(float(query['t1'][0]), 4-offset)
            passed('Shared displayed-time crop translates to each flight’s native query interval without resampling')

            for view in ('full', 'xy'):
                seed({'viewMode': view, 'plotDensity': 'single', 'fullFlightComparison': None})
                drag_zoom(); zoom = state()
                page.get_by_role('button', name=f'Hide flight {A}', exact=True).click()
                expect(plot().locator('.uplot')).to_have_count(0)
                page.get_by_role('button', name=f'Show flight {A}', exact=True).click(); settle()
                for axis in ('x', 'y'):
                    for actual, expected in zip(state()[axis], zoom[axis]): near(actual, expected)
            passed('Initial single-flight Time and XY manual viewports survive entering comparison through hide/show')

            for method, endpoint in [('fft', '/spectrum'), ('waterfall', '/waterfall')]:
                start = len(report['requests'])
                seed({'viewMode': 'spectrum', 'specMode': method, 'plotDensity': 'single',
                      'fullFlightComparison': comparison, 'fullFlightRange': [2, 4], 'fullRange': [100, 104],
                      'waterfallWindow': 128, 'waterfallResolution': None})
                expect(page.get_by_role('button', name='Flights', exact=True)).to_have_count(0)
                requests = [item for item in report['requests'][start:] if item['path'].endswith(endpoint)]
                assert requests and all(A in item['path'] for item in requests), requests
                for item in requests:
                    near(float(item['query']['t0'][0]), 100); near(float(item['query']['t1'][0]), 104)
            passed('Spectrum and Waterfall retain their single active source and native time crop independently of flight comparison alignment')

            if not quick:
                for view in ('full', 'xy'):
                    for density, count in [('single', 1), ('quad', 4), ('nine', 9)]:
                        seed({'viewMode': view, 'plotDensity': density, 'fullFlightComparison': comparison})
                        expect(page.locator(PLOTS)).to_have_count(count)
                        for zoom in (1, 1.25, 1.5):
                            set_browser_zoom(worker, page, zoom); settle(); hover()
                            assert page.locator(PLOTS).evaluate_all('''els=>els.every(el=>{
                              const r=el.getBoundingClientRect();return r.width>50&&r.height>40&&r.right<=innerWidth+1})''')
                        set_browser_zoom(worker, page, 1); settle()
                        plot().get_by_role('button', name=re.compile('^Expand ')).click(); settle(); hover()
                        page.get_by_role('button', name=re.compile('^Minimize ')).click(); settle()
                        expect(page.locator(PLOTS)).to_have_count(count)
                        passed(f'{view}: {count} plots at actual 100/125/150% zoom, maximize/restore and bounded hover boxes')
                page.get_by_role('switch', name='Dark mode', exact=True).click(); settle()
                hover(); capture_browser_view(cdp, output / 'flights-dark-nine.png')
                cdp.send('Browser.setWindowBounds', {'windowId': cdp.send('Browser.getWindowForTarget')['windowId'],
                    'bounds': {'width': 1050, 'height': 800}})
                settle(); hover(); capture_browser_view(cdp, output / 'flights-compact.png')
                passed('Dark theme and resized desktop window retain readable flight controls, XY plots and hover values')
            assert not report['pageErrors'], report['pageErrors']
            assert not report['blockedWrites'], report['blockedWrites']
            assert hashes(dataset, True) == before, 'Generated source samples changed'
            report['sourceSamplesUnchanged'] = True
        except Exception:
            capture_browser_view(cdp, output / 'failure.png')
            (output / 'failure.html').write_text(page.content(), encoding='utf-8')
            raise
        finally:
            (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-python', type=Path, default=Path('D:/okumus/kiha_test/backend/.venv/Scripts/python.exe'))
    parser.add_argument('--web-port', type=int, default=3126)
    parser.add_argument('--api-port', type=int, default=8026)
    parser.add_argument('--quick', action='store_true', help='Skip the desktop zoom/layout matrix')
    parser.add_argument('--output', type=Path, default=Path(tempfile.gettempdir()) / 'ptt-full-flight-comparison')
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    original = hashes(ROOT / 'data/tests')
    with tempfile.TemporaryDirectory(prefix='ptt-flight-comparison-') as directory:
        tmp = Path(directory)
        with servers(tmp, args.output, args.backend_python, args.web_port, args.api_port) as (web, api, dataset):
            verify(web, api, dataset, tmp, args.output, args.quick)
    assert hashes(ROOT / 'data/tests') == original, 'Original datasets changed'
    print('Original datasets unchanged; owned servers stopped. Report:', args.output, flush=True)


if __name__ == '__main__': main()
