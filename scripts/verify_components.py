"""Phase7c isolated real upload, registry, correction/recovery and desktop checks."""
import argparse
import json
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright
from verify_data_quality import ROOT, servers, upload
from verify_test_notes import source_hashes
from verify_browser_zoom import capture_browser_view, set_browser_zoom


def run_checks(web, api, dataset, temporary, output):
    extension = temporary / 'extension'; extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Component zoom verification',
        'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    csv = 'time,signal\n' + ''.join(f'{i/10},{i}\n' for i in range(80))
    original = temporary / 'hardware.csv'; original.write_text(csv, encoding='utf-8')
    labels = {'propeller': 'Propeller', 'motor': 'Electric motor', 'esc': 'ESC'}
    with sync_playwright() as p:
        request = p.request.new_context(base_url=api + '/')
        assert upload(request, 'legacy', csv)['status'] == 'ready'
        assert upload(request, 'unrelated', csv)['status'] == 'ready'
        legacy_path = dataset / 'tests/legacy/meta.json'
        legacy = json.loads(legacy_path.read_text()); legacy.pop('components', None); legacy.pop('components_revision', None)
        legacy['user_meta'] = {'motor': 'legacy motor text', 'components': 'legacy field', '__proto__': 'keep'}
        legacy['notes'] = 'Existing findings'
        legacy_path.write_text(json.dumps(legacy), encoding='utf-8')
        doc = request.get('tests/legacy/annotations').json()
        assert request.put('tests/legacy/annotations', data={'expected_revision': 0, 'expected_data_bounds': doc['data_bounds'],
            'annotations': [{'id': str(uuid4()), 'start_s': 2, 'text': 'Keep marker'}]}).ok
        annotations = (dataset / 'tests/legacy/annotations.json').read_bytes()
        unrelated = (dataset / 'tests/unrelated/meta.json').read_bytes()
        context = p.chromium.launch_persistent_context(str(temporary / 'profile'), channel='chromium', headless=True,
            no_viewport=True, args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]; errors, console, writes = [], [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('console', lambda e: console.append(e.text) if e.type == 'error' else None)
        page.on('request', lambda r: writes.append([r.method, r.url]) if r.url.startswith(api) and r.method in ('POST','PUT','PATCH','DELETE') else None)
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp = context.new_cdp_session(page)

        def component_set(): return page.get_by_role('group', name='Component set 1', exact=True)
        def picker(): return component_set().get_by_role('group', name='Component associations', exact=True)
        def select(kind): return picker().get_by_role('combobox', name=labels[kind], exact=True)
        def create(kind, name):
            select(kind).select_option('__new')
            field = picker().get_by_role('textbox', name=f'New {labels[kind].lower()} name', exact=True)
            expect(field).to_be_focused(); field.fill(name); page.keyboard.press('Enter')
            expect(field).not_to_be_visible()
            expect(select(kind)).to_be_focused()
            return select(kind).input_value()
        def stage(files):
            with page.expect_file_chooser() as chooser:
                page.get_by_role('button', name=re.compile('Import test data')).click()
            chooser.value.set_files(files)
            page.get_by_label('Uploaded by', exact=True).fill('Hardware lab')
            expect(component_set()).to_have_count(0)
            page.get_by_role('button', name='Add component set', exact=True).click()
            expect(select('motor')).to_be_enabled()
        def edit(name):
            page.get_by_role('button', name='Uploads', exact=True).click()
            page.get_by_role('searchbox', name='Search upload history').fill('')
            button = page.get_by_role('button', name=f'Edit components for {name}', exact=True)
            button.focus(); page.keyboard.press('Enter')
            expect(select('motor')).to_be_enabled()
        def save():
            button = page.get_by_role('button', name='Save notes and metadata', exact=True)
            button.focus(); page.keyboard.press('Enter'); expect(button).to_be_disabled()
        def check_saved(name, ids):
            meta = request.get(f'tests/{name}').json()
            actual = meta.get('components')
            assert actual == ids, (name, actual, ids)
            assert len(meta['component_sets']) == 1, meta
            assert meta['component_sets'][0]['components'] == ids, meta
        def pause_and_resume(path, name, server_only=False):
            held = []
            pattern = api + '/uploads/*/complete?*'
            page.route(pattern, lambda route: held.append(route))
            page.get_by_role('button', name='Upload 1 file', exact=True).click()
            page.wait_for_function("document.body.textContent.includes('finalizing')")
            assert held
            records = page.evaluate("JSON.parse(localStorage.getItem('ptt.uploadSessions.v1'))")
            assert records[-1]['component_sets'][0]['components'] == ids
            assert 'components' not in records[-1], records[-1]
            page.get_by_role('button', name='Pause', exact=True).last.click()
            for route in held: route.abort()
            page.unroute(pattern)
            if server_only: page.evaluate("localStorage.removeItem('ptt.uploadSessions.v1')")
            page.reload(); page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='Uploads', exact=True).click()
            with page.expect_file_chooser() as chooser:
                page.get_by_role('button', name=f'Select original CSV to resume {name}', exact=True).click()
            chooser.value.set_files(str(path))
            expect(page.get_by_role('button', name=f'Edit components for {name}', exact=True)).to_be_visible(timeout=20000)
            check_saved(name, ids)

        try:
            page.goto(web); page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='Uploads', exact=True).click()
            stage(str(original))
            propeller = create('propeller', 'helix_1')
            select('motor').select_option('__new')
            field = picker().get_by_role('textbox', name='New electric motor name', exact=True)
            field.fill('x' * 121)
            expect(picker().get_by_role('button', name='Add component', exact=True)).to_be_disabled()
            expect(page.get_by_role('button', name='Upload 1 file', exact=True)).to_be_disabled()
            field.fill('motor_1 🔧')
            page.route(api + '/components', lambda route: route.fulfill(status=503, json={'detail': 'Injected component creation failure'})
                       if route.request.method == 'POST' else route.continue_())
            page.keyboard.press('Enter')
            expect(picker().get_by_role('alert')).to_contain_text('Injected component creation failure')
            expect(field).to_have_value('motor_1 🔧')
            page.unroute(api + '/components')
            picker().get_by_role('button', name='Add component', exact=True).click()
            expect(field).not_to_be_visible(); motor = select('motor').input_value()
            esc = create('esc', 'ESC <literal>')
            ids = {'propeller': propeller, 'motor': motor, 'esc': esc}
            pause_and_resume(original, 'hardware')
            print('PASS: per-type creation/validation/503 retry, stable IDs and real chunk Pause/reload/reselect/resume', flush=True)

            server_file = temporary / 'server_hardware.csv'; server_file.write_text(csv, encoding='utf-8')
            stage(str(server_file))
            for kind in labels:
                expect(select(kind)).to_have_value('')
                select(kind).select_option(ids[kind])
            pause_and_resume(server_file, 'server_hardware', server_only=True)
            stage([{'name': f'batch_hardware_{i}.csv', 'mimeType': 'text/csv', 'buffer': csv.encode()} for i in (1,2)])
            assert create('propeller', 'HELIX_1') == propeller
            for kind in ('motor','esc'): select(kind).select_option(ids[kind])
            page.get_by_role('button', name='Upload 2 files', exact=True).click()
            expect(page.get_by_role('button', name='Edit components for batch_hardware_2', exact=True)).to_be_visible(timeout=20000)
            for name in ('batch_hardware_1','batch_hardware_2'): check_saved(name, ids)
            assert len(request.get('components').json()['components']) == 3
            before = source_hashes(dataset)
            print('PASS: server-only recovery, blank new batches, shared two-file assignments and duplicate-name reuse', flush=True)

            edit('legacy')
            for kind in labels: expect(select(kind)).to_have_value('')
            expect(page.get_by_role('textbox', name='Findings / notes', exact=True)).to_have_value('Existing findings')
            select('propeller').select_option(propeller); select('esc').select_option(esc)
            motor2 = create('motor', 'motor_2')
            assigned = {**ids, 'motor': motor2}
            page.get_by_role('button', name='Uploads', exact=True).click()
            expect(page.get_by_role('alertdialog')).to_contain_text('Discard unsaved')
            page.keyboard.press('Escape'); expect(select('motor')).to_have_value(motor2)
            url = api + '/tests/legacy/meta'
            page.route(url, lambda route: route.fulfill(status=503, json={'detail': 'Injected component assignment failure'}))
            page.get_by_role('button', name='Save notes and metadata', exact=True).click()
            expect(page.get_by_role('status').filter(has_text='Injected component assignment failure')).to_be_visible()
            expect(select('motor')).to_have_value(motor2)
            assert request.get('tests/legacy').json().get('components') is None
            page.unroute(url); save(); check_saved('legacy', assigned)
            saved = request.get('tests/legacy').json()
            assert saved['user_meta'] == legacy['user_meta'] and saved['notes'] == 'Existing findings'
            assert saved['components_revision'] == 1
            assert saved['component_sets_revision'] == 1
            select('motor').select_option(motor)
            remote_sets = [{**saved['component_sets'][0], 'components': {**assigned, 'esc': None}}]
            remote = request.patch('tests/legacy/meta', data={'component_sets': remote_sets,
                'expected_component_sets_revision': saved['component_sets_revision']})
            assert remote.ok
            page.get_by_role('button', name='Save notes and metadata', exact=True).click()
            expect(page.get_by_role('status').filter(has_text='Component sets changed')).to_be_visible()
            expect(select('motor')).to_have_value(motor)
            page.get_by_role('button', name='Reload saved metadata', exact=True).click()
            page.get_by_role('alertdialog').get_by_role('button', name='Reload metadata', exact=True).click()
            expect(select('motor')).to_have_value(motor2); expect(select('esc')).to_have_value('')
            select('motor').select_option(motor); save()
            for kind in labels: select(kind).select_option('')
            save(); check_saved('legacy', {kind: None for kind in labels})
            for kind in labels: select(kind).select_option(assigned[kind])
            save(); page.reload(); page.wait_for_load_state('networkidle'); edit('legacy')
            for kind in labels: expect(select(kind)).to_have_value(assigned[kind])
            select('motor').select_option('__new')
            picker().get_by_role('textbox', name='New electric motor name', exact=True).fill('Unsaved new motor')
            page.get_by_role('button', name='reset drafts', exact=True).click()
            expect(picker().get_by_role('textbox')).to_have_count(0)
            expect(select('motor')).to_have_value(motor2)
            print('PASS: legacy correction/clear/reload, metadata/annotations preservation, failed-save retry, stale409 and draft reset', flush=True)

            # A missing registry entry stays visibly unresolved until corrected.
            registry_path = dataset / 'components.json'; registry = registry_path.read_bytes()
            catalog = json.loads(registry); catalog['components'] = [c for c in catalog['components'] if c['id'] != motor2]
            registry_path.write_text(json.dumps(catalog), encoding='utf-8')
            page.reload(); page.wait_for_load_state('networkidle'); edit('legacy')
            expect(picker().get_by_role('alert')).to_contain_text('ID is retained')
            expect(select('motor')).to_have_value(motor2)
            registry_path.write_bytes(registry)
            # Inject read error and recover using visible Reload control.
            page.get_by_role('button', name='Uploads', exact=True).click()
            page.route(api + '/components', lambda route: route.fulfill(status=503, json={'detail': 'Injected component list failure'}))
            page.get_by_role('button', name='Edit components for legacy', exact=True).click()
            expect(picker().get_by_role('alert')).to_contain_text('Injected component list failure')
            page.unroute(api + '/components')
            picker().get_by_role('button', name='Reload components', exact=True).click()
            expect(select('motor')).to_be_enabled(); expect(select('motor')).to_have_value(motor2)

            window_id = cdp.send('Browser.getWindowForTarget')['windowId']
            for width, factor in ((1440,1),(1100,1),(1440,1.25),(1440,1.5)):
                cdp.send('Browser.setWindowBounds', {'windowId': window_id, 'bounds': {'width': width, 'height': 1000}})
                set_browser_zoom(worker, page, factor)
                picker().scroll_into_view_if_needed()
                for kind in labels:
                    assert select(kind).evaluate('el=>{const r=el.getBoundingClientRect();return r.width>80 && r.left>=0 && r.right<=innerWidth;}')
                capture_browser_view(cdp, output / f'edit-{width}-{factor}.png')
                page.get_by_role('button', name='Uploads', exact=True).click()
                page.get_by_role('searchbox', name='Search upload history').fill('motor_2')
                expect(page.get_by_role('button', name='Edit components for legacy', exact=True)).to_contain_text('motor_2')
                expect(page.get_by_role('button', name='Edit components for hardware', exact=True)).to_have_count(0)
                capture_browser_view(cdp, output / f'history-{width}-{factor}.png')
                if factor in (1.25, 1.5):
                    stage(str(original))
                    for kind in labels: select(kind).select_option(ids[kind])
                    picker().scroll_into_view_if_needed()
                    for kind in labels:
                        assert select(kind).evaluate('el=>{const r=el.getBoundingClientRect();return r.width>80 && r.left>=0 && r.right<=innerWidth;}')
                    select('esc').select_option('__new')
                    picker().get_by_role('textbox', name='New esc name', exact=True).fill('a long component name ' * 5)
                    expect(picker().get_by_role('button', name='Add component', exact=True)).to_be_enabled()
                    picker().scroll_into_view_if_needed()
                    capture_browser_view(cdp, output / f'upload-{width}-{factor}.png')
                    picker().get_by_role('button', name='Cancel new component', exact=True).click()
                    page.get_by_role('region', name='Import setup', exact=True).get_by_role('button', name='Cancel', exact=True).click()
                edit('legacy')
            assert source_hashes(dataset) == before
            assert (dataset / 'tests/unrelated/meta.json').read_bytes() == unrelated
            assert (dataset / 'tests/legacy/annotations.json').read_bytes() == annotations
            assert not errors, errors
            assert all(any(code in error for code in ('503','409','ERR_FAILED','ERR_ABORTED')) for error in console), console
            assert all('/uploads' in url or url.endswith('/components') or url.endswith('/tests/legacy/meta') for _,url in writes), writes
            (output / 'results.json').write_text(json.dumps({'unchanged_source_files': len(before), 'ids': ids,
                'writes': writes, 'page_errors': errors, 'console_errors': console}, indent=2), encoding='utf-8')
            print(f'PASS: missing/list-failure recovery, keyboard/1100px/125%/150%, searchable assignments, {len(before)} source files unchanged', flush=True)
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            print(page.locator('body').aria_snapshot()[-9000:], flush=True)
            raise
        finally:
            context.close(); request.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3200)
    parser.add_argument('--backend-port', type=int, default=8200)
    args = parser.parse_args()
    output = ROOT / 'data/verification/components'; output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-components-') as folder:
        temporary = Path(folder)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web,api,dataset):
            run_checks(web,api,dataset,temporary,output)
    print('PASS: isolated fixtures/profile removed; only owned servers stopped', flush=True)


if __name__ == '__main__': main()
