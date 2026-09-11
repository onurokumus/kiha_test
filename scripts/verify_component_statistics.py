"""Phase 10: real isolated RPM configuration, totals/lifecycle and desktop checks.

Global Python runs stdlib/Playwright only. Reuses repo server helpers for separate
process environments, Python 3.13 native reads, hidden children and exact cleanup.
"""
import argparse
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright
from verify_data_quality import ROOT, servers, upload, wait_until
from verify_test_notes import source_hashes
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout


def run_checks(web, api, dataset, temporary, output):
    extension = temporary / 'extension'; extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Component statistics zoom',
        'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as p:
        request = p.request.new_context(base_url=api + '/')
        context = p.chromium.launch_persistent_context(str(temporary / 'profile'), channel='chromium', headless=True,
            no_viewport=True, args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]; errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp = context.new_cdp_session(page)
        def nav(label):
            button = page.get_by_role('navigation', name='Main navigation').get_by_role('button', name=label, exact=True)
            button.focus(); page.keyboard.press('Enter')
        def ready():
            expect(page.get_by_role('main', name='Component statistics')).to_be_visible()
            expect(page.get_by_role('button', name='Refresh statistics', exact=True)).to_be_enabled()
            expect(page.get_by_text('Calculating component statistics…', exact=True)).to_have_count(0)
        def refresh():
            page.get_by_role('button', name='Refresh statistics', exact=True).click(); ready()
        def edit_test(name):
            page.get_by_role('button', name=f'Edit component settings for {name}', exact=True).click()
            expect(page.get_by_role('combobox', name='Component RPM column', exact=True)).to_be_visible()
            expect(page.get_by_role('combobox', name='Electric motor', exact=True)).to_be_enabled()
        def rpm(): return page.get_by_role('combobox', name='Component RPM column', exact=True)
        def save():
            button = page.get_by_role('button', name='Save notes and metadata', exact=True)
            button.focus(); page.keyboard.press('Enter'); expect(button).to_be_disabled()
        def total(id): return page.locator(f'tr[data-component-id="{id}"]')
        def api_summary(id):
            doc = request.get('component-statistics').json()
            return next(row for row in doc['components'] if row['id'] == id)['summary']

        try:
            page.goto(web); page.wait_for_load_state('networkidle')
            nav('Components'); ready()
            expect(page.get_by_text(re.compile('^No components yet'))).to_be_visible()
            expect(page.get_by_text('No active tests.', exact=True)).to_be_visible()
            ids = {}
            for kind in ('propeller', 'motor', 'esc'):
                ids[kind] = request.post('components', data={'kind': kind, 'name': kind + '_1'}).json()['id']
            motor2 = request.post('components', data={'kind': 'motor', 'name': 'motor_2'}).json()['id']
            values = [1000] * 40 + [0] * 10 + [''] * 10 + [3000] * 20
            alpha = 'time,rpm,alternate\n' + ''.join(f'{i/10},{v},5000\n' for i,v in enumerate(values))
            beta = 'time,rpm,alternate\n' + ''.join(f'{i/20},5000,2000\n' for i in range(80))
            for name, content in (('alpha', alpha), ('beta', beta), ('needs_rpm', alpha), ('stopped', beta.replace(',5000,', ',0,'))):
                assert upload(request, name, content, components=ids)['status'] == 'ready'
            for name in ('beta', 'stopped'):
                assert request.patch(f'tests/{name}/meta', data={'component_rpm_column': 'rpm', 'expected_component_rpm_revision': 0}).ok
            assert request.patch('tests/stopped/meta', data={'components': {**ids, 'motor': motor2}, 'expected_components_revision': 0}).ok
            request.get('analysis-sources')
            page.reload(); page.wait_for_load_state('networkidle'); nav('Components'); ready()
            before = source_hashes(dataset)
            expect(total(ids['motor'])).to_contain_text('1 / 3')
            expect(total(motor2)).to_contain_text('1 / 1')
            assert api_summary(motor2)['running_seconds'] == 0
            edit_test('alpha'); expect(rpm()).to_have_value('')
            rpm().select_option('rpm')
            nav('Components')
            expect(page.get_by_role('alertdialog')).to_be_visible(); page.keyboard.press('Escape')
            expect(rpm()).to_have_value('rpm')
            url = api + '/tests/alpha/meta'
            page.route(url, lambda route: route.fulfill(status=503, json={'detail': 'Injected RPM save failure'}))
            page.get_by_role('button', name='Save notes and metadata', exact=True).click()
            expect(page.get_by_role('status').filter(has_text='Injected RPM save failure')).to_be_visible()
            expect(rpm()).to_have_value('rpm')
            page.unroute(url); save(); nav('Components'); ready()
            summary = api_summary(ids['motor'])
            assert abs(summary['running_seconds'] - 10) < 1e-9, summary
            assert abs(summary['mean_rpm'] - 3000) < 1e-9, summary
            values_weights = [(1000,4), (3000,2), (5000,4)]
            expected_sd = math.sqrt(sum(w * (v - 3000)**2 for v,w in values_weights) / 10)
            assert abs(summary['sd_rpm'] - expected_sd) < 1e-9
            expect(total(ids['motor'])).to_contain_text('2 / 3')
            expect(total(ids['motor']).locator('td').nth(1)).to_have_text('0.167')
            expect(total(ids['motor']).locator('td').nth(2)).to_have_text('3,000')
            total(ids['motor']).get_by_role('button').click()
            expect(page.get_by_role('region', name='Operating ranges')).to_be_visible()
            expect(page.get_by_role('region', name='Test contributions')).to_contain_text('Missing RPM 0.017 min')
            expect(page.get_by_role('button', name='Edit component settings for stopped')).to_have_count(0)
            page.get_by_role('button', name='Show all tests', exact=True).click()
            print('PASS: empty library, explicit RPM, draft guard/503 retry and weighted native runtime/mean/SD/ranges/coverage', flush=True)

            edit_test('alpha'); rpm().select_option('alternate')
            remote = request.patch('tests/alpha/meta', data={'component_rpm_column': None, 'expected_component_rpm_revision': 1})
            assert remote.ok
            page.get_by_role('button', name='Save notes and metadata', exact=True).click()
            expect(page.get_by_role('status').filter(has_text='Component RPM selection changed')).to_be_visible()
            expect(rpm()).to_have_value('alternate')
            page.get_by_role('button', name='Reload saved metadata', exact=True).click()
            page.get_by_role('alertdialog').get_by_role('button', name='Reload metadata', exact=True).click()
            expect(rpm()).to_have_value('')
            rpm().select_option('rpm'); save()
            page.get_by_role('combobox', name='Electric motor', exact=True).select_option(motor2); save()
            nav('Components'); ready()
            assert abs(api_summary(ids['motor'])['running_seconds'] - 4) < 1e-9
            assert abs(api_summary(motor2)['running_seconds'] - 6) < 1e-9
            page.reload(); page.wait_for_load_state('networkidle'); nav('Components'); ready()
            edit_test('alpha'); expect(rpm()).to_have_value('rpm')
            expect(page.get_by_role('combobox', name='Electric motor', exact=True)).to_have_value(motor2)
            nav('Components'); ready()
            assert source_hashes(dataset) == before
            print(f'PASS: real RPM conflict/reload, assignment recalculation/reload and {len(before)} source files unchanged', flush=True)

            # A failed or unmounted request never leaves a previously successful
            # table masquerading as refreshed current data.
            stats_url = api + '/component-statistics'
            page.route(stats_url, lambda route: route.fulfill(status=503, json={'detail': 'Injected statistics failure'}))
            refresh()
            expect(page.get_by_role('alert')).to_contain_text('Injected statistics failure')
            expect(total(motor2)).to_have_count(0)
            page.unroute(stats_url); page.get_by_role('button', name='Retry statistics', exact=True).click(); ready()
            page.route(stats_url, lambda route: route.fulfill(json={'version': 99}))
            refresh(); expect(page.get_by_role('alert')).to_contain_text('Unsupported component statistics response')
            page.unroute(stats_url); page.get_by_role('button', name='Retry statistics', exact=True).click(); ready()
            held = []
            page.route(stats_url, lambda route: held.append(route))
            page.get_by_role('button', name='Refresh statistics', exact=True).click()
            expect(page.get_by_text('Calculating component statistics…')).to_be_visible()
            expect(total(motor2)).to_have_count(0)
            nav('Uploads')
            for route in held: route.abort()
            page.unroute(stats_url)
            nav('Components'); ready()
            expect(total(motor2)).to_contain_text('2 / 2')

            # Real lifecycle changes; source data moves byte-for-byte. Aggregate
            # cache must not retain old membership or double-count name conflicts.
            preserved = {str(path.relative_to(dataset / 'tests/alpha')): path.read_bytes()
                         for path in (dataset / 'tests/alpha').rglob('*') if path.is_file()}
            assert request.delete('tests/alpha').ok
            refresh(); assert api_summary(motor2)['running_seconds'] == 0
            entry = request.get('trash').json()['entries'][0]
            assert request.post('trash/' + entry['id'] + '/restore', data={'name': 'beta'}).status == 409
            assert request.post('trash/' + entry['id'] + '/restore', data={'name': 'alpha'}).ok
            refresh(); assert abs(api_summary(motor2)['running_seconds'] - 6) < 1e-9
            for relative, content in preserved.items():
                assert (dataset / 'tests/alpha' / relative).read_bytes() == content, relative
            assert request.delete('tests/alpha').ok
            entry = request.get('trash').json()['entries'][0]
            assert request.delete('trash/' + entry['id']).ok
            refresh(); assert api_summary(motor2)['running_seconds'] == 0
            print('PASS: statistics failure/retry/unmount, trash/restore/conflict/permanent deletion and restored bytes', flush=True)

            # Data changes through the actual rebuild endpoint, followed by a
            # data-derived reference rename and a drop requiring explicit repair.
            result = request.post('tests/beta/edit', data={'rename': {'rpm': 'shaft_speed'}, 'trim_t0': 1., 'trim_t1': 2.})
            assert result.ok, result.text()
            wait_until(lambda: request.get('tests/beta/status').json().get('status') == 'ready', 'trim/rename rebuild')
            refresh(); assert abs(api_summary(ids['motor'])['running_seconds'] - 1.05) < 1e-9
            expect(page.get_by_role('region', name='Test contributions')).to_contain_text('RPM: shaft_speed')
            assert request.post('tests/beta/edit', data={'drop': ['shaft_speed']}).ok
            wait_until(lambda: request.get('tests/beta/status').json().get('status') == 'ready', 'drop rebuild')
            refresh(); assert api_summary(ids['motor'])['running_seconds'] == 0
            expect(total(ids['motor'])).to_contain_text('0 / 2')
            # No test-independent selection guess after a deleted reference.
            assert request.get('tests/beta').json()['component_rpm_column'] is None
            assert request.patch('tests/beta/meta', data={'component_rpm_column': 'alternate', 'expected_component_rpm_revision': 3}).ok
            refresh(); assert abs(api_summary(ids['motor'])['running_seconds'] - 1.05) < 1e-9
            # Acquisition rows remain excluded after interpolation. A legacy
            # edited file that lost this history must be visibly unavailable.
            gap_id = request.post('components', data={'kind': 'esc', 'name': 'gap_observer'}).json()['id']
            gap_csv = 'time,rpm\n0,1000\n0.5,1000\n1,1000\n3,1000\n3.5,1000\n4,1000\n'
            assert upload(request, 'observed_gap', gap_csv, components={'esc': gap_id})['status'] == 'ready'
            assert request.patch('tests/observed_gap/meta', data={'component_rpm_column': 'rpm', 'expected_component_rpm_revision': 0}).ok
            assert request.post('tests/observed_gap/edit', data={'nan_policy': 'interpolate'}).ok
            wait_until(lambda: request.get('tests/observed_gap/status').json().get('status') == 'ready', 'gap fill')
            refresh(); assert api_summary(gap_id)['running_seconds'] == 3.
            gap_row = page.get_by_role('region', name='Source contributions').get_by_role('row').filter(has_text='observed_gap')
            expect(gap_row).to_contain_text('Gaps 0.025 min')
            saved = request.get('tests/observed_gap').json()
            legacy = dict(saved); legacy.pop('acquisition_gap_ranges')
            path = dataset / 'tests/observed_gap/meta.json'
            path.write_text(json.dumps(legacy), encoding='utf-8')
            refresh(); expect(gap_row).to_contain_text('history is unavailable')
            assert api_summary(gap_id)['running_seconds'] == 0
            path.write_text(json.dumps(saved), encoding='utf-8')
            refresh(); assert api_summary(gap_id)['running_seconds'] == 3.
            remaining = source_hashes(dataset)

            window_id = cdp.send('Browser.getWindowForTarget')['windowId']
            for width, factor in ((1440,1), (1100,1), (1440,1.25), (1440,1.5), (1100,1.5)):
                cdp.send('Browser.setWindowBounds', {'windowId': window_id, 'bounds': {'width': width, 'height': 1000}})
                set_browser_zoom(worker, page, factor)
                for label in ('Import CSV', 'Sessions', 'Components'):
                    button = page.get_by_role('button', name=label, exact=True)
                    assert button.evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0 && r.right<=innerWidth && r.top>=0;}'), (width, factor, label)
                page.get_by_role('searchbox', name='Find component', exact=True).fill('motor')
                page.get_by_role('combobox', name='Component type', exact=True).select_option('motor')
                expect(page.locator('tr[data-component-id]')).to_have_count(2)
                button = total(ids['motor']).get_by_role('button'); button.focus(); page.keyboard.press('Enter')
                expect(page.get_by_role('region', name='Operating ranges')).to_be_visible()
                for region in ('Component totals', 'Source contributions'):
                    table = page.get_by_role('region', name=region, exact=True)
                    table.scroll_into_view_if_needed(); table.focus(); page.keyboard.press('ArrowRight')
                    assert table.evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0 && r.right<=innerWidth+1 && r.width>200;}')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, factor)
                page.get_by_role('main', name='Component statistics').evaluate('el=>el.scrollTop=0')
                capture_browser_view(cdp, output / f'components-{width}-{factor}.png')
                page.get_by_role('button', name='Show all tests', exact=True).click()
                page.get_by_role('searchbox', name='Find component', exact=True).fill('')
                page.get_by_role('combobox', name='Component type', exact=True).select_option('')
            assert source_hashes(dataset) == remaining
            assert not errors, errors
            (output / 'results.json').write_text(json.dumps({'baseline_source_files': len(before),
                'remaining_unchanged_files': len(remaining), 'mean_rpm_oracle': 3000, 'sd_rpm_oracle': expected_sd,
                'native_lifecycle_and_edit': True, 'page_errors': errors, 'ids': ids}, indent=2), encoding='utf-8')
            print('PASS: real data trim/rename/drop/repair, keyboard/search/type filters, 1100px/125%/150% and unchanged remaining sources', flush=True)
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            print(page.locator('body').aria_snapshot()[-12000:], flush=True)
            raise
        finally:
            context.close(); request.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3290)
    parser.add_argument('--backend-port', type=int, default=8290)
    parser.add_argument('--regressions', action='store_true', help='Also run the existing rendering and Time Y suites on the owned servers')
    args = parser.parse_args()
    output = ROOT / 'data/verification/component-statistics'; output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-component-stats-') as directory:
        temporary = Path(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web,api,dataset):
            run_checks(web,api,dataset,temporary,output)
            if args.regressions:
                for script in ('verify_rendering.py', 'verify_time_y_zoom.py'):
                    subprocess.run([sys.executable, '-X', 'utf8', '-u', str(ROOT / 'scripts' / script), '--url', web], cwd=ROOT, check=True)
    print('PASS: owned servers stopped; temporary data/profile removed', flush=True)


if __name__ == '__main__': main()
