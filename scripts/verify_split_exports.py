"""Phase 5 live downloads with temporary uploads and an isolated Chromium profile.

Run with global Python/Playwright. Backend always uses its Python 3.13 venv.
Only owned listeners/profile/data are created or removed. Evidence is ignored
under data/verification/split-exports. Reuses the project's isolated servers.
"""
import argparse
import csv
import io
import json
from pathlib import Path
import tempfile

from playwright.sync_api import expect, sync_playwright

from verify_data_quality import ROOT, servers, upload
from verify_browser_zoom import capture_browser_view, set_browser_zoom


def download_csv(page, link, output, label, filename, tp_id, indices):
    link.scroll_into_view_if_needed()
    link.focus()
    with page.expect_download() as event:
        page.keyboard.press('Enter')
    download = event.value
    assert download.suggested_filename == filename, download.suggested_filename
    assert download.failure() is None
    path = output / f'{label}.csv'
    download.save_as(path)
    content = path.read_bytes()
    rows = list(csv.DictReader(io.StringIO(content.decode())))
    assert len(rows) == len(indices), (label, len(rows), len(indices))
    assert list(rows[0]) == ['time', 'signal', 'source_source_test_point_id',
                            'source_test_point_id', 'test_point_id']
    assert [row['test_point_id'] for row in rows] == [str(tp_id)] * len(rows)
    assert [float(row['time']) for row in rows] == [i / 10 for i in indices]
    assert [float(row['source_source_test_point_id']) for row in rows] == [900 + i for i in indices]
    assert [float(row['source_test_point_id']) for row in rows] == [42] * len(rows)
    for row, i in zip(rows, indices):
        if i == 25:
            assert row['signal'] == ''
        else:
            assert float(row['signal']) == float(f'{i * 1.234567890123e-9:.15g}')
    return content


