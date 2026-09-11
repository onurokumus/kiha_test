"""Native Chromium checks for reviewed multi-variable auto-split proposals.

Owns temporary uploads, browser profile, zoom extension and isolated Vite/Python
3.13 backend servers. No user data or settings are touched. Global Python runs
only stdlib and Playwright; evidence is in data/verification/multi-variable-autosplit.
"""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile

from playwright.sync_api import Error, expect, sync_playwright

from verify_browser_zoom import capture_browser_view, set_browser_zoom
from verify_data_quality import ROOT, servers, upload


ALPHA = 'auto_split_alpha'
BETA = 'auto_split_beta'
EXPECTED = [(5, 15), (15, 25), (32, 42), (47, 57), (62, 72)]


def fixtures(request):
    runs = [(0, 5, 0, 1), (5, 15, 1, 1), (15, 25, 1, 2),
            (25, 28, 2, 2), (28, 32, 2, ''), (32, 42, 2, 2),
            (42, 47, 2, 0), (47, 57, 3, 3), (57, 62, 3, 4), (62, 72, 3, 3)]
    content = 'time,run_id,state_id,load_N\n' + '\n'.join(
        f'{index / 10},{run},{state},{12.125 + index * .013}'
        for start, end, run, state in runs for index in range(start, end))
    assert upload(request, ALPHA, content)['status'] == 'ready'
    beta = 'time,beta_mode,beta_step\n' + '\n'.join(
        f'{index / 10},{1 + index // 20},{4 + index // 10}' for index in range(40))
    assert upload(request, BETA, beta)['status'] == 'ready'
    for test, count in [(ALPHA, 72), (BETA, 40)]:
        response = request.put(f'tests/{test}/testpoints', data={
            'version': 1, 'test': test, 'test_points': [{
                'id': 99, 'name': 'Existing saved interval', 'label': 'Original saved definition',
                'start_s': 0, 'end_s': count / 10, 'start_idx': 0,
                'end_idx': count, 'notes': 'Must survive proposal review'}]})
        assert response.ok, response.text()


