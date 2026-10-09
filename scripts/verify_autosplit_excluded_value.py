"""Isolated native Chromium checks for the auto-split exact-value exclusion.

Run with global Python + Playwright. The shared server helper launches the
backend with its Python 3.13 venv and temporary data. No user data or browser
settings are touched. Evidence remains in data/verification/autosplit-excluded-value.
"""
import argparse
import csv
import json
from pathlib import Path
import re
import sys
import tempfile

from playwright.sync_api import Error, expect, sync_playwright

from verify_browser_zoom import capture_browser_view, set_browser_zoom
from verify_data_quality import ROOT, servers, upload
from verify_multi_variable_autosplit import hashes


ALPHA = 'excluded_value_alpha'
BETA = 'excluded_value_beta'
EXPECTED = [(0, 10), (40, 50), (60, 70), (70, 80)]
BASELINE = [(0, 10), (10, 20), (30, 40), (40, 50), (60, 70), (70, 80)]
PREFIX = 'ptt.auto-split.v1:'


def fixtures(request):
    # Missing > zero > custom precedence: the last two exclusion groups overlap.
    # -2.5001 deliberately survives an exact -2.5 comparison.
    runs = [(0, 10, 1, 1), (10, 20, -2.5, 1), (20, 30, 2, 0),
            (30, 40, 2, -2.5), (40, 50, 3, 3), (50, 55, '', -2.5),
            (55, 60, 0, -2.5), (60, 70, 4, 4), (70, 80, -2.5001, 4)]
    content = 'time,run_id,state_id,load_N\n' + '\n'.join(
        f'{index / 10},{run},{state},{12.125 + index * .013}'
        for start, end, run, state in runs for index in range(start, end))
    for name in (ALPHA, BETA):
        assert upload(request, name, content)['status'] == 'ready'
        response = request.put(f'tests/{name}/testpoints', data={
            'version': 1, 'test': name, 'test_points': [{
                'id': 99, 'name': 'Existing saved interval', 'label': 'Saved definition',
                'start_s': 0, 'end_s': 8, 'start_idx': 0, 'end_idx': 80,
                'notes': 'Must survive exclusion preview'}]})
        assert response.ok, response.text()


def bounds(proposal):
    return [(point['start_idx'], point['end_idx']) for point in proposal['test_points']]


