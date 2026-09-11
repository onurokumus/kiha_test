"""Isolated native browser verification of component sets and operating telemetry.

Global Python runs only stdlib/Playwright. The reused server helper starts the
required Python 3.13 backend with a temporary dataset and hidden child processes.
"""
import argparse
import json
from pathlib import Path
import re
import tempfile

from playwright.sync_api import expect, sync_playwright
from verify_browser_zoom import capture_browser_view, set_browser_zoom
from verify_data_quality import ROOT, servers, upload, wait_until
from verify_test_notes import source_hashes


def fixture_csv():
    rows = ['time,rpm_a,rpm_b,temp_f,temp_c,power_kw,power_w']
    for i in range(80):
        values = [i / 10, 1200 if i < 40 else 0, 0 if i < 20 else 2400,
                  68 if i < 20 else 104, '' if 50 <= i < 60 else 50 if i < 60 else 70,
                  .1 if i < 20 else .3, '' if 40 <= i < 60 else 400 if i < 60 else 800]
        rows.append(','.join(map(str, values)))
    return '\n'.join(rows) + '\n'


def run_checks(web, api, dataset, temporary, output):
    extension = temporary / 'extension'; extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3,
        'name': 'Component set desktop verification', 'version': '1.0', 'permissions': ['tabs'],
        'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as p:
        request = p.request.new_context(base_url=api + '/')
        csv = fixture_csv()
        hardware = []
        for suffix in ('left', 'right'):
            hardware.append({kind: request.post('components', data={'kind': kind,
                'name': f'{kind}_{suffix}'}).json()['id'] for kind in ('propeller', 'motor', 'esc')})
        assert upload(request, 'legacy', csv, components=hardware[0])['status'] == 'ready'
        assert request.patch('tests/legacy/meta', data={'component_rpm_column': 'rpm_a',
            'expected_component_rpm_revision': 0}).ok
        request.get('analysis-sources')
        context = p.chromium.launch_persistent_context(str(temporary / 'profile'), channel='chromium',
            headless=True, no_viewport=True, args=[f'--disable-extensions-except={extension}',
                f'--load-extension={extension}', '--window-size=1440,1000'])
        # Localhost is secure-context exempt; simulate the deployed plain-HTTP
        # feature set to prevent an accidental dependency on randomUUID.
        context.add_init_script("Object.defineProperty(Crypto.prototype, 'randomUUID', {value: undefined});")
        page = context.pages[0]; errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text)
                if re.search('same key|unique.*key.*prop', message.text, re.I) else None)
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp = context.new_cdp_session(page)
        def nav(label):
            control = page.get_by_role('navigation', name='Main navigation').get_by_role('button', name=label, exact=True)
            control.focus(); page.keyboard.press('Enter')
        def group(index): return page.get_by_role('group', name=f'Component set {index}', exact=True)
        def field(index, label):
            return group(index).get_by_role('textbox' if label == 'Set name' else 'combobox', name=label, exact=True)
        def edit(name):
            nav('Uploads')
            page.get_by_role('searchbox', name='Search upload history').fill('')
            page.get_by_role('button', name=f'Edit components for {name}', exact=True).click()
            expect(group(1)).to_be_visible()
        def save():
            button = page.get_by_role('button', name='Save notes and metadata', exact=True)
            button.focus(); page.keyboard.press('Enter'); expect(button).to_be_disabled()
        def metadata(name='dual'): return request.get(f'tests/{name}').json()
        def statistics():
            doc = request.get('component-statistics').json()
            assert doc['method'] == 'component-usage-v2', doc
            return doc
        def ready():
            expect(page.get_by_role('main', name='Component statistics')).to_be_visible()
            expect(page.get_by_role('button', name='Refresh statistics', exact=True)).to_be_enabled()
            expect(page.get_by_text('Calculating component statistics…', exact=True)).to_have_count(0)
        def refresh():
            page.get_by_role('button', name='Refresh statistics', exact=True).click(); ready()
        def source_rows(name='dual'):
            return [row for row in statistics()['sources'] if row['name'] == name]
        def assert_values():
            rows = {row['set_name']: row for row in source_rows()}
            assert set(rows) == {'Left drive', 'Right drive'}, rows
            for label, seconds, rpm, temperature, power, tsamples, psamples in [
                ('Left drive', 4, 1200, 30, 200, 40, 40),
                ('Right drive', 6, 2400, 58, 600, 50, 40)]:
                row = rows[label]; assert row['issue'] is None, row
                summary = row['summary']
                for key, expected in [('running_seconds', seconds), ('mean_rpm', rpm)]:
                    assert abs(summary[key] - expected) < 1e-8, (label, key, summary)
                for key, expected, samples, unit in [('motor_temperature', temperature, tsamples, 'C'),
                                                     ('power', power, psamples, 'W')]:
                    metric = summary[key]
                    assert abs(metric['mean'] - expected) < 1e-8 and metric['samples'] == samples, metric
                    assert metric['unit'] == unit and abs(metric['seconds'] - samples / 10) < 1e-8, metric
            return rows
        def stage_and_resume(name, server_only=False):
            path = temporary / f'{name}.csv'; path.write_text(csv, encoding='utf-8')
            nav('Uploads')
            with page.expect_file_chooser() as chooser:
                page.get_by_role('button', name=re.compile('Import test data')).click()
            chooser.value.set_files(str(path))
            page.get_by_label('Uploaded by', exact=True).fill('Two-shaft verification')
            for index, label in ((1, 'Left drive'), (2, 'Right drive')):
                page.get_by_role('button', name='Add component set', exact=True).click()
                field(index, 'Set name').fill(label)
                for kind, title in [('propeller', 'Propeller'), ('motor', 'Electric motor'), ('esc', 'ESC')]:
                    field(index, title).select_option(hardware[index - 1][kind])
            held = []
            pattern = api + '/uploads/*/complete?*'
            page.route(pattern, lambda route: held.append(route))
            page.get_by_role('button', name='Upload 1 file', exact=True).click()
            page.wait_for_function("document.body.textContent.includes('finalizing')")
            records = page.evaluate("JSON.parse(localStorage.getItem('ptt.uploadSessions.v1'))")
            record = next(record for record in records if record['testName'] == name)
            assert [item['name'] for item in record['component_sets']] == ['Left drive', 'Right drive'], record
            page.get_by_role('button', name='Pause', exact=True).last.click()
            for route in held: route.abort()
            page.unroute(pattern)
            if server_only: page.evaluate("localStorage.removeItem('ptt.uploadSessions.v1')")
            page.reload(); page.wait_for_load_state('networkidle'); nav('Uploads')
            with page.expect_file_chooser() as chooser:
                page.get_by_role('button', name=f'Select original CSV to resume {name}', exact=True).click()
            chooser.value.set_files(str(path))
            expect(page.get_by_role('button', name=f'Edit components for {name}', exact=True)).to_be_visible(timeout=20000)
            saved = metadata(name)['component_sets']
            assert [item['name'] for item in saved] == ['Left drive', 'Right drive'], saved
            assert len({item['id'] for item in saved}) == 2
            assert all(re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', item['id']) for item in saved)
            assert [item['components'] for item in saved] == hardware
            assert all(item['rpm_column'] is None for item in saved)

        try:
            page.goto(web); page.wait_for_load_state('networkidle')
            edit('legacy')
            expect(field(1, 'Electric motor')).to_have_value(hardware[0]['motor'])
            expect(field(1, 'RPM column')).to_have_value('rpm_a')
            assert 'component_sets' not in metadata('legacy'), 'Opening legacy metadata must not write it'
            stage_and_resume('dual')
            stage_and_resume('server_dual', server_only=True)
            print('PASS: legacy single-set display, two-set upload, local and server-only Pause/reload/resume', flush=True)
            before = source_hashes(dataset)
            edit('dual')
            pending_create = []
            component_url = api + '/components'
            page.route(component_url, lambda route: pending_create.append(route)
                       if route.request.method == 'POST' else route.continue_())
            field(1, 'Electric motor').select_option('__new')
            new_motor = group(1).get_by_label('New electric motor name', exact=True)
            new_motor.fill('motor_created_during_edit')
            group(1).get_by_role('button', name='Add component', exact=True).click()
            expect(new_motor).to_be_disabled()
            field(1, 'Set name').fill('Changed during create')
            field(2, 'Power unit').select_option('kW')
            wait_until(lambda: pending_create, 'held component create')
            pending_create[0].continue_()
            expect(new_motor).not_to_be_visible()
            expect(field(1, 'Set name')).to_have_value('Changed during create')
            expect(field(2, 'Power unit')).to_have_value('kW')
            page.unroute(component_url)
            field(1, 'Set name').fill('Left drive')
            field(1, 'Electric motor').select_option(hardware[0]['motor'])
            for index, rpm, temperature, unit, power, punit in [
                (1, 'rpm_a', 'temp_f', 'F', 'power_kw', 'kW'),
                (2, 'rpm_b', 'temp_c', 'C', 'power_w', 'W')]:
                field(index, 'RPM column').select_option(rpm)
                field(index, 'Motor temperature column').select_option(temperature)
                field(index, 'Motor temperature unit').select_option(unit)
                field(index, 'Power column').select_option(power)
                field(index, 'Power unit').select_option(punit)
            nav('Components'); expect(page.get_by_role('alertdialog')).to_be_visible(); page.keyboard.press('Escape')
            expect(field(2, 'RPM column')).to_have_value('rpm_b')
            url = api + '/tests/dual/meta'
            page.route(url, lambda route: route.fulfill(status=503, json={'detail': 'Injected set save failure'}))
            page.get_by_role('button', name='Save notes and metadata', exact=True).click()
            expect(page.get_by_role('status').filter(has_text='Injected set save failure')).to_be_visible()
            expect(field(1, 'Motor temperature unit')).to_have_value('F')
            page.unroute(url); save(); assert_values()
            page.reload(); page.wait_for_load_state('networkidle'); edit('dual')
            expect(field(2, 'Power column')).to_have_value('power_w')
            expect(field(1, 'Power unit')).to_have_value('kW')
            nav('Components'); ready()
            expect(page.get_by_role('region', name='Source contributions')).to_contain_text('Right drive')
            expect(page.get_by_role('region', name='Component totals')).to_contain_text('Motor temperature')
            print('PASS: independent RPM runtime/temperature/power, F/kW conversion, missing coverage, draft guards and failure retry', flush=True)

            edit('dual'); field(1, 'Set name').fill('Unsaved left')
            current = metadata(); remote = json.loads(json.dumps(current['component_sets']))
            remote[1]['name'] = 'Remote right'
            assert request.patch('tests/dual/meta', data={'component_sets': remote,
                'expected_component_sets_revision': current['component_sets_revision']}).ok
            page.get_by_role('button', name='Save notes and metadata', exact=True).click()
            expect(page.get_by_role('status').filter(has_text=re.compile('changed', re.I))).to_be_visible()
            expect(field(1, 'Set name')).to_have_value('Unsaved left')
            page.get_by_role('button', name='Reload saved metadata', exact=True).click()
            page.get_by_role('alertdialog').get_by_role('button', name='Reload metadata', exact=True).click()
            expect(field(2, 'Set name')).to_have_value('Remote right')
            field(2, 'Set name').fill('Right drive'); save()
            field(2, 'Electric motor').select_option(hardware[0]['motor'])
            expect(page.get_by_role('button', name='Save notes and metadata', exact=True)).to_be_disabled()
            field(2, 'Electric motor').select_option(hardware[1]['motor'])
            group(2).get_by_role('button', name='Remove Right drive', exact=True).click()
            expect(group(2)).to_have_count(0)
            save(); assert len(source_rows()) == 1
            current = metadata()
            assert request.patch('tests/dual/meta', data={'component_sets': remote[:1] + [{**remote[1], 'name': 'Right drive'}],
                'expected_component_sets_revision': current['component_sets_revision']}).ok
            assert_values()
            assert source_hashes(dataset) == before
            print('PASS: atomic stale-revision conflict/reload, duplicate hardware prevention, set removal and recalculation', flush=True)

            nav('Components'); ready()
            stats_url = api + '/component-statistics'
            page.route(stats_url, lambda route: route.fulfill(status=503, json={'detail': 'Injected statistics failure'}))
            refresh(); expect(page.get_by_role('alert')).to_contain_text('Injected statistics failure')
            expect(page.locator('tr[data-component-id]')).to_have_count(0)
            page.unroute(stats_url); page.get_by_role('button', name='Retry statistics', exact=True).click(); ready()
            saved_sets = metadata()['component_sets']
            assert request.delete('tests/dual').ok
            assert not source_rows()
            entry = next(entry for entry in request.get('trash').json()['entries'] if entry['name'] == 'dual')
            assert entry['component_sets'] == saved_sets
            assert request.post('trash/' + entry['id'] + '/restore', data={'name': 'dual'}).ok
            assert metadata()['component_sets'] == saved_sets
            assert_values(); assert source_hashes(dataset) == before
            print('PASS: statistics failure/retry, trash removes both sets, restore preserves mappings and source bytes', flush=True)
            # External API edits deliberately do not live-replace the open
            # editor cache; reload, as a user would after another client writes.
            page.reload(); page.wait_for_load_state('networkidle')

            window_id = cdp.send('Browser.getWindowForTarget')['windowId']
            for width, zoom in ((1440, 1), (1100, 1), (1440, 1.25), (1440, 1.5), (1100, 1.5)):
                cdp.send('Browser.setWindowBounds', {'windowId': window_id, 'bounds': {'width': width, 'height': 1000}})
                set_browser_zoom(worker, page, zoom)
                edit('dual')
                for index in (1, 2):
                    control = field(index, 'Motor temperature column')
                    control.scroll_into_view_if_needed(); control.focus(); expect(control).to_be_focused()
                    assert group(index).evaluate('el=>el.scrollWidth<=el.clientWidth+2'), (width, zoom)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, zoom)
                capture_browser_view(cdp, output / f'edit-{width}-{zoom}.png')
                nav('Components'); ready()
                for label in ('Component totals', 'Source contributions'):
                    region = page.get_by_role('region', name=label, exact=True)
                    region.scroll_into_view_if_needed(); region.focus(); page.keyboard.press('ArrowRight')
                    assert region.evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0 && r.right<=innerWidth+1;}')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, zoom)
                page.get_by_role('main', name='Component statistics').evaluate('el=>el.scrollTop=0')
                capture_browser_view(cdp, output / f'statistics-{width}-{zoom}.png')
            set_browser_zoom(worker, page, 1)
            malformed_path = dataset / 'tests/server_dual/meta.json'
            original_meta = malformed_path.read_bytes()
            base_meta = json.loads(original_meta)
            for invalid in (None, [None], [{'id': 'legacy', 'components': {}}]):
                malformed_path.write_text(json.dumps({**base_meta, 'component_sets': invalid}), encoding='utf-8')
                page.reload(); page.wait_for_load_state('networkidle'); nav('Uploads')
                button = page.get_by_role('button', name='Edit components for server_dual', exact=True)
                expect(button).to_contain_text('Invalid component settings')
                button.click()
                expect(page.get_by_role('alert').filter(has_text='Invalid component settings')).to_be_visible()
                expect(group(1)).to_have_count(0)
                page.get_by_role('textbox', name='Findings / notes', exact=True).fill('Notes preserve malformed configuration')
                save(); assert metadata('server_dual')['component_sets'] == invalid
                page.get_by_role('button', name='Add component set', exact=True).click()
                field(1, 'Electric motor').select_option(hardware[1]['motor'])
                field(1, 'RPM column').select_option('rpm_b'); save()
                repaired = metadata('server_dual')['component_sets']
                assert len(repaired) == 1 and repaired[0]['rpm_column'] == 'rpm_b'
                assert metadata('server_dual')['notes'] == 'Notes preserve malformed configuration'
            malformed_path.write_bytes(original_meta)
            print('PASS: malformed saved sets remain visible, notes preserve raw settings, explicit replacement repairs them', flush=True)
            assert source_hashes(dataset) == before
            assert not errors, errors
            (output / 'results.json').write_text(json.dumps({'unchanged_source_files': len(before),
                'independent_channels': True, 'real_local_and_server_resume': True,
                'lifecycle_preserved': True, 'desktop_windows_and_browser_zoom': True,
                'page_errors': errors}, indent=2))
            print(f'PASS: keyboard, 1100px desktop, actual 125%/150% browser zoom, {len(before)} unchanged source files, no page errors', flush=True)
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            print(page.locator('body').aria_snapshot()[-16000:], flush=True)
            raise
        finally:
            context.close(); request.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3196)
    parser.add_argument('--backend-port', type=int, default=8196)
    args = parser.parse_args()
    output = ROOT / 'data/verification/component-sets'; output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-component-sets-') as directory:
        temporary = Path(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, dataset):
            run_checks(web, api, dataset, temporary, output)
    print('PASS: owned servers stopped and temporary fixtures/profile removed', flush=True)


if __name__ == '__main__': main()
