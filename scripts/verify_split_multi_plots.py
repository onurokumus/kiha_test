"""Split multi-line previews against isolated real uploads and native Chromium.

Global Python runs only stdlib/Playwright. Reuses verify_data_quality.servers so
the backend always runs in its Python 3.13 venv, with temporary fixture data.
No user datasets, profiles, listeners, or settings are changed. Evidence stays
in ignored data/verification/split-multi-plots; temporary fixtures are removed.
"""
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout
from verify_data_quality import ROOT, servers, upload, wait_until


ALPHA = 'split_multi_alpha'
BETA = 'split_multi_beta'
COLS = ['force_N', 'temperature_C', 'speed_rpm', 'pressure_bar', 'current_A',
        'voltage_V', 'torque_Nm', 'flow_lpm', 'vibration_g', 'all_missing', 'last_sensor']


def source_hashes(dataset):
    return {str(path.relative_to(dataset)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in dataset.rglob('*')
            if path.is_file() and (path.suffix in {'.parquet', '.csv'} or 'pyramid' in path.parts)}


def instrument(route):
    response = route.fetch()
    source = response.text()
    needle = re.compile(r'plotRef\.current = (u\d*);')
    assert needle.search(source), 'SplitPlot instrumentation assignment changed'
    route.fulfill(response=response, body=needle.sub(
        lambda match: match[0] + f' {match[1]}.root.__verificationPlot = {match[1]};', source))


def fixtures(request):
    rows = []
    for index in range(1200):
        signals = [round((slot + 1) * 10 + math.sin(index / (slot + 4)) * (slot + 1), 7)
                   for slot in range(len(COLS))]
        signals[9] = ''
        if 400 <= index < 420:
            signals[1] = ''
        rows.append(','.join(map(str, [index / 10, *signals])))
    assert upload(request, ALPHA, 'time,' + ','.join(COLS) + '\n' + '\n'.join(rows))['status'] == 'ready'
    assert upload(request, BETA, 'time,beta_force,beta_temp\n' + '\n'.join(
        f'{index / 10},{index},{50 + index / 100}' for index in range(400)))['status'] == 'ready'
    for test in (ALPHA, BETA):
        response = request.put(f'tests/{test}/testpoints', data={
            'version': 1, 'test': test, 'test_points': [
                {'id': 7, 'name': 'First interval', 'label': '', 'start_s': 10,
                 'end_s': 20, 'start_idx': 100, 'end_idx': 200},
                {'id': 9, 'name': 'Second interval', 'label': '', 'start_s': 25,
                 'end_s': 30, 'start_idx': 250, 'end_idx': 300}]})
        assert response.ok, response.text()


def run_checks(web, api, dataset, temporary, output):
    extension = temporary / 'zoom-extension'
    extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({
        'manifest_version': 3, 'name': 'Split multi-line zoom verification', 'version': '1.0',
        'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as playwright:
        request = playwright.request.new_context(base_url=api + '/')
        fixtures(request)
        original_sources = source_hashes(dataset)
        context = playwright.chromium.launch_persistent_context(
            str(temporary / 'profile'), channel='chromium', headless=True,
            no_viewport=True, accept_downloads=True,
            args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}',
                  '--window-size=1440,1000'])
        page = context.pages[0]
        page.set_default_timeout(10000)
        errors, preview_requests, results = [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
        page.on('request', lambda req: preview_requests.append(req.url)
                if re.search(r'/tests/[^/]+/data\?', req.url) else None)
        page.route('**/src/components/split/SplitPlot.tsx*', instrument)
        def envelope_preview(route):
            query = parse_qs(urlparse(route.request.url).query)
            if query.get('cols') == ['force_N'] and 't0' not in query:
                response = route.fetch(url=route.request.url.replace('display=auto', 'display=envelope'))
                route.fulfill(response=response)
            else:
                route.continue_()
        page.route(api + f'/tests/{ALPHA}/data?*', envelope_preview)
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp = context.new_cdp_session(page)

        def cards():
            return page.locator('[data-split-plot]')

        def card(index):
            return page.get_by_role('region', name=f'Split plot {index}', exact=True)

        def settle(count=None):
            page.wait_for_load_state('networkidle')
            if count is not None:
                expect(cards()).to_have_count(count)
            for target in cards().all():
                expect(target.locator('.uplot')).to_have_count(1)
            wait_for_chart_layout(page)

        def choose(index, column, wait=True):
            picker = page.get_by_role('button', name=f'Variable for split plot {index}', exact=True)
            picker.scroll_into_view_if_needed()
            picker.focus()
            page.keyboard.press('Enter')
            search = page.get_by_role('combobox').last
            search.fill(column)
            option = page.get_by_role('option', name=column, exact=True)
            expect(option).to_be_visible()
            page.keyboard.press('ArrowDown')
            page.keyboard.press('Enter')
            expect(picker).to_contain_text(column)
            expect(picker).to_be_focused()
            if wait:
                settle()

        def choose_test(test):
            page.get_by_role('button', name='Active test', exact=True).click()
            page.get_by_role('combobox').last.fill(test)
            page.get_by_role('option', name=re.compile('^' + re.escape(test))).click()
            settle()

        def variables():
            return [target.get_by_role('button', name=re.compile('^Variable for split plot ')).inner_text().strip()
                    for target in cards().all()]

        def x_ranges():
            return [target.locator('.uplot').evaluate('el=>{const u=el.__verificationPlot;return [u.scales.x.min,u.scales.x.max]}')
                    for target in cards().all()]

        def linked(expected=None):
            ranges = x_ranges()
            assert ranges and all(all(abs(left - right) < 1e-8 for left, right in zip(pair, ranges[0]))
                                  for pair in ranges), ranges
            if expected is not None:
                assert all(abs(left - right) < 1e-7 for left, right in zip(ranges[0], expected)), (ranges, expected)
            return ranges[0]

        def drag_zoom(index, left=.23, right=.57):
            over = card(index).locator('.u-over')
            over.scroll_into_view_if_needed()
            box = over.bounding_box()
            page.mouse.move(box['x'] + box['width'] * left, box['y'] + box['height'] * .62)
            page.mouse.down()
            page.mouse.move(box['x'] + box['width'] * right, box['y'] + box['height'] * .62, steps=8)
            page.mouse.up()
            settle()

        def download(link, label):
            link.scroll_into_view_if_needed()
            with page.expect_download() as event:
                link.click()
            value = event.value
            assert value.failure() is None
            target = output / label
            value.save_as(target)
            return value.suggested_filename, target.read_bytes()

        def overlays_match(name='First interval'):
            # Compare normalized positions relative to each canvas. Different Y
            # axis labels may have different widths, but the same TP must represent
            # identical time coordinates in every preview.
            positions = []
            for target in cards().all():
                target.scroll_into_view_if_needed()
                positions.append(target.evaluate('''(el,name)=>{
                    const over=el.querySelector('.u-over').getBoundingClientRect();
                    const label=[...el.querySelectorAll('*')].find(n=>n.childElementCount===0&&n.textContent===name);
                    if(!label)throw new Error('Missing TP overlay '+name);
                    const r=label.getBoundingClientRect();return (r.left-over.left)/over.width;
                }''', name))
            assert max(positions) - min(positions) < .002, positions

        try:
            page.goto(web)
            page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='Uploads', exact=True).click()
            page.get_by_role('searchbox', name='Search upload history').fill(ALPHA)
            page.get_by_role('button', name=f'Analyze {ALPHA}', exact=True).click()
            page.get_by_role('button', name='Split', exact=True).click()
            settle(1)
            expect(page.get_by_role('button', name='Remove split plot 1', exact=True)).to_be_disabled()
            choose(1, COLS[0])
            for index in (2, 3):
                add = page.get_by_role('button', name='Add line plot', exact=True)
                add.focus()
                page.keyboard.press('Enter')
                settle(index)
                choose(index, COLS[index - 1])
            assert variables() == COLS[:3], variables()
            linked([0, 120])
            overlays_match()
            assert card(1).locator('.uplot').evaluate('el=>el.__verificationPlot.bands.length') == 1
            over = card(3).locator('.u-over'); over.scroll_into_view_if_needed()
            rect = over.bounding_box()
            page.mouse.move(rect['x'] + rect['width'] * .64, rect['y'] + rect['height'] * .5)
            cursors = [target.locator('.uplot').evaluate('el=>{const u=el.__verificationPlot;return u.cursor.left/u.over.clientWidth}')
                       for target in cards().all()]
            assert all(abs(value - .64) < .002 for value in cursors), cursors
            results.append('Three independent variables added and picked by keyboard; linked initial axes and TP overlays')
            print('PASS:', results[-1], flush=True)

            # Every source chart changes the same X range, with exact endpoints.
            drag_zoom(3)
            zoomed = linked()
            assert 0 < zoomed[0] < zoomed[1] < 119.9, zoomed
            assert card(1).locator('.uplot').evaluate('el=>el.__verificationPlot.bands.length') == 0
            card(1).get_by_role('button', name='Reset zoom', exact=True).click()
            settle(); linked([0, 120])
            drag_zoom(1)
            over = card(2).locator('.u-over')
            over.scroll_into_view_if_needed(); over.dblclick(position={'x': 300, 'y': 100})
            settle(); linked([0, 120])
            page.get_by_role('button', name='🔍', exact=True).first.click()
            settle(); linked([8.5, 21.5]); overlays_match()
            card(3).get_by_role('button', name='Reset zoom', exact=True).click()
            settle(); linked([0, 120])
            results.append('Envelope/raw transition, linked cursor, third/first card drag zoom, second-card double-click, cross-card reset, and table zoom link all axes')
            print('PASS:', results[-1], flush=True)

            # One shared draft: edit an edge using a handle on the third card,
            # then change the plot list and compare the same table/export bounds.
            if card(3).locator('[style*="cursor: ew-resize"]').count() == 0:
                card(3).get_by_text('First interval', exact=True).click()
            handle = card(3).locator('[style*="cursor: ew-resize"]').last
            handle.scroll_into_view_if_needed()
            box = handle.bounding_box()
            page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] * .55)
            page.mouse.down(); page.mouse.move(box['x'] + 22, box['y'] + box['height'] * .55, steps=6); page.mouse.up()
            end_input = page.get_by_role('spinbutton', name='End seconds for TP 7', exact=True)
            assert float(end_input.input_value()) > 20, end_input.input_value()
            draft_end = end_input.input_value()
            choose(2, COLS[3])
            remove = page.get_by_role('button', name='Remove split plot 2', exact=True)
            remove.focus(); page.keyboard.press('Enter'); settle(2)
            expect(end_input).to_have_value(draft_end)
            expect(page.get_by_role('button', name='save *', exact=True)).to_be_enabled()
            assert variables() == [COLS[0], COLS[2]], variables()
            overlays_match()
            name, draft_csv = download(page.get_by_role('link', name='Download draft CSV for TP 7', exact=True), 'draft.csv')
            assert name == f'{ALPHA}_tp7_draft.csv', name
            rows = list(csv.DictReader(io.StringIO(draft_csv.decode())))
            assert rows and {row['test_point_id'] for row in rows} == {'7'}
            assert request.get(f'tests/{ALPHA}/testpoints').json()['test_points'][0]['end_s'] == 20
            page.get_by_role('button', name='save *', exact=True).click()
            expect(page.get_by_role('button', name='save', exact=True)).to_be_disabled()
            saved = request.get(f'tests/{ALPHA}/testpoints').json()
            assert saved['test_points'][0]['end_s'] == float(draft_end)
            name, saved_csv = download(page.get_by_role('link', name='Download CSV for TP 7', exact=True), 'saved.csv')
            assert name == f'{ALPHA}_tp7.csv' and saved_csv == draft_csv
            name, json_bytes = download(page.get_by_role('button', name='download', exact=True), 'testpoints.json')
            assert name == f'{ALPHA}.testpoints.json'
            assert json.loads(json_bytes)['test_points'][0]['end_s'] == float(draft_end)
            end_input.fill('35'); page.keyboard.press('Tab')
            page.get_by_role('button', name='reset', exact=True).click()
            expect(end_input).to_have_value(draft_end)
            results.append('Third-card TP edge editing preserves shared draft through variable change/removal; Save/reset/CSV/JSON retain bounds')
            print('PASS:', results[-1], flush=True)

            # Persistence is per test; tab switches must not require a TP save.
            page.get_by_role('button', name='Uploads', exact=True).click()
            page.get_by_role('button', name='Split', exact=True).click(); settle(2)
            assert variables() == [COLS[0], COLS[2]]
            page.reload(); page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='Split', exact=True).click(); settle(2)
            assert variables() == [COLS[0], COLS[2]]
            choose_test(BETA); settle(1)
            assert variables() == ['beta_force'], variables()
            page.get_by_role('button', name='Add line plot', exact=True).click(); settle(2)
            assert variables() == ['beta_force', 'beta_temp']
            expect(page.get_by_role('button', name='Add line plot', exact=True)).to_be_disabled()
            choose_test(ALPHA); settle(2)
            assert variables() == [COLS[0], COLS[2]]
            results.append('Per-test plot choices survive tab switches/reload and differing variable catalogs; two-column capacity is enforced')
            print('PASS:', results[-1], flush=True)

            # An empty or failed second signal must not mask another plot or
            # relabel previous response data as the newly selected variable.
            def empty_preview(route):
                query = parse_qs(urlparse(route.request.url).query)
                if query.get('cols') == ['temperature_C']:
                    response = route.fetch()
                    body = response.json()
                    body['series']['temperature_C'] = [None] * len(body['t'])
                    route.fulfill(response=response, json=body)
                else:
                    route.continue_()
            page.route(api + f'/tests/{ALPHA}/data?*', empty_preview)
            choose(2, 'temperature_C')
            expect(card(2)).to_contain_text('No samples in this range')
            expect(card(1)).not_to_contain_text('No samples in this range')
            page.unroute(api + f'/tests/{ALPHA}/data?*', empty_preview)
            failed_pattern = api + f'/tests/{ALPHA}/data?*'
            def fail_preview(route):
                column = parse_qs(urlparse(route.request.url).query).get('cols', [''])[0]
                if column == 'pressure_bar':
                    route.fulfill(status=503, json={'detail': 'Isolated preview read failure'})
                else:
                    route.continue_()
            page.route(failed_pattern, fail_preview)
            choose(2, 'pressure_bar', wait=False)
            expect(card(2)).to_contain_text('Could not load the split preview')
            expect(card(1)).not_to_contain_text('Could not load the split preview')
            expect(card(2).locator('.uplot')).to_have_count(0)
            page.unroute(failed_pattern, fail_preview)
            card(2).get_by_role('button', name='Retry', exact=True).click()
            settle(2)
            choose(2, 'temperature_C', wait=False)
            choose(2, 'voltage_V', wait=False)
            choose_test(BETA)
            choose_test(ALPHA); settle(2)
            assert variables() == [COLS[0], 'voltage_V'], variables()
            for target in cards().all():
                displayed = target.locator('.uplot').evaluate('el=>el.__verificationPlot.series.slice(1).map(s=>s.label)')
                selected = target.get_by_role('button', name=re.compile('^Variable for split plot ')).inner_text().strip()
                assert all(label == selected or label.startswith(selected + ' ') for label in displayed), (selected, displayed)
            results.append('Per-card missing/error/Retry states and rapid variable/test switches never show stale signal labels')
            print('PASS:', results[-1], flush=True)

            for count in range(3, 10):
                page.get_by_role('button', name='Add line plot', exact=True).click(); settle(count)
            assert len(set(variables())) == 9, variables()
            expect(page.get_by_role('button', name='Add line plot', exact=True)).to_be_disabled()
            # At least one tall card must remain reachable via normal scrolling.
            card(9).scroll_into_view_if_needed()
            expect(card(9).get_by_role('button', name='Remove split plot 9', exact=True)).to_be_visible()
            page.screenshot(path=str(output / 'nine-plots.png'), full_page=True)
            for count in range(9, 3, -1):
                page.get_by_role('button', name=f'Remove split plot {count}', exact=True).click(); settle(count - 1)
            window = cdp.send('Browser.getWindowForTarget')['windowId']
            initial_dpr = page.evaluate('devicePixelRatio')
            for width, factor in [(1100, 1), (1440, 1.25), (1440, 1.5)]:
                cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': 1000}})
                set_browser_zoom(worker, page, factor)
                page.wait_for_function('dpr=>Math.abs(devicePixelRatio-dpr)<.001', arg=initial_dpr * factor)
                settle(3)
                for index in (1, 3):
                    target = card(index); target.scroll_into_view_if_needed()
                    assert target.evaluate('el=>{const r=el.getBoundingClientRect();return el.scrollWidth<=el.clientWidth+1&&r.left>=0&&r.right<=innerWidth+1}'), (width, factor, index)
                    choose(index, variables()[index - 1])
                    capture_browser_view(cdp, output / f'plot-{index}-{width}-{factor}.png')
                overlays_match(); linked()
                drag_zoom(3); linked()
                card(1).get_by_role('button', name='Reset zoom', exact=True).click(); settle(); linked([0, 120])
            set_browser_zoom(worker, page, 1)
            results.append('Nine distinct plot capacity, tall-stack access, 1100px resize, actual 125%/150% zoom, picker and linked gestures pass')
            print('PASS:', results[-1], flush=True)

            assert source_hashes(dataset) == original_sources, 'Preview/TP changes modified source samples'
            # Only this explicit isolated fixture edit may change source hashes.
            selected_before_drop = variables()
            dropped = selected_before_drop[1]
            response = request.post(f'tests/{ALPHA}/edit', data={'drop': [dropped]})
            assert response.ok, response.text()
            wait_until(lambda: dropped not in request.get(f'tests/{ALPHA}').json()['columns'], 'drop selected fixture column')
            page.reload(); page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='Split', exact=True).click(); settle()
            assert dropped not in variables(), variables()
            assert variables() and all(value in COLS and value != dropped for value in variables())
            choose_test(BETA)
            for url in preview_requests[-4:]:
                if f'/tests/{BETA}/data?' in url:
                    assert set(parse_qs(urlparse(url).query).get('cols', [''])[0].split(',')) <= {'beta_force', 'beta_temp'}, url
            results.append('Real selected-column removal reconciles saved plot choices without stale requests for the next test')
            print('PASS:', results[-1], flush=True)
            unexpected = [error for error in errors if '503' not in error and 'Isolated preview read failure' not in error]
            assert not unexpected, unexpected
            (output / 'results.json').write_text(json.dumps({
                'checks': results, 'original_source_files': len(original_sources),
                'unexpected_browser_errors': unexpected, 'preview_requests': len(preview_requests)}, indent=2))
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            print('Browser errors:', errors, flush=True)
            print('Visible text:', page.locator('body').inner_text()[-12000:], flush=True)
            raise
        finally:
            context.close()
            request.dispose()


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3320)
    parser.add_argument('--backend-port', type=int, default=8320)
    args = parser.parse_args()
    output = ROOT / 'data/verification/split-multi-plots'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-split-multi-') as directory:
        temporary = Path(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, dataset):
            run_checks(web, api, dataset, temporary, output)
    print('PASS: temporary fixtures/profile removed and owned servers stopped', flush=True)


if __name__ == '__main__':
    main()