def run_checks(web, api, temporary, output):
    extension = temporary / 'extension'
    extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({
        'manifest_version': 3, 'name': 'Split export zoom verification', 'version': '1.0',
        'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(() => {});')
    with sync_playwright() as playwright:
        request = playwright.request.new_context(base_url=api + '/')
        source = 'time,signal,test_point_id,source_test_point_id\n' + '\n'.join(
            f'{i / 10:.1f},{"" if i == 25 else f"{i * 1.234567890123e-9:.15g}"},{900 + i},42'
            for i in range(140))
        assert upload(request, 'export_alpha', source)['status'] == 'ready'
        assert upload(request, 'export_empty', source)['status'] == 'ready'
        payload = {'version': 1, 'test': 'export_alpha', 'test_points': [
            {'id': 7, 'name': 'A long TP name ' * 18, 'label': 'First run',
             'start_s': 2, 'end_s': 5, 'start_idx': 20, 'end_idx': 50},
            {'id': 9, 'name': 'Middle', 'label': '', 'start_s': 5, 'end_s': None,
             'start_idx': 50, 'end_idx': None},
            {'id': 17, 'name': 'Final', 'label': '', 'start_s': 8, 'end_s': None,
             'start_idx': 80, 'end_idx': None},
        ]}
        assert request.put('tests/export_alpha/testpoints', data=payload).ok
        assert request.put('tests/export_empty/testpoints', data={'test': 'export_empty', 'test_points': []}).ok
        saved = request.get('tests/export_alpha/testpoints').json()
        context = playwright.chromium.launch_persistent_context(
            str(temporary / 'profile'), channel='chromium', headless=True,
            no_viewport=True, accept_downloads=True,
            args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}',
                  '--window-size=1440,1000'])
        page = context.pages[0]
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            page.goto(web)
            page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='Uploads', exact=True).click()
            page.get_by_role('searchbox', name='Search upload history').fill('export_alpha')
            toggle = page.get_by_role('button', name='Split CSV downloads for export_alpha', exact=True)
            toggle.focus()
            page.keyboard.press('Enter')
            details = page.get_by_role('region', name='Split CSV downloads for export_alpha', exact=True)
            expect(details).to_contain_text('Saved test points')
            saved_csv = download_csv(page, details.get_by_role('link', name='Download CSV for export_alpha TP 7'),
                                     output, 'uploads-saved', 'export_alpha_tp7.csv', 7, range(20, 50))
            page.keyboard.press('Escape')
            expect(toggle).to_be_focused()
            expect(toggle).to_have_attribute('aria-expanded', 'false')
            with page.expect_download() as event:
                page.get_by_role('link', name='Download original CSV for export_alpha').click()
            raw = event.value
            assert raw.suggested_filename == 'export_alpha.csv'
            raw.save_as(output / 'original.csv')
            assert (output / 'original.csv').read_bytes() == source.encode()
            print('PASS: Uploads saved TP identity, original bytes, keyboard download/Escape', flush=True)

            failed_url = api + '/tests/export_alpha/testpoints'
            page.route(failed_url, lambda route: route.fulfill(status=503, json={'detail': 'split fixture failure'}))
            toggle.click()
            expect(details.get_by_role('alert')).to_contain_text('Could not load test points')
            page.unroute(failed_url)
            details.get_by_role('button', name='Retry', exact=True).click()
            expect(details.get_by_role('link')).to_have_count(3)
            cdp = context.new_cdp_session(page)
            window = cdp.send('Browser.getWindowForTarget')['windowId']
            baseline_dpr = page.evaluate('devicePixelRatio')
            for width, factor in [(1100, 1), (1440, 1.25), (1440, 1.5)]:
                cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': 1000}})
                set_browser_zoom(worker, page, factor)
                page.wait_for_function('dpr => Math.abs(devicePixelRatio - dpr) < .001', arg=baseline_dpr * factor)
                download_csv(page, details.get_by_role('link', name='Download CSV for export_alpha TP 9'),
                             output, f'uploads-{width}-{factor}', 'export_alpha_tp9.csv', 9, range(50, 80))
                assert details.evaluate('el => el.scrollWidth <= el.clientWidth + 1'), 'details overflow'
                action_layout = toggle.evaluate('''el => {
                    const table = el.closest('table');
                    const r = table.parentElement.getBoundingClientRect();
                    return {container: r.toJSON(), table: table.getBoundingClientRect().toJSON(),
                      actions: [...table.querySelectorAll('.upload-history-actions > *')].map(el => {
                        const a = el.getBoundingClientRect();
                        return {name: el.innerText, left: a.left, right: a.right,
                          fits: a.left >= r.left && a.right <= r.right + 1};
                    })};
                }''')
                assert all(action['fits'] for action in action_layout['actions']), action_layout
                capture_browser_view(cdp, output / f'uploads-{width}-{factor}.png')
            set_browser_zoom(worker, page, 1)
            page.get_by_role('searchbox', name='Search upload history').fill('export_empty')
            page.get_by_role('button', name='Split CSV downloads for export_empty', exact=True).click()
            expect(page.get_by_role('region', name='Split CSV downloads for export_empty')).to_contain_text('No saved test points')
            print('PASS: lazy retry/empty states, long names, desktop resize, real 125%/150% zoom', flush=True)

            page.get_by_role('searchbox', name='Search upload history').fill('export_alpha')
            page.get_by_role('button', name='Analyze export_alpha', exact=True).click()
            page.get_by_role('button', name='Split', exact=True).click()
            saved_link = page.get_by_role('link', name='Download CSV for TP 7', exact=True)
            expect(saved_link).to_be_visible()
            # Plot wheel/pan changes must not leak into the TP export range.
            plot = page.locator('.u-over').first
            plot.hover()
            page.mouse.wheel(0, -240)
            box = plot.bounding_box()
            page.keyboard.down('Shift')
            page.mouse.move(box['x'] + box['width'] * .4, box['y'] + box['height'] * .5)
            page.mouse.down()
            page.mouse.move(box['x'] + box['width'] * .6, box['y'] + box['height'] * .5, steps=5)
            page.mouse.up()
            page.keyboard.up('Shift')
            split_csv = download_csv(page, saved_link, output, 'split-saved', 'export_alpha_tp7.csv', 7, range(20, 50))
            assert split_csv == saved_csv
            page.get_by_role('spinbutton', name='End seconds for TP 7').fill('6.2')
            page.keyboard.press('Tab')
            download_csv(page, page.get_by_role('link', name='Download draft CSV for TP 7'), output,
                         'draft-edited', 'export_alpha_tp7_draft.csv', 7, range(20, 62))
            assert request.get('tests/export_alpha/testpoints').json() == saved
            page.get_by_role('checkbox', name='Open end for TP 7').check()
            draft_csv = download_csv(page, page.get_by_role('link', name='Download draft CSV for TP 7'), output,
                                     'draft-open-next', 'export_alpha_tp7_draft.csv', 7, range(20, 50))
            download_csv(page, page.get_by_role('link', name='Download draft CSV for TP 17'), output,
                         'draft-open-final', 'export_alpha_tp17_draft.csv', 17, range(80, 140))
            page.get_by_role('button', name='save *', exact=True).click()
            expect(page.get_by_role('button', name='save', exact=True)).to_be_disabled()
            after_save = download_csv(page, saved_link, output, 'split-after-save', 'export_alpha_tp7.csv', 7, range(20, 50))
            assert after_save == draft_csv
            print('PASS: saved entry-point parity, zoom/pan isolation, draft edit/open ends, Save parity', flush=True)

            page.get_by_role('button', name='+ new TP', exact=True).click()
            page.get_by_role('spinbutton', name='Start seconds for TP 18').fill('3')
            page.keyboard.press('Tab')
            page.get_by_role('spinbutton', name='End seconds for TP 18').fill('4')
            page.keyboard.press('Tab')
            download_csv(page, page.get_by_role('link', name='Download draft CSV for TP 18'), output,
                         'draft-new', 'export_alpha_tp18_draft.csv', 18, range(30, 40))
            assert all(tp['id'] != 18 for tp in request.get('tests/export_alpha/testpoints').json()['test_points'])
            for width, factor in [(1100, 1), (1440, 1.25), (1440, 1.5)]:
                cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': 1000}})
                set_browser_zoom(worker, page, factor)
                download_csv(page, page.get_by_role('link', name='Download draft CSV for TP 18'), output,
                             f'split-{width}-{factor}', 'export_alpha_tp18_draft.csv', 18, range(30, 40))
                assert page.get_by_role('link', name='Download draft CSV for TP 18').evaluate('''el => {
                    const row = el.parentElement.getBoundingClientRect();
                    const links = [...el.parentElement.children].map(e => e.getBoundingClientRect());
                    return row.right <= innerWidth && links.every(r => r.left >= row.left && r.right <= row.right + 1);
                }'''), 'Split row actions overflow'
                capture_browser_view(cdp, output / f'split-{width}-{factor}.png')
            set_browser_zoom(worker, page, 1)
            page.get_by_role('spinbutton', name='End seconds for TP 18').fill('3.01')
            page.keyboard.press('Tab')
            empty_link = page.get_by_label('Download draft CSV for TP 18', exact=True)
            expect(empty_link).to_have_attribute('aria-disabled', 'true')
            assert empty_link.get_attribute('href') is None
            # Discard and reopen Uploads: disclosure fetch must see the latest saved file.
            page.get_by_role('button', name='reset', exact=True).click()
            page.get_by_role('button', name='Uploads', exact=True).click()
            page.get_by_role('searchbox', name='Search upload history').fill('export_alpha')
            page.get_by_role('button', name='Split CSV downloads for export_alpha', exact=True).click()
            details = page.get_by_role('region', name='Split CSV downloads for export_alpha', exact=True)
            assert download_csv(page, details.get_by_role('link', name='Download CSV for export_alpha TP 7'), output,
                                'uploads-after-save', 'export_alpha_tp7.csv', 7, range(20, 50)) == after_save
            print('PASS: new unsaved TP, empty-range guard, Split resize/zoom, fresh Uploads after save', flush=True)
            unexpected = [error for error in errors if '503' not in error and 'split fixture failure' not in error]
            assert not unexpected, unexpected
            (output / 'results.json').write_text(json.dumps({
                'saved_points': saved, 'downloads': sorted(path.name for path in output.glob('*.csv')),
                'unexpected_browser_errors': unexpected}, indent=2))
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            print('Browser errors:', errors, flush=True)
            raise
        finally:
            context.close()
            request.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3110)
    parser.add_argument('--backend-port', type=int, default=8110)
    args = parser.parse_args()
    output = ROOT / 'data/verification/split-exports'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-split-exports-') as directory:
        temporary = Path(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, _dataset):
            run_checks(web, api, temporary, output)
    print('PASS: temporary fixtures/profile removed; owned servers stopped', flush=True)


if __name__ == '__main__':
    main()
