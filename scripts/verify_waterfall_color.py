"""Native waterfall color-limit controls, persistence and export verification.

Uses a temporary dataset and isolated Chromium profile; never touches user data.
Python/Playwright manages the browser, and the repository Python 3.13 backend
performs native FFT work. Evidence remains under data/verification/waterfall-color.
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
from verify_waterfall_detail import assert_grid_csv


A, B = 'color_A', 'color_B'
FS, SECONDS = 2048, 16


def fixtures(request):
    for name, amplitude in ((A, 3), (B, 1)):
        lines = ['time,signal_N,reference_V,quiet_U']
        for i in range(FS * SECONDS):
            t = i / FS
            signal = amplitude * (math.sin(2 * math.pi * 84 * t) + .7 * math.sin(2 * math.pi * 85 * t))
            lines.append(f'{100+t:.12f},{signal:.15g},{math.cos(2*math.pi*40*t):.15g},0')
        assert upload(request, name, '\n'.join(lines))['status'] == 'ready'
        points = [{'id': 7, 'name': 'Color fixture', 'start_s': 0, 'end_s': SECONDS,
                   'start_idx': 0, 'end_idx': FS * SECONDS}]
        assert request.put(f'tests/{name}/testpoints', data={'test': name, 'version': 1, 'test_points': points}).ok
        for column in ('signal_N', 'reference_V', 'quiet_U'):
            assert request.get(f'tests/{name}/tp_stats', params={'col': column}).ok


def color_records(value):
    """Read actual image provenance without depending on container nesting."""
    if isinstance(value, dict):
        if 'color_range' in value:
            yield value
        for child in value.values():
            yield from color_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from color_records(child)


def run(web, api, dataset, temporary, output):
    extension = temporary / 'extension'
    extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Waterfall color verification',
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
        errors, posts, requests, checks = [], [], [], []
        latest = {}
        page.on('pageerror', lambda e: errors.append(str(e)))

        def instrument(route):
            response = route.fetch()
            source = response.text()
            needle = 'plot.current = u;'
            assert needle in source
            expose = " u.root.__waterfall = u; u.root.__color = {scale:[lo,hi], logColor, ticks:colorTicks, pixel:(x,y)=>Array.from(ctx.getImageData(x,rows-1-y,1,1).data)};"
            route.fulfill(response=response, body=source.replace(needle, needle + expose))

        def waterfall_route(route):
            response = route.fetch()
            if response.ok:
                data = response.json()
                test = route.request.url.split('/tests/')[1].split('/')[0]
                requests.append({'test': test, 'query': parse_qs(urlparse(route.request.url).query)})
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
            'plotConfigs': ['signal_N', 'reference_V', 'quiet_U'], 'plotsUserEdited': True, 'plotDensity': 'single',
            'viewMode': 'spectrum', 'specMode': 'waterfall', 'specSource': 'tp', 'specLogY': False, 'scatterCollapsed': True,
            'waterfallResolution': .25, 'waterfallBand': 'low', 'waterfallWindow': 1024, 'waterfallOverlap': 75}
        page.add_init_script(f'''if(!sessionStorage.getItem('color-seeded')){{localStorage.clear();sessionStorage.setItem('color-seeded','1');
            localStorage.setItem('ptt.analysis-session.v1',{json.dumps(json.dumps(session))});}}''')

        def plot(column='signal_N'):
            return page.get_by_role('group', name=f'{column} waterfall plot', exact=True).first

        def color_panel(column='signal_N', slot=0):
            target = page.get_by_role('group', name=f'{column} waterfall plot', exact=True).nth(slot)
            trigger = target.get_by_role('button', name=f'Color range for {column}', exact=True)
            if trigger.get_attribute('aria-expanded') != 'true':
                trigger.click()
            expect(trigger).to_have_attribute('aria-expanded', 'true')
            # The panel is portaled outside the plot. Its generated ID also
            # distinguishes slots that show the same variable.
            panel_id = trigger.get_attribute('aria-controls')
            assert panel_id, 'Open color range trigger must identify its panel'
            panel = page.locator(f'[id={json.dumps(panel_id)}]')
            expect(panel).to_be_visible()
            return panel

        def form(column='signal_N', slot=0):
            return color_panel(column, slot).get_by_role('form', name='Waterfall color range', exact=True)

        def settled(columns=('signal_N',)):
            page.wait_for_load_state('networkidle')
            for column in columns:
                expect(plot(column).locator('.uplot')).to_have_count(2)
                expect(plot(column)).not_to_contain_text('Calculating waterfall FFT')
                expect(plot(column)).not_to_contain_text('Refining visible grid')
            wait_for_chart_layout(page)

        def scales(column='signal_N'):
            return plot(column).locator('.uplot').evaluate_all('els=>els.map(el=>({x:[el.__waterfall.scales.x.min,el.__waterfall.scales.x.max],y:[el.__waterfall.scales.y.min,el.__waterfall.scales.y.max]}))')

        def colors(column='signal_N'):
            return plot(column).locator('.uplot').evaluate_all('els=>els.map(el=>({scale:el.__color.scale,log:el.__color.logColor}))')

        def assert_color(bounds, log=False, column='signal_N'):
            assert colors(column) == [{'scale': bounds, 'log': log}] * 2, colors(column)

        def apply(low, high, column='signal_N', keyboard=False):
            form(column).get_by_label('Color min', exact=True).fill(str(low))
            form(column).get_by_label('Color max', exact=True).fill(str(high))
            if keyboard:
                form(column).get_by_label('Color max', exact=True).press('Enter')
            else:
                form(column).get_by_role('button', name='Apply', exact=True).click()
            settled((column,))

        def auto(column='signal_N'):
            form(column).get_by_role('button', name='Auto', exact=True).focus()
            page.keyboard.press('Enter')
            settled((column,))

        def set_axes(x, y):
            plot().locator('.uplot').first.evaluate('''(el,r)=>{const u=el.__waterfall;u.batch(()=>{
                u.setScale('x',{min:r.x[0],max:r.x[1]});u.setScale('y',{min:r.y[0],max:r.y[1]});});}''', {'x': x, 'y': y})
            settled()

        def menu(action):
            plot().get_by_role('button', name='Plot actions for signal_N', exact=True).focus()
            page.keyboard.press('Enter')
            page.get_by_role('menuitem', name=action, exact=True).click()

        def patch_session(changes):
            page.wait_for_timeout(400)
            page.evaluate('''({changes,sources})=>{const s=JSON.parse(localStorage.getItem('ptt.analysis-session.v1'));
                Object.assign(s,{sources},changes);localStorage.setItem('ptt.analysis-session.v1',JSON.stringify(s));}''',
                {'changes': changes, 'sources': sources})
            page.reload()

        def export(fmt, tag, bounds=None, log=False):
            displayed = {name: latest[(name, 'signal_N')] for name in (A, B)}
            menu('Export CSV / PNG…')
            panel = page.get_by_role('dialog', name='Export signal_N plot', exact=True)
            with page.expect_download(timeout=60000) as download:
                panel.get_by_role('button', name=f'Download {fmt}', exact=True).click()
            path = output / f'{tag}.zip'
            download.value.save_as(path)
            csv_data = None
            with zipfile.ZipFile(path) as archive:
                metadata = json.loads(archive.read('analysis.json'))
                assert 'kiha-waterfall-v2' in json.dumps(metadata)
                if fmt == 'CSV':
                    payload = posts[-1]['body']
                    csv_data = archive.read(next(n for n in archive.namelist() if n.endswith('.csv')))
                    rows = list(csv.DictReader(io.StringIO(csv_data.decode())))
                    assert_grid_csv(rows, displayed, payload)
                else:
                    entry = next(n for n in archive.namelist() if n.endswith('.png'))
                    (output / f'{tag}.png').write_bytes(archive.read(entry))
                    records = list(color_records(metadata))
                    assert len(records) == 2, records
                    assert all(r['color_range'] == bounds for r in records), records
                    assert all(r['color_transform'] == ('log10' if log else 'linear') for r in records), records
                    assert all(r['color_range_mode'] == 'manual' for r in records), records
                    (output / f'{tag}-metadata.json').write_text(json.dumps(metadata, indent=2))
            page.keyboard.press('Escape')
            expect(panel).not_to_be_visible()
            return csv_data

        def passed(message):
            checks.append(message)
            print('PASS: ' + message, flush=True)

        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            page.goto(web)
            settled()
            expect(color_panel()).to_contain_text('Auto · U')
            initial_color = colors()
            initial_axes = scales()
            assert initial_color[0] == initial_color[1]
            assert initial_color[0]['scale'][0] == 0
            max_amplitude = max(v for test in (A, B) for row in latest[(test, 'signal_N')]['magnitude'] for v in row)
            assert math.isclose(initial_color[0]['scale'][1], max_amplitude)
            menu('Analysis details…')
            details = page.get_by_role('dialog', name='Waterfall analysis for signal_N', exact=True)
            expect(details).to_be_visible()
            page.keyboard.press('Escape')
            expect(details).not_to_be_visible()
            # Immediately reopen the shared plot menu by keyboard after details
            # closes, guarding against deferred scrolling/focus from the dialog.
            baseline_csv = export('CSV', 'auto-grid')
            before_requests = len(requests)
            apply(.2, 1.2, keyboard=True)
            expect(form().get_by_label('Color max', exact=True)).to_be_focused()
            assert_color([.2, 1.2])
            assert scales() == initial_axes
            assert len(requests) == before_requests
            for index, test in enumerate((A, B)):
                data = latest[(test, 'signal_N')]
                high = max(range(len(data['magnitude'][0])), key=lambda x: data['magnitude'][0][x])
                low = min(range(len(data['magnitude'][0])), key=lambda x: data['magnitude'][0][x])
                pixels = plot().locator('.uplot').nth(index).evaluate('(el,p)=>p.map(x=>el.__color.pixel(x,0))', [low, high])
                assert pixels[0] == [10, 14, 90, 255], pixels
                if data['magnitude'][0][high] >= 1.2:
                    assert pixels[1] == [192, 22, 30, 255], pixels
            plot().screenshot(path=str(output / 'linear-manual.png'))
            passed('Legacy auto range shares source maximum; keyboard Apply uses common limits, clamps endpoints, preserves axes and avoids FFT requests')

            for low, high in (('', '2'), ('1', ''), ('2', '1'), ('1', '1'), ('-.1', '2')):
                apply(low, high)
                expect(form().get_by_role('alert')).to_be_visible()
                assert_color([.2, 1.2])
                assert scales() == initial_axes
                assert len(requests) == before_requests
            # Text inputs accept an explicit nonfinite string; number inputs use
            # an overflowing exponent, which exercises browser/handler rejection.
            nonfinite = '1e309' if form().get_by_label('Color max', exact=True).get_attribute('type') == 'number' else 'Infinity'
            apply('0', nonfinite)
            expect(form().get_by_role('alert')).to_be_visible()
            assert_color([.2, 1.2])
            auto()
            expect(form().get_by_role('button', name='Auto', exact=True)).to_be_focused()
            assert colors() == initial_color
            apply(.2, 1.2)
            assert export('CSV', 'manual-grid') == baseline_csv
            export('PNG', 'manual-linear-image', [.2, 1.2])
            passed('Empty, inverted, equal, negative-linear and nonfinite drafts do not commit; Auto works by keyboard; CSV is unchanged and PNG records limits')

            before_requests = len(requests)
            apply(1, 1.00001)
            assert_color([1, 1.00001])
            expect(plot()).to_contain_text('manual range 1 to 1.00001')
            ticks = plot().locator('.uplot').evaluate_all('els=>els.map(el=>el.__color.ticks)')
            assert ticks[0] == ticks[1], ticks
            assert len({tick['label'] for tick in ticks[0]}) == 3, ticks
            assert ticks[0][0]['label'] == '1' and ticks[0][-1]['label'] == '1.00001', ticks
            assert len(requests) == before_requests
            assert scales() == initial_axes
            plot().screenshot(path=str(output / 'narrow-range.png'))
            export('PNG', 'narrow-range-image', [1, 1.00001])
            apply(.2, 1.2)
            passed('Narrow manual range 1–1.00001 keeps distinct truthful color-bar labels and exact visible/export limits')

            before_requests = len(requests)
            page.get_by_role('button', name='Log color', exact=True).click()
            settled()
            expect(color_panel()).to_contain_text('Auto · log10(U)')
            auto_log = colors()
            assert math.isclose(auto_log[0]['scale'][1] - auto_log[0]['scale'][0], 6)
            apply(-3, -.2, keyboard=True)
            assert_color([-3, -.2], True)
            page.get_by_role('button', name='Log color', exact=True).click()
            settled()
            assert_color([.2, 1.2])
            page.get_by_role('button', name='Log color', exact=True).click()
            settled()
            assert_color([-3, -.2], True)
            assert len(requests) == before_requests
            assert scales() == initial_axes
            page.wait_for_timeout(400)
            page.reload()
            settled()
            assert_color([-3, -.2], True)
            export('PNG', 'manual-log-image', [-3, -.2], True)
            auto()
            assert colors() == auto_log
            page.get_by_role('button', name='Log color', exact=True).click()
            settled()
            assert_color([.2, 1.2])
            passed('Log limits allow negatives, remain separate from linear limits, survive reload and export; Auto clears only active mode')

            set_axes([80, 90], [4, 10])
            zoomed = scales()
            assert_color([.2, 1.2])
            apply(.1, 1.1)
            assert scales() == zoomed
            plot().get_by_role('button', name='Expand signal_N', exact=True).click()
            settled()
            assert scales() == zoomed
            assert_color([.1, 1.1])
            plot().get_by_role('button', name='Minimize signal_N', exact=True).click()
            settled()
            page.wait_for_timeout(400)
            page.reload()
            settled()
            assert scales() == zoomed
            assert_color([.1, 1.1])
            passed('Explicit range survives both-axis refinement, color edits, maximize/restore and viewport reload')

            patch_session({'plotDensity': 'quad'})
            settled(('signal_N', 'reference_V', 'quiet_U'))
            assert_color([.1, 1.1])
            apply(.05, .8, 'reference_V')
            assert_color([.05, .8], column='reference_V')
            assert_color([.1, 1.1])
            apply(.1, 2, 'quiet_U')
            assert_color([.1, 2], column='quiet_U')
            zero_pixels = plot('quiet_U').locator('.uplot').evaluate_all('els=>els.map(el=>el.__color.pixel(0,0))')
            assert zero_pixels == [[10, 14, 90, 255]] * 2
            page.get_by_role('button', name='Log color', exact=True).click()
            settled(('signal_N', 'reference_V', 'quiet_U'))
            apply(-2, 1, 'quiet_U')
            assert_color([-2, 1], True, 'quiet_U')
            assert plot('quiet_U').locator('.uplot').evaluate_all('els=>els.map(el=>el.__color.pixel(0,0))') == zero_pixels
            page.get_by_role('button', name='Log color', exact=True).click()
            settled(('signal_N', 'reference_V', 'quiet_U'))
            assert_color([.1, 1.1])
            assert_color([.05, .8], column='reference_V')
            page.wait_for_timeout(400)
            page.reload()
            settled(('signal_N', 'reference_V', 'quiet_U'))
            assert_color([.05, .8], column='reference_V')
            plot().screenshot(path=str(output / 'per-slot-limits.png'))
            passed('Three slots preserve independent per-variable limits; an all-zero source maps to the low color endpoint in linear and log modes')

            expected_ranges = [
                {'column': 'signal_N', 'linear': [.1, 1.1], 'log': None},
                {'column': 'reference_V', 'linear': [.05, .8], 'log': None},
                {'column': 'quiet_U', 'linear': [.1, 2], 'log': [-2, 1]},
            ]
            page.wait_for_function("ranges=>JSON.stringify(JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).waterfallColorRanges.slice(0,3))===JSON.stringify(ranges)", arg=expected_ranges)
            browser_state = page.evaluate("localStorage.getItem('ptt.analysis-session.v1')")
            apply(.5, 5)
            page.evaluate("saved=>localStorage.setItem('ptt.analysis-session.v1',saved)", browser_state)
            page.reload()
            settled(('signal_N', 'reference_V', 'quiet_U'))
            assert_color([.1, 1.1])
            assert_color([.05, .8], column='reference_V')
            assert_color([.1, 2], column='quiet_U')
            passed('Quiet browser restoration retains all three slots and both linear/log limits')

            patch_session({'plotConfigs': ['signal_N', 'signal_N', 'quiet_U']})
            settled(('signal_N', 'quiet_U'))
            duplicates = page.get_by_role('group', name='signal_N waterfall plot', exact=True)
            expect(duplicates).to_have_count(2)
            duplicate_form = form('signal_N', slot=1)
            expect(duplicate_form.get_by_role('button', name='Auto', exact=True)).to_have_attribute('aria-pressed', 'true')
            duplicate_form.get_by_label('Color min', exact=True).fill('0.3')
            duplicate_form.get_by_label('Color max', exact=True).fill('3.3')
            duplicate_form.get_by_role('button', name='Apply', exact=True).click()
            settled()
            assert_color([.1, 1.1])
            assert duplicates.nth(1).locator('.uplot').evaluate_all('els=>els.map(el=>el.__color.scale)') == [[.3, 3.3]] * 2
            page.wait_for_timeout(400)
            page.reload()
            settled()
            assert_color([.1, 1.1])
            assert duplicates.nth(1).locator('.uplot').evaluate_all('els=>els.map(el=>el.__color.scale)') == [[.3, 3.3]] * 2
            passed('A changed slot column discards incompatible limits; duplicate-variable slots keep different ranges through reload')

            patch_session({'plotDensity': 'single'})
            settled()
            plot().get_by_role('button', name='Expand signal_N', exact=True).click()
            settled()
            cdp = context.new_cdp_session(page)
            window = cdp.send('Browser.getWindowForTarget')['windowId']
            for width, zoom in ((1100, 1), (1600, 1.25), (1600, 1.5)):
                cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': 1100}})
                set_browser_zoom(worker, page, zoom)
                settled()
                assert_color([.1, 1.1])
                assert scales() == zoomed
                assert plot().evaluate('el=>el.scrollWidth<=el.clientWidth+1')
                for label in ('Color min', 'Color max'):
                    control = form().get_by_label(label, exact=True)
                    expect(control).to_be_visible()
                    assert control.evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth}')
                for label in ('Apply', 'Auto'):
                    expect(form().get_by_role('button', name=label, exact=True)).to_be_visible()
                capture_browser_view(cdp, output / f'desktop-{width}-{zoom}.png')
            assert not errors, errors
            assert dataset_hashes(dataset) == before, 'Source files changed'
            passed('1100 px desktop, actual 125%/150% browser zoom, readable controls, unchanged source files and no browser page errors')
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
    parser.add_argument('--frontend-port', type=int, default=3352)
    parser.add_argument('--backend-port', type=int, default=8352)
    args = parser.parse_args()
    output = ROOT / 'data/verification/waterfall-color'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kiha-waterfall-color-') as directory:
        temporary = Path(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, dataset):
            run(web, api, dataset, temporary, output)
