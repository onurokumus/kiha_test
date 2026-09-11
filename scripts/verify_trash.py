"""Phase 7d real trash/restore/delete checks with isolated data and Chromium."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright
from verify_data_quality import ROOT, servers, upload
from verify_browser_zoom import capture_browser_view, set_browser_zoom


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and p.name != 'tp_stats.json'
            and not p.name.startswith('.tp_stats.json.')}


def run_checks(web, api, dataset, temporary, output):
    extension = temporary / 'extension'; extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Trash zoom checks',
        'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    csv = 'time,signal\n' + ''.join(f'{i/10},{i}\n' for i in range(80))
    with sync_playwright() as p:
        request = p.request.new_context(base_url=api + '/')
        motor = request.post('components', data={'kind': 'motor', 'name': 'Motor with a descriptive laboratory identifier 🔧'}).json()
        for name in ('alpha', 'unrelated', 'from_edit', 'legacy_bin'):
            assert upload(request, name, csv, components={'motor': motor['id']}, description=f'{name}: retained notes and hardware')['status'] == 'ready'
        meta = request.patch('tests/alpha/meta', data={'notes': 'Keep all findings <literal> 🔧', 'user_meta': {'motor': 'legacy text'}}).json()
        doc = request.get('tests/alpha/annotations').json()
        assert request.put('tests/alpha/annotations', data={'expected_revision': 0, 'expected_data_bounds': doc['data_bounds'],
            'annotations': [{'id': str(uuid4()), 'start_s': 1, 'end_s': 3, 'text': 'Preserve interval'}]}).ok
        assert request.put('tests/alpha/testpoints', data={'test': 'alpha', 'test_points': [{'id': 3, 'name': 'Run', 'start_s': 0, 'end_s': 5}]}).ok
        # Analysis lazily adds durable IDs before its first metadata load (8a).
        # Include that additive file in lifecycle preservation checks.
        assert request.get('analysis-sources').ok
        before_alpha = hashes(dataset / 'tests/alpha')
        unrelated = hashes(dataset / 'tests/unrelated')
        registry = (dataset / 'components.json').read_bytes()
        # Exercise an actual pre-milestone name-based trash folder.
        legacy_source = (dataset / 'tests/legacy_bin').resolve()
        legacy_target = (dataset / 'trash/legacy_bin').resolve()
        assert legacy_source.parent == (dataset / 'tests').resolve() and legacy_target.parent == (dataset / 'trash').resolve()
        legacy_before = hashes(legacy_source)
        legacy_target.parent.mkdir(); legacy_source.rename(legacy_target)
        context = p.chromium.launch_persistent_context(str(temporary / 'profile'), channel='chromium', headless=True,
            no_viewport=True, args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]; errors, console, writes = [], [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('console', lambda e: console.append(e.text) if e.type == 'error' else None)
        page.on('request', lambda r: writes.append([r.method, r.url]) if r.url.startswith(api) and r.method in ('POST','PUT','PATCH','DELETE') else None)
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp = context.new_cdp_session(page)
        def bin(): return page.get_by_role('region', name='Trash bin', exact=True)
        def row(entry_id): return bin().locator(f'[data-trash-id="{entry_id}"]')
        def entries(): return request.get('trash').json()['entries']
        def open_trash():
            button = bin().get_by_role('button', name=re.compile(r'^Trash \('))
            if button.get_attribute('aria-expanded') == 'false': button.click()
        def soft_delete(name):
            button = page.get_by_role('button', name=f'Delete {name}', exact=True)
            expect(button).to_be_visible(timeout=15000); button.focus(); page.keyboard.press('Enter')
            page.get_by_role('alertdialog').get_by_role('button', name='Move to trash', exact=True).click()
            expect(button).not_to_be_visible()
            open_trash()
            return next(entry['id'] for entry in entries() if entry['name'] == name)
        def start_restore(entry_id):
            row(entry_id).get_by_role('button', name='Restore…', exact=True).click()
            field = row(entry_id).get_by_role('textbox', name='Restore name', exact=True)
            expect(field).to_be_focused(); return field
        def confirm_delete(label='Delete permanently'):
            page.get_by_role('alertdialog').get_by_role('button', name=label, exact=True).click()

        try:
            page.goto(web); page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='Uploads', exact=True).click(); open_trash()
            legacy_id = entries()[0]['id']
            expect(row(legacy_id)).to_contain_text('(estimated)')
            assert hashes(dataset / 'trash' / legacy_id / 'data') == legacy_before
            button = page.get_by_role('button', name='Delete alpha', exact=True)
            button.click(); page.keyboard.press('Escape'); expect(button).to_be_focused()
            first = soft_delete('alpha')
            assert hashes(dataset / 'trash' / first / 'data') == before_alpha
            page.reload(); page.wait_for_load_state('networkidle'); page.get_by_role('button', name='Uploads', exact=True).click(); open_trash()
            expect(row(first)).to_be_visible(); assert {e['id'] for e in entries()} == {first, legacy_id}
            assert upload(request, 'alpha', csv.replace(',70\n', ',170\n'), description='Second identically named test')['status'] == 'ready'
            second = soft_delete('alpha'); assert second != first
            assert upload(request, 'alpha', csv, description='Active third copy')['status'] == 'ready'
            assert request.get('analysis-sources').ok
            active = hashes(dataset / 'tests/alpha')
            field = start_restore(first)
            route_pattern = api + f'/trash/{first}/restore'
            page.route(route_pattern, lambda route: route.fulfill(status=503, json={'detail': 'Injected restore failure'}))
            page.keyboard.press('Enter'); expect(bin().get_by_role('alert')).to_contain_text('Injected restore failure')
            expect(field).to_have_value('alpha'); page.unroute(route_pattern)
            row(first).get_by_role('button', name='Restore test', exact=True).click()
            expect(bin().get_by_role('alert')).to_contain_text('already exists')
            expect(field).to_have_value('alpha')
            assert hashes(dataset / 'tests/alpha') == active and hashes(dataset / 'trash' / first / 'data') == before_alpha
            field.fill('../invalid'); expect(row(first).get_by_role('button', name='Restore test', exact=True)).to_be_disabled()
            field.fill('alpha_restored'); page.keyboard.press('Enter')
            expect(row(first)).not_to_be_visible()
            restored = request.get('tests/alpha_restored').json()
            assert restored == {**meta, 'name': 'alpha_restored'}
            assert request.get('tests/alpha_restored/testpoints').json()['test'] == 'alpha_restored'
            after = hashes(dataset / 'tests/alpha_restored')
            preserved = [key for key in before_alpha if key not in ('meta.json','testpoints.json','.upload/manifest.json')]
            assert all(after[key] == before_alpha[key] for key in preserved)
            print('PASS: real duplicate-name deletes, legacy migration, reload persistence, restore503 retry/409 conflict and named restore preserve source data', flush=True)

            # The existing Edit delete action must feed the same persistent list.
            page.get_by_role('button', name='Edit components for from_edit', exact=True).click()
            page.get_by_role('button', name='delete test', exact=True).click()
            page.get_by_role('alertdialog').get_by_role('button', name='Move to trash', exact=True).click()
            page.get_by_role('button', name='Uploads', exact=True).click(); open_trash()
            edit_id = next(entry['id'] for entry in entries() if entry['name'] == 'from_edit')
            expect(row(edit_id)).to_be_visible()
            field = start_restore(second)
            for width, factor in ((1440,1), (1100,1), (1440,1.25), (1440,1.5)):
                window_id = cdp.send('Browser.getWindowForTarget')['windowId']
                cdp.send('Browser.setWindowBounds', {'windowId': window_id, 'bounds': {'width': width, 'height': 1000}})
                set_browser_zoom(worker, page, factor)
                field.fill('restored_' + 'long_identifier_' * 8)
                expect(row(second).get_by_role('button', name='Restore test', exact=True)).to_be_enabled()
                row(second).locator('form').scroll_into_view_if_needed()
                assert bin().evaluate('el => el.scrollWidth <= el.clientWidth + 1'), 'Trash overflows horizontally'
                assert field.evaluate('el => el.getBoundingClientRect().right <= document.documentElement.clientWidth + 1')
                capture_browser_view(cdp, output / f'trash-{width}-{factor}.png')
            row(second).get_by_role('button', name='Cancel restore', exact=True).click()
            expect(bin().get_by_role('button', name=re.compile(r'^Trash \('))).to_be_focused()
            page.route(api + '/trash', lambda route: route.fulfill(status=503, json={'detail':'Injected trash list failure'}) if route.request.method == 'GET' else route.continue_())
            bin().get_by_role('button', name='Refresh trash', exact=True).click()
            expect(bin().get_by_role('alert')).to_contain_text('Injected trash list failure')
            expect(row(second).get_by_role('button', name='Delete permanently', exact=True)).to_be_disabled()
            page.unroute(api + '/trash'); bin().get_by_role('button', name='Refresh trash', exact=True).click()
            expect(row(second).get_by_role('button', name='Delete permanently', exact=True)).to_be_enabled()
            row(second).get_by_role('button', name='Delete permanently', exact=True).click(); page.keyboard.press('Escape')
            assert any(entry['id'] == second for entry in entries())
            page.route(api + '/trash', lambda route: route.fulfill(status=503, json={'detail':'Injected deletion failure'}) if route.request.method == 'DELETE' else route.continue_())
            row(second).get_by_role('button', name='Delete permanently', exact=True).click(); confirm_delete()
            expect(bin().get_by_role('alert')).to_contain_text('Injected deletion failure')
            assert any(entry['id'] == second for entry in entries())
            page.unroute(api + '/trash')
            row(second).get_by_role('button', name='Delete permanently', exact=True).click(); confirm_delete()
            expect(row(second)).not_to_be_visible()
            assert hashes(dataset / 'tests/alpha') == active
            print('PASS: Edit deletion integration, keyboard/1100px/125%/150%, list/deletion503 recovery, single-delete confirmation and active-copy preservation', flush=True)

            # Hold a real Windows file open so the deletion worker partially
            # removes the entry, persists deleting, and has to offer Retry only.
            lock_path = dataset / 'trash' / edit_id / 'data/raw.csv'
            with lock_path.open('rb'):
                bin().get_by_role('button', name='Delete all', exact=True).click()
                expect(page.get_by_role('alertdialog')).to_contain_text('Only the entries currently listed')
                assert upload(request, 'late', csv)['status'] == 'ready'
                late_response = request.delete('tests/late'); assert late_response.ok
                late = late_response.json()['trash_id']
                confirm_delete('Delete all permanently')
                expect(row(edit_id)).to_contain_text('Permanent deletion incomplete')
                expect(row(edit_id).get_by_role('button', name='Restore…', exact=True)).to_be_disabled()
                expect(row(late)).to_be_visible()
                assert {entry['id'] for entry in entries()} == {edit_id, late}
                row(edit_id).scroll_into_view_if_needed(); capture_browser_view(cdp, output / 'partial-deletion.png')
            row(edit_id).get_by_role('button', name='Retry deletion', exact=True).click(); confirm_delete()
            expect(row(edit_id)).not_to_be_visible(); expect(row(late)).to_be_visible()
            bin().get_by_role('button', name='Delete all', exact=True).click(); confirm_delete('Delete all permanently')
            expect(bin()).to_contain_text('The trash is empty.')
            assert entries() == []
            # Deleting the final active test must not hide the only way back.
            last_ids = {name: soft_delete(name) for name in ('alpha', 'alpha_restored', 'unrelated')}
            assert request.get('tests').json() == []
            page.reload(); page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='Uploads', exact=True).click(); open_trash()
            for name, entry_id in last_ids.items():
                field = start_restore(entry_id); expect(field).to_have_value(name)
                page.keyboard.press('Enter'); expect(row(entry_id)).not_to_be_visible()
                expect(page.get_by_role('button', name=f'Analyze {name}', exact=True)).to_be_visible()
            assert entries() == []
            assert hashes(dataset / 'tests/alpha') == active
            assert hashes(dataset / 'tests/unrelated') == unrelated
            assert hashes(dataset / 'tests/alpha_restored') == after
            assert (dataset / 'components.json').read_bytes() == registry
            assert not errors, errors
            assert all(any(code in message for code in ('503','409')) for message in console), console
            assert all('/trash' in url or re.search(r'/tests/(alpha|alpha_restored|unrelated|from_edit)$', url) for _,url in writes), writes
            (output / 'results.json').write_text(json.dumps({'preserved_restored_files': preserved,
                'unchanged_active_files': len(active) + len(unrelated), 'first_id':first, 'second_id':second,
                'page_errors':errors, 'console_errors':console, 'writes':writes}, indent=2), encoding='utf-8')
            print('PASS: actual locked-file partial deletion/retry, snapshot Delete all excludes concurrent new entry, empty-library reload/restore and active data/registry unchanged', flush=True)
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            print(page.locator('body').aria_snapshot()[-11000:], flush=True)
            raise
        finally:
            context.close(); request.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3210)
    parser.add_argument('--backend-port', type=int, default=8210)
    args = parser.parse_args()
    output = ROOT / 'data/verification/trash'; output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-trash-') as folder:
        temporary = Path(folder)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web,api,dataset):
            run_checks(web,api,dataset,temporary,output)
    print('PASS: isolated fixtures/profile removed; only owned servers stopped', flush=True)


if __name__ == '__main__': main()
