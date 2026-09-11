"""Native high-detail waterfall UI/grid/export verification with isolated fixtures.

The runner imports only stdlib and Playwright. All dataset/native calculations
run in the repository Python 3.13 child server. Existing helpers own and clean
only their temporary dataset, profile, extension and server processes.
"""
import argparse
import csv
import io
import json
import math
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import parse_qs, urlparse
import zipfile

from playwright.sync_api import expect, sync_playwright
from verify_data_quality import ROOT, servers, upload
from verify_filter_overlay import dataset_hashes
from verify_browser_zoom import set_browser_zoom, capture_browser_view, wait_for_chart_layout


A, B = 'detail_A', 'detail_B'
FS, SECONDS = 2048, 24


def fixtures(request):
    for name, amplitude in ((A, 3), (B, 1)):
        lines = ['time,signal_N,reference_V']
        for i in range(FS * SECONDS):
            t = i / FS
            signal = amplitude * (math.sin(2 * math.pi * 84 * t) + .7 * math.sin(2 * math.pi * 85 * t))
            lines.append(f'{100+t:.12f},{signal:.15g},{math.cos(2*math.pi*40*t):.15g}')
        assert upload(request, name, '\n'.join(lines))['status'] == 'ready'
        points = [{'id': 7, 'name': 'Two tones', 'start_s': 0, 'end_s': SECONDS,
                   'start_idx': 0, 'end_idx': FS * SECONDS},
                  {'id': 9, 'name': 'Short interval', 'start_s': 2, 'end_s': 3,
                   'start_idx': FS * 2, 'end_idx': FS * 3}]
        assert request.put(f'tests/{name}/testpoints', data={'test': name, 'version': 1, 'test_points': points}).ok
        for column in ('signal_N', 'reference_V'):
            assert request.get(f'tests/{name}/tp_stats', params={'col': column}).ok


def assert_grid_csv(rows, grids, payload):
    """Compare every exported cell with the actual last displayed source grid."""
    expected = {}
    for test, data in grids.items():
        freq, time = data['frequency_edges_hz'], data['time_edges_s']
        for y, values in enumerate(data['magnitude']):
            if payload.get('y_range') and (time[y + 1] < payload['y_range'][0] or time[y] > payload['y_range'][1]):
                continue
            for x, magnitude in enumerate(values):
                if payload.get('x_range') and (freq[x + 1] < payload['x_range'][0] or freq[x] > payload['x_range'][1]):
                    continue
                key = (test, time[y], time[y+1], freq[x], freq[x+1])
                expected[key] = (magnitude, data['source_frame_start_s'][y], data['source_frame_end_s'][y])
    assert len(rows) == len(expected), (len(rows), len(expected), payload)
    for row in rows:
        key = (row['source_test'], *(float(row[k]) for k in ('elapsed_start_s', 'elapsed_end_s', 'frequency_start_hz', 'frequency_end_hz')))
        magnitude, first, last = expected.pop(key)
        assert math.isclose(float(row['magnitude_U']), magnitude, rel_tol=1e-11, abs_tol=1e-11)
        assert math.isclose(float(row['first_frame_source_time_s']), first, abs_tol=1e-10)
        assert math.isclose(float(row['last_frame_source_time_s']), last, abs_tol=1e-10)
        assert row['method_version'] == 'kiha-waterfall-v2'
    assert not expected