def run_checks(web, api, dataset, temporary, output):
    extension = temporary / 'extension'
    extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({
        'manifest_version': 3, 'name': 'Exact-value exclusion browser zoom verification',
        'version': '1.0', 'permissions': ['tabs'],
        'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as playwright:
        request = playwright.request.new_context(base_url=api + '/')
        fixtures(request)
        before_sources = hashes(dataset)
        saved_path = dataset / 'tests' / ALPHA / 'testpoints.json'
        saved_bytes = saved_path.read_bytes()
        native_cases, checks, errors, requests = {}, [], [], []
        context = playwright.chromium.launch_persistent_context(
            str(temporary / 'profile'), channel='chromium', headless=True,
            no_viewport=True, accept_downloads=True,
            args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}',
                  '--window-size=1440,1000'])
        page = context.pages[0]
        page.set_default_timeout(10000)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
        page.on('request', lambda req: requests.append(req.post_data_json)
                if req.method == 'POST' and req.url.endswith('/split/preview') else None)
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp = context.new_cdp_session(page)

        def passed(message):
            checks.append(message)
            print('PASS:', message, flush=True)

        def panel():
            return page.get_by_role('region', name='Auto-split', exact=True)

        def open_panel():
            button = page.get_by_role('button', name='Configure auto-split', exact=True)
            expect(button).to_be_enabled()
            button.focus()
            page.keyboard.press('Enter')
            expect(panel()).to_be_visible()

        def close_panel():
            panel().get_by_role('button', name='Close auto-split', exact=True).focus()
            page.keyboard.press('Enter')
            expect(panel()).to_have_count(0)

        def custom():
            return panel().get_by_role('checkbox', name='Exclude a value', exact=True)

        def value():
            return panel().get_by_role('textbox', name='Value to exclude', exact=True)

        def zero():
            return panel().get_by_role('checkbox', name=re.compile('Exclude.*zero'))

        def preview_button():
            return panel().get_by_role('button', name='Preview test points', exact=True)

        def choose(index, column):
            picker = panel().get_by_role('button', name=f'Auto-split variable {index}', exact=True)
            picker.focus(); page.keyboard.press('Enter')
            page.get_by_role('combobox').last.fill(column)
            expect(page.get_by_role('option', name=re.compile('^' + re.escape(column) + r'(?:\s|$)'))).to_be_visible()
            page.keyboard.press('ArrowDown'); page.keyboard.press('Enter')
            expect(picker).to_contain_text(column)

        def preview(expected_bounds):
            with page.expect_response(lambda response: response.url.endswith('/split/preview')) as event:
                preview_button().focus(); page.keyboard.press('Enter')
            response = event.value
            assert response.ok, response.text()
            proposal = response.json()
            assert bounds(proposal) == expected_bounds, proposal
            apply = panel().get_by_role('button', name=f'Use {len(expected_bounds)} test points', exact=True)
            expect(apply).to_be_enabled()
            return proposal, apply

        def same_saved():
            assert saved_path.read_bytes() == saved_bytes, 'Preview or Apply changed saved test points'

        def choose_test(test):
            page.get_by_role('button', name='Active test', exact=True).click()
            page.get_by_role('combobox').last.fill(test)
            page.get_by_role('option', name=re.compile('^' + re.escape(test))).click()
            page.wait_for_load_state('networkidle')

        def assert_native_csv(path):
            with (dataset / 'tests' / ALPHA / 'raw.csv').open(newline='') as source:
                original = list(csv.DictReader(source))[:10]
            with path.open(newline='') as download:
                actual = list(csv.DictReader(download))
            assert len(actual) == 10
            assert {row['test_point_id'] for row in actual} == {'1'}
            for original_row, exported_row in zip(original, actual):
                assert set(exported_row) == set(original_row) | {'test_point_id'}
                assert {key: float(exported_row[key]) for key in original_row} == {
                    key: float(item) for key, item in original_row.items()}

        try:
            for label, ignore_zero, excluded, expected, counts in [
                ('negative_with_zero', True, -2.5, EXPECTED, (5, 15, 20)),
                ('negative_without_zero', False, -2.5,
                 [(0, 10), (20, 30), (40, 50), (60, 70), (70, 80)], (5, 0, 25)),
                ('zero_with_zero', True, 0, BASELINE, (5, 15, 0)),
                ('zero_without_zero', False, 0, BASELINE, (5, 0, 15)),
                ('disabled', True, None, BASELINE, (5, 15, 0)),
            ]:
                response = request.post(f'tests/{ALPHA}/split/preview', data={
                    'columns': ['run_id', 'state_id'], 'ignore_zero': ignore_zero,
                    'min_len_s': 1, 'exclude_value': excluded})
                assert response.ok, response.text()
                proposal = response.json()
                assert bounds(proposal) == expected, (label, proposal)
                assert tuple(proposal['excluded'][key] for key in
                             ('missing_samples', 'zero_samples', 'value_samples')) == counts, (label, proposal)
                native_cases[label] = proposal
            same_saved()
            passed('Native exact matches in either selected column; zero and missing overlaps count once; near-matching values survive')

            page.goto(web); page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='Uploads', exact=True).click()
            page.get_by_role('searchbox', name='Search upload history').fill(ALPHA)
            page.get_by_role('button', name=f'Analyze {ALPHA}', exact=True).click()
            page.get_by_role('button', name='Split', exact=True).click()
            page.get_by_role('textbox', name='Name for TP 99', exact=True).wait_for()
            open_panel()
            expect(custom()).not_to_be_checked(); expect(value()).to_be_disabled()
            expect(value()).to_have_value('')
            choose(1, 'run_id')
            panel().get_by_role('button', name=re.compile(r'^\+? ?Add variable$')).click()
            choose(2, 'state_id')
            custom().focus(); page.keyboard.press('Space')
            expect(custom()).to_be_checked(); expect(value()).to_be_enabled()
            for invalid in ['', 'not a number', 'NaN', 'Infinity', '1e999', '-']:
                value().fill(invalid)
                expect(value()).to_have_attribute('aria-invalid', 'true')
                expect(preview_button()).to_be_disabled()
            assert not requests, requests
            value().focus(); page.keyboard.press('Control+A'); page.keyboard.type('-2.5')
            proposal, _ = preview(EXPECTED)
            assert requests[-1]['exclude_value'] == -2.5, requests[-1]
            assert proposal['excluded']['value_samples'] == 20
            expect(panel().get_by_text('Value -2.5 samples (after zero exclusions)', exact=True)
                   .locator('..').locator('dd')).to_have_text('20')
            same_saved()
            expect(page.get_by_role('button', name='Save test points', exact=True)).to_be_disabled()
            passed('Keyboard enables and types a signed decimal; empty/text/nonfinite drafts block requests; Preview remains read-only')

            value().fill('-2.50e0')
            expect(panel().get_by_role('button', name=re.compile('^Use '))).to_be_disabled()
            preview(EXPECTED)
            assert requests[-1]['exclude_value'] == -2.5
            custom().uncheck()
            expect(value()).to_be_disabled()
            expect(panel().get_by_role('button', name=re.compile('^Use '))).to_be_disabled()
            preview(BASELINE)
            assert requests[-1]['exclude_value'] is None
            custom().check(); value().fill('0')
            proposal, _ = preview(BASELINE)
            assert proposal['excluded']['value_samples'] == 0 and proposal['excluded']['zero_samples'] == 15
            zero().uncheck()
            proposal, _ = preview(BASELINE)
            assert proposal['excluded']['value_samples'] == 15 and proposal['excluded']['zero_samples'] == 0
            zero().check(); value().fill('-2.5')
            preview(EXPECTED)
            passed('Exponent input works; value/toggle changes invalidate proposals; disabled sends null and both zero choices avoid double counting')

            held = []
            preview_url = api + f'/tests/{ALPHA}/split/preview'
            def hold(route):
                held.append((route, route.fetch()))
            page.route(preview_url, hold)
            preview_button().click()
            page.wait_for_timeout(100)
            assert held
            value().fill('-3.5')
            for route, response in held:
                try:
                    route.fulfill(response=response)
                except Error as error:
                    assert re.search('cancel|abort|closed', str(error), re.I), str(error)
            page.unroute(preview_url)
            page.wait_for_load_state('networkidle')
            expect(panel().get_by_role('button', name=re.compile('^Use '))).to_be_disabled()
            same_saved()
            value().fill('-2.5'); preview(EXPECTED)
            passed('A held old response cannot restore a proposal after the exclusion value changes')

            close_panel(); choose_test(BETA); open_panel()
            expect(custom()).not_to_be_checked(); expect(value()).to_be_disabled()
            expect(value()).to_have_value('')
            custom().check(); value().fill('3')
            close_panel(); choose_test(ALPHA); open_panel()
            expect(custom()).to_be_checked(); expect(value()).to_have_value('-2.5')
            page.reload(); page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='Split', exact=True).click()
            # Workspace active-test recovery is debounced independently of the
            # immediate per-test settings; select the intended test explicitly.
            choose_test(ALPHA)
            open_panel()
            expect(custom()).to_be_checked(); expect(value()).to_have_value('-2.5')
            preview(EXPECTED)
            close_panel(); choose_test(BETA)
            # Seed a pre-feature setting into this isolated profile only.
            page.evaluate('([key, setting])=>localStorage.setItem(key, JSON.stringify(setting))',
                          [PREFIX + BETA, {'columns': ['run_id', 'state_id'], 'ignoreZero': True, 'minDuration': '1'}])
            open_panel()
            expect(custom()).not_to_be_checked(); expect(value()).to_be_disabled()
            expect(value()).to_have_value('')
            preview(BASELINE)
            assert requests[-1]['exclude_value'] is None
            close_panel(); choose_test(ALPHA); open_panel()
            expect(custom()).to_be_checked(); expect(value()).to_have_value('-2.5')
            passed('Per-test preferences and reload retain exclusion; legacy settings safely default to disabled with an empty draft')

            _, apply = preview(EXPECTED)
            apply.focus(); page.keyboard.press('Enter')
            for point_id, (start, end) in enumerate(EXPECTED, 1):
                assert float(page.get_by_role('textbox', name=f'Start seconds for TP {point_id}', exact=True).input_value()) == start / 10
                assert float(page.get_by_role('textbox', name=f'End seconds for TP {point_id}', exact=True).input_value()) == end / 10
            same_saved()
            with page.expect_download() as event:
                page.get_by_role('link', name='Download draft CSV for TP 1', exact=True).click()
            assert event.value.suggested_filename == f'{ALPHA}_tp1_draft.csv'
            event.value.save_as(output / 'draft-tp1.csv')
            assert_native_csv(output / 'draft-tp1.csv')
            same_saved()
            page.get_by_role('button', name='Save test points', exact=True).click()
            expect(page.get_by_role('button', name='Save test points', exact=True)).to_be_disabled()
            assert bounds(request.get(f'tests/{ALPHA}/testpoints').json()) == EXPECTED
            with page.expect_download() as event:
                page.get_by_role('link', name='Download CSV for TP 1', exact=True).click()
            event.value.save_as(output / 'saved-tp1.csv')
            assert_native_csv(output / 'saved-tp1.csv')
            assert (output / 'saved-tp1.csv').read_bytes() == (output / 'draft-tp1.csv').read_bytes()
            passed('Apply modifies only the draft; Save and draft/saved native CSV retain exact half-open sample bounds')

            if not panel().count():
                open_panel()
            preview(EXPECTED)
            window_id = cdp.send('Browser.getWindowForTarget')['windowId']
            initial_dpr = page.evaluate('devicePixelRatio')
            layout_metrics = []
            for theme in ('light', 'dark'):
                switch = page.get_by_role('switch', name='Dark mode', exact=True)
                if (switch.get_attribute('aria-checked') == 'true') != (theme == 'dark'):
                    switch.click()
                for width, factor in [(1100, 1), (1440, 1.25), (1440, 1.5)]:
                    cdp.send('Browser.setWindowBounds', {'windowId': window_id,
                             'bounds': {'width': width, 'height': 1000}})
                    set_browser_zoom(worker, page, factor)
                    page.wait_for_function('expected=>Math.abs(devicePixelRatio-expected)<.001', arg=initial_dpr * factor)
                    value().scroll_into_view_if_needed()
                    expect(custom()).to_be_visible(); expect(value()).to_be_visible()
                    assert panel().evaluate('el=>{const r=el.getBoundingClientRect();return el.scrollWidth<=el.clientWidth+1&&r.left>=0&&r.right<=innerWidth+1}'), (theme, width, factor)
                    assert value().evaluate('el=>{const r=el.getBoundingClientRect();return r.width>=60&&r.left>=0&&r.right<=innerWidth+1}'), (theme, width, factor)
                    value().focus(); page.keyboard.press('Control+A'); page.keyboard.type('-2.5')
                    preview(EXPECTED)
                    value().scroll_into_view_if_needed()
                    capture_browser_view(cdp, output / f'exclusion-{theme}-{width}-{factor}.png')
                    layout_metrics.append({'theme': theme, 'width': width, 'zoom': factor,
                                           **page.evaluate('({dpr:devicePixelRatio, innerWidth, innerHeight})')})
            set_browser_zoom(worker, page, 1)
            assert hashes(dataset) == before_sources, 'Auto-split changed original source samples'
            assert not errors, errors
            passed('Light/dark compact desktop and actual 125%/150% browser zoom retain usable controls; all source samples unchanged')
            (output / 'results.json').write_text(json.dumps({
                'checks': checks, 'native_cases': native_cases, 'preview_requests': requests,
                'layout_metrics': layout_metrics, 'unchanged_source_files': len(before_sources),
                'unexpected_browser_errors': errors}, indent=2))
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            print('Browser errors:', errors, flush=True)
            print('Visible UI:', page.locator('body').inner_text()[-14000:], flush=True)
            raise
        finally:
            context.close(); request.dispose()


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3352)
    parser.add_argument('--backend-port', type=int, default=8352)
    args = parser.parse_args()
    output = ROOT / 'data/verification/autosplit-excluded-value'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-excluded-value-') as directory:
        temporary = Path(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, dataset):
            run_checks(web, api, dataset, temporary, output)
    print('PASS: temporary fixtures/profile/extension removed; owned servers stopped', flush=True)


if __name__ == '__main__':
    main()