def hashes(dataset):
    return {str(path.relative_to(dataset)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in dataset.rglob('*') if path.is_file()
            and (path.suffix in {'.parquet', '.csv'} or 'pyramid' in path.parts)}


def run_checks(web, api, dataset, temporary, output):
    extension = temporary / 'extension'
    extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({
        'manifest_version': 3, 'name': 'Auto-split browser zoom verification', 'version': '1.0',
        'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as playwright:
        request = playwright.request.new_context(base_url=api + '/')
        fixtures(request)
        before_sources = hashes(dataset)
        saved_path = dataset / 'tests' / ALPHA / 'testpoints.json'
        saved_bytes = saved_path.read_bytes()
        saved_json = request.get(f'tests/{ALPHA}/testpoints').json()
        context = playwright.chromium.launch_persistent_context(
            str(temporary / 'profile'), channel='chromium', headless=True,
            no_viewport=True, accept_downloads=True,
            args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]
        page.set_default_timeout(10000)
        errors, requests, checks = [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
        page.on('request', lambda req: requests.append({'url': req.url, 'body': req.post_data_json})
                if req.method == 'POST' and req.url.endswith('/split/preview') else None)
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp = context.new_cdp_session(page)

        def panel():
            return page.get_by_role('region', name='Auto-split', exact=True)

        def open_panel():
            button = page.get_by_role('button', name='Configure auto-split', exact=True)
            button.focus(); page.keyboard.press('Enter')
            expect(panel()).to_be_visible()

        def choose(index, value):
            picker = panel().get_by_role('button', name=f'Auto-split variable {index}', exact=True)
            picker.focus(); page.keyboard.press('Enter')
            page.get_by_role('combobox').last.fill(value)
            option = page.get_by_role('option', name=re.compile('^' + re.escape(value) + r'(?:\s|$)'))
            expect(option).to_be_visible()
            page.keyboard.press('ArrowDown'); page.keyboard.press('Enter')
            expect(picker).to_contain_text(value)
            expect(picker).to_be_focused()

        def preview(count):
            button = panel().get_by_role('button', name='Preview test points', exact=True)
            button.focus(); page.keyboard.press('Enter')
            apply = panel().get_by_role('button', name=f'Use {count} test points', exact=True)
            expect(apply).to_be_visible()
            expect(apply).to_be_enabled()
            return apply

        def configure_pair():
            choose(1, 'run_id')
            if panel().get_by_role('button', name='Auto-split variable 2', exact=True).count() == 0:
                panel().get_by_role('button', name=re.compile(r'^\+? ?Add variable$')).focus()
                page.keyboard.press('Enter')
            choose(2, 'state_id')
            panel().get_by_role('spinbutton', name='Minimum duration (s)', exact=True).fill('1')
            panel().get_by_role('checkbox', name=re.compile('Exclude.*zero')).check()

        def same_saved():
            assert saved_path.read_bytes() == saved_bytes, 'Proposal review or Apply persisted testpoints.json'
            assert request.get(f'tests/{ALPHA}/testpoints').json() == saved_json

        def close_panel():
            panel().get_by_role('button', name='Close auto-split', exact=True).focus()
            page.keyboard.press('Enter')
            expect(panel()).to_have_count(0)
            expect(page.get_by_role('button', name='Configure auto-split', exact=True)).to_be_focused()

        def choose_test(test):
            page.get_by_role('button', name='Active test', exact=True).click()
            page.get_by_role('combobox').last.fill(test)
            page.get_by_role('option', name=re.compile('^' + re.escape(test))).click()
            page.wait_for_load_state('networkidle')

        try:
            # The native endpoint must report exact full-resolution tuple runs,
            # irrespective of the display plot's reduced samples.
            response = request.post(f'tests/{ALPHA}/split/preview', data={
                'columns': ['run_id', 'state_id'], 'ignore_zero': True, 'min_len_s': 1})
            assert response.ok, response.text()
            proposal = response.json()
            assert [(point['start_idx'], point['end_idx']) for point in proposal['test_points']] == EXPECTED, proposal
            assert proposal['excluded']['missing_samples'] == 4, proposal
            assert proposal['excluded']['zero_samples'] == 10, proposal
            assert proposal['excluded']['short_runs'] == 2, proposal
            same_saved()
            page.goto(web); page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='Uploads', exact=True).click()
            page.get_by_role('searchbox', name='Search upload history').fill(ALPHA)
            page.get_by_role('button', name=f'Analyze {ALPHA}', exact=True).click()
            page.get_by_role('button', name='Split', exact=True).click()
            page.get_by_role('textbox', name='Name for TP 99', exact=True).wait_for()
            open_panel(); configure_pair()
            expect(panel()).to_contain_text(re.compile('any selected variable', re.I))
            apply = preview(5)
            same_saved()
            expect(page.get_by_role('textbox', name='Name for TP 99', exact=True)).to_have_value('Existing saved interval')
            expect(page.get_by_role('button', name='save', exact=True)).to_be_disabled()
            preview_table = panel().get_by_role('table')
            expect(preview_table.get_by_role('row')).to_have_count(6)
            expect(preview_table).to_contain_text('run_id=1')
            expect(preview_table).to_contain_text('state_id=2')
            checks.append('Native two-variable tuple bounds/zero/missing/minimum counts and keyboard preview are read-only')
            print('PASS:', checks[-1], flush=True)

            # Editing the actual TP draft after preview must invalidate it.
            name_input = page.get_by_role('textbox', name='Name for TP 99', exact=True)
            name_input.fill('Existing unsaved name')
            expect(panel().get_by_role('button', name=re.compile('^Use '))).to_be_disabled()
            same_saved()
            apply = preview(5)
            expect(panel()).to_contain_text(re.compile('replace', re.I))
            apply.focus(); page.keyboard.press('Enter')
            expect(page.get_by_role('textbox', name='Name for TP 99', exact=True)).to_have_count(0)
            for index, (start, end) in enumerate(EXPECTED, 1):
                assert abs(float(page.get_by_role('spinbutton', name=f'Start seconds for TP {index}', exact=True).input_value()) - start / 10) < 1e-9
                assert abs(float(page.get_by_role('spinbutton', name=f'End seconds for TP {index}', exact=True).input_value()) - end / 10) < 1e-9
            same_saved()
            expect(page.get_by_role('button', name='save *', exact=True)).to_be_enabled()
            with page.expect_download() as event:
                page.get_by_role('link', name='Download draft CSV for TP 1', exact=True).click()
            draft_download = event.value
            assert draft_download.suggested_filename == f'{ALPHA}_tp1_draft.csv'
            draft_download.save_as(output / 'draft-tp1.csv')
            rows = list(csv.DictReader(io.StringIO((output / 'draft-tp1.csv').read_text())))
            assert [float(row['time']) for row in rows] == [index / 10 for index in range(5, 15)]
            assert {row['test_point_id'] for row in rows} == {'1'}
            same_saved()
            page.get_by_role('button', name='save *', exact=True).click()
            expect(page.get_by_role('button', name='save', exact=True)).to_be_disabled()
            actual_saved = request.get(f'tests/{ALPHA}/testpoints').json()
            assert [(point['start_idx'], point['end_idx']) for point in actual_saved['test_points']] == EXPECTED
            assert saved_path.read_bytes() != saved_bytes
            with page.expect_download() as event:
                page.get_by_role('link', name='Download CSV for TP 1', exact=True).click()
            event.value.save_as(output / 'saved-tp1.csv')
            assert (output / 'saved-tp1.csv').read_bytes() == (output / 'draft-tp1.csv').read_bytes()
            checks.append('TP draft edit invalidates preview; Apply changes draft only, then Save and native draft/saved CSV retain exact half-open bounds')
            print('PASS:', checks[-1], flush=True)

            if panel().count() == 0:
                open_panel()
            configure_pair(); preview(5)
            minimum = panel().get_by_role('spinbutton', name='Minimum duration (s)', exact=True)
            minimum.fill('0.2')
            expect(panel().get_by_role('button', name=re.compile('^Use '))).to_be_disabled()
            preview(7)
            panel().get_by_role('checkbox', name=re.compile('Exclude.*zero')).uncheck()
            preview(9)
            minimum.fill('100')
            panel().get_by_role('button', name='Preview test points', exact=True).click()
            expect(panel()).to_contain_text(re.compile('No test points', re.I))
            expect(panel().get_by_role('button', name=re.compile('^Use '))).to_be_disabled()
            minimum.fill('1')
            panel().get_by_role('checkbox', name=re.compile('Exclude.*zero')).check()
            panel().get_by_role('button', name='Remove auto-split variable 2', exact=True).focus()
            page.keyboard.press('Enter')
            expect(panel().get_by_role('button', name='Auto-split variable 1', exact=True)).to_be_focused()
            preview(3)
            configure_pair()
            checks.append('Rule changes clear proposals; zero inclusion and minimum duration change exact run counts; keyboard removal preserves single-variable behavior')
            print('PASS:', checks[-1], flush=True)

            preview_url = api + f'/tests/{ALPHA}/split/preview'
            page.route(preview_url, lambda route: route.fulfill(status=503, json={'detail': 'Isolated auto-split preview failure'}))
            panel().get_by_role('button', name='Preview test points', exact=True).click()
            expect(panel().get_by_role('alert')).to_contain_text('Isolated auto-split preview failure')
            expect(panel().get_by_role('button', name=re.compile('^Use '))).to_be_disabled()
            assert request.get(f'tests/{ALPHA}/testpoints').json() == actual_saved
            page.unroute(preview_url)
            preview(5)

            # Hold a real response until the rule changes, then release it. The
            # canceled old response cannot restore a proposal or edit the draft.
            held = []
            def hold_preview(route):
                response = route.fetch()
                held.append((route, response))
            page.route(preview_url, hold_preview)
            minimum.fill('0.2')
            panel().get_by_role('button', name='Preview test points', exact=True).click()
            expect(panel()).to_contain_text(re.compile('preview', re.I))
            page.wait_for_timeout(100)
            assert held, 'Preview request was not held'
            minimum.fill('1')
            for route, response in held:
                try:
                    route.fulfill(response=response)
                except Error as error:
                    assert re.search('cancel|abort|closed', str(error), re.I), str(error)
            page.unroute(preview_url)
            page.wait_for_load_state('networkidle')
            expect(panel().get_by_role('button', name=re.compile('^Use '))).to_be_disabled()
            assert request.get(f'tests/{ALPHA}/testpoints').json() == actual_saved

            held.clear(); page.route(preview_url, hold_preview)
            panel().get_by_role('button', name='Preview test points', exact=True).click()
            page.wait_for_timeout(100); assert held
            close_panel()
            for route, response in held:
                try:
                    route.fulfill(response=response)
                except Error as error:
                    assert re.search('cancel|abort|closed', str(error), re.I), str(error)
            page.unroute(preview_url)
            open_panel()
            expect(panel().get_by_role('button', name=re.compile('^Use '))).to_be_disabled()
            assert request.get(f'tests/{ALPHA}/testpoints').json() == actual_saved
            checks.append('Preview error is recoverable; deliberately held responses after rule change or Close cannot restore/apply stale proposals')
            print('PASS:', checks[-1], flush=True)

            close_panel()
            candidates_url = api + f'/tests/{BETA}/split/candidates'
            page.route(candidates_url, lambda route: route.fulfill(status=503, json={'detail': 'Isolated candidate suggestions failure'}))
            choose_test(BETA); open_panel()
            expect(panel()).to_contain_text('Recommendations could not load')
            choose(1, 'beta_mode')
            panel().get_by_role('button', name=re.compile(r'^\+? ?Add variable$')).click()
            choose(2, 'beta_step')
            preview(4)
            assert all('run_id' not in item['body']['columns'] and 'state_id' not in item['body']['columns']
                       for item in requests if f'/tests/{BETA}/' in item['url'])
            page.unroute(candidates_url)
            panel().get_by_role('button', name='Retry recommendations', exact=True).click()
            expect(panel().get_by_text('Recommendations could not load', exact=False)).to_have_count(0)
            checks.append('Failed candidate suggestions keep manual numeric variables usable; Retry and disjoint test context recover without stale columns')
            print('PASS:', checks[-1], flush=True)

            close_panel(); choose_test(ALPHA); open_panel(); configure_pair(); preview(5)
            window_id = cdp.send('Browser.getWindowForTarget')['windowId']
            initial_dpr = page.evaluate('devicePixelRatio')
            for width, factor in [(1100, 1), (1440, 1.25), (1440, 1.5)]:
                cdp.send('Browser.setWindowBounds', {'windowId': window_id, 'bounds': {'width': width, 'height': 1000}})
                set_browser_zoom(worker, page, factor)
                page.wait_for_function('expected=>Math.abs(devicePixelRatio-expected)<.001', arg=initial_dpr * factor)
                panel().scroll_into_view_if_needed()
                assert panel().evaluate('el=>{const r=el.getBoundingClientRect();return el.scrollWidth<=el.clientWidth+1&&r.left>=0&&r.right<=innerWidth+1}'), (width, factor)
                choose(2, 'state_id')
                if panel().get_by_role('button', name='Use 5 test points', exact=True).count() == 0:
                    preview(5)
                apply = panel().get_by_role('button', name='Use 5 test points', exact=True)
                apply.scroll_into_view_if_needed(); expect(apply).to_be_visible()
                capture_browser_view(cdp, output / f'preview-{width}-{factor}.png')
            set_browser_zoom(worker, page, 1)
            close_panel()
            assert hashes(dataset) == before_sources, 'Auto-split modified original CSV/Parquet/pyramid data'
            unexpected = [error for error in errors if '503' not in error and 'Isolated' not in error]
            assert not unexpected, unexpected
            checks.append('1100px resize, actual125%/150% browser zoom and keyboard controls remain usable; all source samples unchanged')
            print('PASS:', checks[-1], flush=True)
            (output / 'results.json').write_text(json.dumps({
                'checks': checks, 'native_proposal': proposal, 'preview_requests': requests,
                'unchanged_source_files': len(before_sources), 'unexpected_browser_errors': unexpected}, indent=2))
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            print('Browser errors:', errors, flush=True)
            print('Visible UI:', page.locator('body').inner_text()[-16000:], flush=True)
            raise
        finally:
            context.close(); request.dispose()


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3340)
    parser.add_argument('--backend-port', type=int, default=8340)
    args = parser.parse_args()
    output = ROOT / 'data/verification/multi-variable-autosplit'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-multi-autosplit-') as directory:
        temporary = Path(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, dataset):
            run_checks(web, api, dataset, temporary, output)
    print('PASS: temporary fixtures/profile/extension removed; owned servers stopped', flush=True)


if __name__ == '__main__':
    main()