def run(web, api, dataset, temporary, output):
    extension = temporary / 'extension'
    extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Waterfall detail verification',
        'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as p:
        request = p.request.new_context(base_url=api + '/')
        fixtures(request)
        sources = request.get('analysis-sources').json()['sources']
        before = dataset_hashes(dataset)
        context = p.chromium.launch_persistent_context(str(temporary / 'profile'), channel='chromium', headless=True,
            no_viewport=True, args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1600,1100'])
        page = context.pages[0]
        errors, posts, requests, held, checks = [], [], [], [], []
        latest = {}
        state = {'hold': False, 'fail': False}
        page.on('pageerror', lambda e: errors.append(str(e)))

        def instrument(route):
            response = route.fetch()
            text = response.text()
            needle = 'plot.current = u;'
            assert needle in text
            route.fulfill(response=response, body=text.replace(needle, needle + ' u.root.__waterfall = u;'))

        def waterfall_route(route):
            if state['fail'] and f'{B}/' in route.request.url:
                route.fulfill(status=503, json={'detail': 'Isolated detail source failure'})
                return
            response = route.fetch()
            if state['hold']:
                held.append((route, response))
                return
            if response.ok:
                query = parse_qs(urlparse(route.request.url).query)
                data = response.json()
                test = route.request.url.split('/tests/')[1].split('/')[0]
                requests.append({'test': test, 'query': query, 'method': data['method'], 'reduction': data['reduction']})
                latest[(test, data['col'])] = data
            route.fulfill(response=response)

        def exported(route):
            posts.append({'url': route.request.url, 'body': route.request.post_data_json})
            route.continue_()

        context.route('**/src/components/plots/WaterfallPlot.tsx*', instrument)
        context.route('**/waterfall?*', waterfall_route)
        context.route(re.compile(r'/waterfall-export(?:/bundle)?(?:\?.*)?$'), exported)
        session = {'version': 1, 'sources': sources, 'currentTest': A, 'xAxis': 'signal_N', 'yAxis': 'reference_V',
            'axesUserSet': True, 'selections': [{'test': name, 'tpId': 7, 'hidden': False} for name in (A, B)],
            'plotConfigs': ['signal_N', 'reference_V'], 'plotsUserEdited': True, 'plotDensity': 'single',
            'viewMode': 'spectrum', 'specMode': 'waterfall', 'specSource': 'tp', 'specLogY': False, 'scatterCollapsed': True}
        page.add_init_script(f'''if(!sessionStorage.getItem('detail-seeded')){{localStorage.clear();sessionStorage.setItem('detail-seeded','1');
            localStorage.setItem('ptt.analysis-session.v1',{json.dumps(json.dumps(session))});}}''')

        def plot():
            return page.get_by_role('group', name='signal_N waterfall plot', exact=True).first

        def settled(count=2):
            page.wait_for_load_state('networkidle')
            expect(plot().locator('.uplot')).to_have_count(count)
            expect(plot()).not_to_contain_text('Calculating waterfall FFT')
            expect(plot()).not_to_contain_text('Refining visible grid')
            wait_for_chart_layout(page)

        def scales():
            return plot().locator('.uplot').evaluate_all('els=>els.map(el=>({x:[el.__waterfall.scales.x.min,el.__waterfall.scales.x.max],y:[el.__waterfall.scales.y.min,el.__waterfall.scales.y.max]}))')

        def set_axes(x, y):
            plot().locator('.uplot').first.evaluate('''(el,r)=>{const u=el.__waterfall;u.batch(()=>{
                u.setScale('x',{min:r.x[0],max:r.x[1]});u.setScale('y',{min:r.y[0],max:r.y[1]});});}''', {'x': x, 'y': y})
            settled()

        def menu(action, keyboard=False):
            button = plot().get_by_role('button', name='Plot actions for signal_N', exact=True)
            if keyboard:
                button.focus()
                page.keyboard.press('Enter')
            else:
                button.click()
            page.get_by_role('menuitem', name=action, exact=True).click()

        def setting(name, value, count=2):
            page.get_by_role('combobox', name=name, exact=True).select_option(value)
            settled(count)

        def patch_session(changes, remove=()):
            page.wait_for_timeout(350)
            page.evaluate('''({changes,remove,sources})=>{const s=JSON.parse(localStorage.getItem('ptt.analysis-session.v1'));
                Object.assign(s,{sources},changes);for(const key of remove)delete s[key];localStorage.setItem('ptt.analysis-session.v1',JSON.stringify(s));}''',
                {'changes': changes, 'remove': list(remove), 'sources': sources})
            page.reload()

        def source_grids(column='signal_N'):
            return {name: latest[(name, column)] for name in (A, B)}

        def export(fmt, tag):
            displayed = source_grids()
            menu('Export CSV / PNG…', keyboard=True)
            panel = page.get_by_role('dialog', name='Export signal_N plot', exact=True)
            with page.expect_download(timeout=60000) as download:
                panel.get_by_role('button', name=f'Download {fmt}', exact=True).click()
            path = output / f'{tag}.zip'
            download.value.save_as(path)
            with zipfile.ZipFile(path) as archive:
                metadata = json.loads(archive.read('analysis.json'))
                assert 'kiha-waterfall-v2' in json.dumps(metadata)
                if fmt == 'CSV':
                    payload = posts[-1]['body']
                    assert payload['method_version'] == 'kiha-waterfall-v2'
                    assert 'grid_frequency_range_hz' in payload and 'grid_time_range_s' in payload
                    rows = list(csv.DictReader(io.StringIO(archive.read(next(n for n in archive.namelist() if n.endswith('.csv'))).decode())))
                    assert_grid_csv(rows, displayed, payload)
                else:
                    entry = next(n for n in archive.namelist() if n.endswith('.png'))
                    (output / f'{tag}.png').write_bytes(archive.read(entry))
            page.keyboard.press('Escape')
            expect(panel).not_to_be_visible()

        def passed(message):
            checks.append(message)
            print('PASS: ' + message, flush=True)

        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            page.goto(web)
            settled()
            expect(page.get_by_role('combobox', name='Bin spacing', exact=True)).to_have_value('0.25')
            expect(page.get_by_role('combobox', name='Band', exact=True)).to_have_value('low')
            expect(page.get_by_role('combobox', name='Overlap', exact=True)).to_have_value('75')
            initial = scales()
            assert initial[0] == initial[1]
            assert initial[0]['x'] == [0, 200], initial
            for data in source_grids().values():
                assert data['method']['version'] == 'kiha-waterfall-v2'
                assert data['method']['nperseg'] == 8192 and data['method']['bin_spacing_hz'] == .25
                assert data['reduction']['frequency_factor'] == 1
                edges = data['frequency_edges_hz']
                indexes = [next(i for i in range(len(edges)-1) if edges[i] <= hz < edges[i+1]) for hz in (84, 84.5, 85)]
                peaks = [data['magnitude'][0][i] for i in indexes]
                assert peaks[0] > peaks[1] * 10 and peaks[2] > peaks[1] * 10, peaks
            passed('Fresh defaults preserve individual 0.25 Hz bins and distinct 84/85 Hz peaks at 2048 Hz')
            plot().locator('.u-over').first.hover(position={'x': 120, 'y': 90})
            expect(plot()).to_contain_text('source frame centers')
            plot().screenshot(path=str(output / 'default-025hz.png'))
            menu('Analysis details…')
            details = page.get_by_role('dialog', name='Waterfall analysis for signal_N')
            expect(details).to_contain_text('hann_periodic')
            page.keyboard.press('Escape')
            export('CSV', 'default-grid')

            setting('Bin spacing', '0.1')
            assert latest[(A, 'signal_N')]['method']['nperseg'] == 20480
            assert latest[(A, 'signal_N')]['method']['window_seconds'] == 10
            assert latest[(A, 'signal_N')]['method']['bin_spacing_hz'] == .1
            assert latest[(A, 'signal_N')]['reduction']['frequency_factor'] == 1
            page.wait_for_timeout(400)
            page.reload()
            settled()
            expect(page.get_by_role('combobox', name='Bin spacing', exact=True)).to_have_value('0.1')
            setting('Bin spacing', '0.5')
            assert latest[(A, 'signal_N')]['method']['window_seconds'] == 2
            setting('Bin spacing', '0.25')
            setting('Band', 'full')
            assert scales()[0]['x'] == [0, 1024], scales()
            assert latest[(A, 'signal_N')]['reduction']['frequency_factor'] > 1
            prior = len(requests)
            set_axes([80, 90], [6, 14])
            assert len(requests) > prior
            zoomed = scales()
            assert zoomed[0] == zoomed[1]
            assert latest[(A, 'signal_N')]['reduction']['frequency_factor'] == 1
            q = requests[-1]['query']
            assert float(q['frequency_min_hz'][0]) == 80 and float(q['frequency_max_hz'][0]) == 90, q
            assert float(q['elapsed_min_s'][0]) == 6 and float(q['elapsed_max_s'][0]) == 14, q
            plot().screenshot(path=str(output / 'zoom-84-85hz.png'))
            export('CSV', 'zoom-grid')
            export('PNG', 'zoom-image')
            passed('0.5/0.25/0.1 Hz settings, 10-second window, band switch, zoom refetch and exact CSV/PNG grid provenance')

            plot().get_by_role('button', name='Expand signal_N', exact=True).click()
            settled()
            assert scales() == zoomed
            page.wait_for_timeout(400)
            page.reload()
            settled()
            assert scales() == zoomed
            menu('Reset axes', keyboard=True)
            settled()
            full_initial = scales()
            assert full_initial[0]['x'] == [0, 1024]
            box = plot().locator('.u-over').first.bounding_box()
            page.mouse.move(box['x']+box['width']*.08, box['y']+box['height']*.2)
            page.mouse.down()
            page.mouse.move(box['x']+box['width']*.15, box['y']+box['height']*.8, steps=12)
            page.mouse.up()
            settled()
            dragged = scales()
            assert dragged[0] == dragged[1] and dragged != full_initial
            plot().locator('.u-over').first.hover()
            page.mouse.wheel(0, -220)
            settled()
            wheeled = scales()
            assert wheeled[0] == wheeled[1] and wheeled != dragged
            box = plot().locator('.u-over').first.bounding_box()
            page.keyboard.down('Shift')
            page.mouse.move(box['x']+box['width']*.5, box['y']+box['height']*.5)
            page.mouse.down()
            page.mouse.move(box['x']+box['width']*.6, box['y']+box['height']*.5, steps=8)
            page.mouse.up()
            page.keyboard.up('Shift')
            settled()
            assert scales() != wheeled
            plot().get_by_label(f'Waterfall canvas for Two tones · {A} · TP 7', exact=True).focus()
            page.keyboard.press('Home')
            settled()
            assert scales() == full_initial
            plot().get_by_role('button', name='Minimize signal_N', exact=True).click()
            settled()
            passed('Linked two-axis drag/wheel zoom, Shift-pan, keyboard reset, maximize/restore and saved viewport reload')

            setting('Bin spacing', 'manual')
            setting('Window (samples)', '256')
            data = latest[(A, 'signal_N')]
            assert data['reduction']['time_factor'] > 1
            set_axes([0, 200], [10, 11])
            data = latest[(A, 'signal_N')]
            assert data['reduction']['time_factor'] == 1
            assert data['source_frame_start_s'][0] > data['time_start_s'] + 9
            export('CSV', 'time-refined-grid')
            patch_session({'waterfallWindow': 512, 'waterfallOverlap': 50},
                          ('waterfallResolution', 'waterfallBand', 'plotViewports'))
            settled()
            expect(page.get_by_role('combobox', name='Bin spacing', exact=True)).to_have_value('manual')
            expect(page.get_by_role('combobox', name='Band', exact=True)).to_have_value('full')
            expect(page.get_by_role('combobox', name='Window (samples)', exact=True)).to_have_value('512')
            expect(page.get_by_role('combobox', name='Overlap', exact=True)).to_have_value('50')
            set_axes([50, 150], [5, 15])
            legacy_scales = scales()
            page.wait_for_timeout(400)
            legacy_viewports = page.evaluate('''()=>{const s=JSON.parse(localStorage.getItem('ptt.analysis-session.v1'));
                const v=s.plotViewports.spectrum[0];const c=JSON.parse(v.context);c[2]=c[2].slice(0,3);
                v.context=JSON.stringify(c);return s.plotViewports;}''')
            patch_session({'plotViewports': legacy_viewports}, ('waterfallResolution', 'waterfallBand'))
            settled()
            assert scales() == legacy_scales, (scales(), legacy_scales)
            q = requests[-1]['query']
            assert float(q['frequency_min_hz'][0]) == 50 and float(q['elapsed_min_s'][0]) == 5, q
            passed('Time zoom restores native frames with original origins; legacy manual/full settings and saved viewport migrate')

            setting('Bin spacing', '0.25')
            setting('Band', 'low')
            state['fail'] = True
            page.get_by_role('combobox', name='Overlap', exact=True).select_option('75')
            page.wait_for_load_state('networkidle')
            expect(plot()).to_contain_text('Isolated detail source failure')
            menu('Export CSV / PNG…')
            panel = page.get_by_role('dialog', name='Export signal_N plot')
            expect(panel.get_by_role('button', name='Download CSV', exact=True)).to_be_disabled()
            page.keyboard.press('Escape')
            state['fail'] = False
            plot().get_by_role('button', name='Retry waterfall').click()
            settled()
            patch_session({'selections': [{'test': A, 'tpId': 9, 'hidden': False}]}, ('plotViewports',))
            page.wait_for_load_state('networkidle')
            expect(plot().get_by_role('alert')).to_be_visible()
            expect(plot().locator('.uplot')).to_have_count(0)
            plot().screenshot(path=str(output / 'short-interval-error.png'))
            patch_session({'selections': session['selections']}, ('plotViewports',))
            settled()
            state['hold'] = True
            page.get_by_role('combobox', name='Bin spacing', exact=True).select_option('0.5')
            for _ in range(200):
                if held:
                    break
                page.wait_for_timeout(50)
            assert held, 'Expected pending request'
            page.get_by_role('combobox', name='Estimator', exact=True).select_option('fft')
            state['hold'] = False
            for route, response in held:
                try:
                    route.fulfill(response=response)
                except Exception:
                    pass  # An aborted browser request must not update its unmounted plot.
            held.clear()
            page.wait_for_load_state('networkidle')
            expect(page.get_by_role('group', name='signal_N spectrum plot', exact=True).first.locator('.uplot')).to_have_count(1)
            page.get_by_role('combobox', name='Estimator', exact=True).select_option('welch')
            page.wait_for_load_state('networkidle')
            page.get_by_role('combobox', name='Estimator', exact=True).select_option('waterfall')
            settled(1)
            expect(page.get_by_role('group', name='Data source', exact=True).get_by_role('button', name='Full test', exact=True)).to_have_attribute('aria-pressed', 'true')
            page.get_by_role('group', name='Data source', exact=True).get_by_role('button', name='Selected TPs', exact=True).click()
            settled()
            passed('Partial source failure disables exports; Retry, short-interval error, late-response guard and FFT/Welch return work')

            setting('Bin spacing', '0.25')
            patch_session({'plotDensity': 'quad', 'plotConfigs': ['signal_N', 'reference_V']}, ('plotViewports',))
            settled()
            page.get_by_role('button', name='Export selected plots', exact=True).click()
            panel = page.get_by_role('dialog', name='Export selected plots', exact=True)
            panel.get_by_role('button', name='Clear selection', exact=True).click()
            panel.get_by_label('Select plot 1: signal_N', exact=True).check()
            panel.get_by_label('Select plot 2: reference_V', exact=True).check()
            panel.get_by_label('2 × 2', exact=True).check()
            with page.expect_download(timeout=60000) as download:
                panel.get_by_role('button', name='Download CSV ZIP', exact=True).click()
            path = output / 'selected-grids.zip'
            download.value.save_as(path)
            payload = posts[-1]['body']
            with zipfile.ZipFile(path) as archive:
                entries = [n for n in archive.namelist() if n.endswith('.csv')]
                assert len(entries) == 2
                assert 'kiha-waterfall-v2' in archive.read('analysis.json').decode()
                for entry in entries:
                    rows = list(csv.DictReader(io.StringIO(archive.read(entry).decode())))
                    column = rows[0]['variable']
                    options = next(item['request'] for item in payload['plots'] if item['request']['column'] == column)
                    assert_grid_csv(rows, source_grids(column), options)
            with page.expect_download(timeout=60000) as download:
                panel.get_by_role('button', name='Download combined PNG', exact=True).click()
            path = output / 'selected-images.zip'
            download.value.save_as(path)
            with zipfile.ZipFile(path) as archive:
                entry = next(n for n in archive.namelist() if n.endswith('.png'))
                (output / 'selected-images.png').write_bytes(archive.read(entry))
                assert 'kiha-waterfall-v2' in archive.read('analysis.json').decode()
            panel.get_by_role('button', name='Close', exact=True).click()
            passed('Real single and selected CSV/PNG packages preserve the displayed v2 grid and source/method provenance')

            patch_session({'plotDensity': 'single'}, ('plotViewports',))
            settled()
            domain = scales()
            set_axes([2000, 2100], domain[0]['y'])
            expect(plot()).to_contain_text('No FFT cells in this view. Press Home to reset axes.')
            assert all(not data['magnitude'] for data in source_grids().values())
            menu('Export CSV / PNG…')
            panel = page.get_by_role('dialog', name='Export signal_N plot', exact=True)
            expect(panel.get_by_role('button', name='Download CSV', exact=True)).to_be_disabled()
            expect(panel.get_by_role('button', name='Download PNG', exact=True)).to_be_disabled()
            page.keyboard.press('Escape')
            plot().screenshot(path=str(output / 'empty-viewport.png'))
            plot().get_by_label(f'Waterfall canvas for Two tones · {A} · TP 7', exact=True).focus()
            page.keyboard.press('Home')
            settled()
            assert scales() == domain
            assert all(data['magnitude'] for data in source_grids().values())
            passed('Out-of-domain viewport explains empty cells, disables CSV/PNG and Home restores full source data')
            plot().get_by_role('button', name='Expand signal_N', exact=True).click()
            settled()
            cdp = context.new_cdp_session(page)
            window = cdp.send('Browser.getWindowForTarget')['windowId']
            for width, zoom in ((1100, 1), (1600, 1.25), (1600, 1.5)):
                cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': 1100}})
                set_browser_zoom(worker, page, zoom)
                settled()
                assert plot().evaluate('el=>el.scrollWidth<=el.clientWidth+1')
                for chart in plot().locator('.uplot').all():
                    assert chart.evaluate('el=>el.clientWidth>180&&el.clientHeight>100')
                for label in ('Bin spacing', 'Band', 'Overlap'):
                    control = page.get_by_role('combobox', name=label, exact=True)
                    expect(control).to_be_visible()
                    assert control.evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth}')
                capture_browser_view(cdp, output / f'desktop-{width}-{zoom}.png')
            assert not errors, errors
            assert dataset_hashes(dataset) == before, 'Source files changed'
            passed('1100 px desktop, maximize, actual 125%/150% zoom, unchanged source files and no browser errors')
            (output / 'results.json').write_text(json.dumps({'checks': checks, 'posts': posts, 'requests': requests,
                'source_files_unchanged': len(before), 'browser_errors': errors}, indent=2))
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            print(page.locator('body').inner_text()[-8000:], flush=True)
            print(errors, flush=True)
            raise
        finally:
            context.close()
            request.dispose()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3351)
    parser.add_argument('--backend-port', type=int, default=8351)
    args = parser.parse_args()
    output = ROOT / 'data/verification/waterfall-detail'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kiha-waterfall-detail-') as directory:
        temporary = Path(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, dataset):
            run(web, api, dataset, temporary, output)
