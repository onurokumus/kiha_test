"""Phase 6g real ZIP sidecars, loaded PNG context and compatibility downloads.

Isolated fixtures/profile; backend/native operations stay on Python 3.13.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import tempfile
import time
import zipfile

from playwright.sync_api import expect, sync_playwright
import verify_xy_exports as xy
import verify_plot_exports as single
import verify_multi_plot_exports as multi
from verify_data_quality import ROOT, servers, wait_until
from verify_filter_overlay import dataset_hashes
from verify_browser_zoom import set_browser_zoom, capture_browser_view, wait_for_chart_layout
from verify_time_y_zoom import instrument

DERIVED = 'export_derived_N'
BASE = 'export_base_N'
# The legacy time-trace endpoint still uses comma-separated column lists.
# Exact comma-containing names are covered in the XY-only regression suite.
COLS = [DERIVED, xy.X, xy.TINY, xy.REFERENCE]


def edit(request, test, expression, replace=False):
    specs = ([{'name': BASE, 'expression': '{' + xy.Y + '} + 1'}] if not replace else [])
    specs.append({'name': DERIVED, 'expression': expression, 'replace': replace})
    response = request.post(f'tests/{test}/edit', data={'formulas': specs})
    assert response.ok, response.text()
    status = wait_until(lambda: (value if (value := request.get(f'tests/{test}/status').json())['status'] in ('ready', 'error') else None), 'formula edit')
    assert status['status'] == 'ready', status
    meta = request.get(f'tests/{test}').json()
    for col in meta['columns']:
        if col != meta['time_column']:
            assert request.get(f'tests/{test}/tp_stats', params={'col': col}).ok


def seed(page):
    settings = {'scatterX': xy.X, 'scatterY': xy.REFERENCE, 'gridColumns': COLS, 'clustering': False}
    session = {'version': 1, 'currentTest': xy.A, 'xAxis': xy.X, 'yAxis': xy.REFERENCE, 'axesUserSet': True,
               'selections': [{'test': t, 'tpId': tp, 'hidden': False} for t, tp in ((xy.A, 7), (xy.A, 9), (xy.B, 7))],
               'plotConfigs': COLS, 'plotsUserEdited': True, 'plotDensity': 'quad', 'viewMode': 'tp',
               'xySource': 'tp', 'xyXCols': [xy.X] * 4, 'xyYCols': COLS,
               'plotFilters': [{'kind': 'moving_avg', 'winS': '.051'}, {}, {}, {}], 'scatterCollapsed': True}
    page.add_init_script(f'''if(!sessionStorage.getItem('metadata-seeded')){{
      localStorage.clear();sessionStorage.setItem('metadata-seeded','true');
      localStorage.setItem('ptt.settings.v1',{json.dumps(json.dumps(settings))});
      localStorage.setItem('ptt.analysis-session.v1',{json.dumps(json.dumps(session))});}}''')
    page.add_init_script(multi.CANVAS_INSTRUMENT)


def run_checks(web, api, dataset, temporary, output):
    packages = []; errors = []; downloads = []; writes = []; image_requests = []
    def settled(page):
        page.wait_for_load_state('networkidle')
        for target in page.locator('[role=group][aria-label$=" plot"]').all():
            target.locator('.uplot').wait_for()
        wait_for_chart_layout(page)

    def panel(page):
        button = page.get_by_role('button', name=re.compile(r'^Export .* plot$')).first
        button.focus(); page.keyboard.press('Enter')
        modal = page.get_by_role('dialog', name=button.get_attribute('aria-label'), exact=True)
        expect(modal).to_be_visible(); single.check_panel_layout(modal)
        expect(modal.get_by_role('checkbox', name='Include analysis metadata (ZIP)', exact=True)).to_be_checked()
        return modal

    def close(page, modal):
        page.keyboard.press('Escape'); expect(modal).not_to_be_visible()

    def check_context(record):
        if not isinstance(record, dict): return
        if 'equations' in record:
            names = [entry['name'] for entry in record['equations']]
            if DERIVED in names:
                assert BASE in names, record
                assert next(entry for entry in record['equations'] if entry['name'] == DERIVED)['engine'] == 'safe-polars-v1'
            assert 'not_retained' in record['edit_history_status']
            assert record['source']['file_observations']['data.parquet'] is not None
        for value in record.values():
            if isinstance(value, dict): check_context(value)
            elif isinstance(value, list):
                for item in value: check_context(item)

    def package(page, modal, label, image=False, multiple=False):
        button = modal.get_by_role('button', name=('Download combined PNG' if multiple else 'Download PNG') if image else 'Download CSV ZIP' if multiple else 'Download CSV', exact=True)
        expect(button).to_be_enabled()
        path, _ = single.download(page, button, output, label, 'zip')
        with zipfile.ZipFile(path) as archive:
            document = json.loads(archive.read('analysis.json'))
            assert document['schema'] == 'kiha-analysis-v1'
            assert document['format'] == ('png' if image else 'csv')
            check_context(document)
            if image:
                info = document['file']; content = archive.read(info['name'])
                assert info['sha256'] == hashlib.sha256(content).hexdigest()
                png = output / (label + '.png'); png.write_bytes(content)
                single.png_inspect(page, png, [])
                sent = image_requests[-1]
                assert document['plots'] == sent['plots']
                for plot in document['plots']:
                    assert plot['sources'] and plot['axes']['x']['min'] < plot['axes']['x']['max']
                    assert plot['legend']
            else:
                for plot in document['plots']:
                    content = archive.read(plot['file'])
                    assert plot['sha256'] == hashlib.sha256(content).hexdigest()
                    rows = list(csv.DictReader(io.StringIO(content.decode('utf-8'))))
                    assert len(rows) == plot['rows'] == sum(source['exported_rows'] for source in plot['sources'])
                    for source in plot['sources']:
                        actual = [row for row in rows if row['source_test'] == source['test'] and row['test_point_id'] == (source['test_point_id'] or '')]
                        assert len(actual) == source['exported_rows']
                        if actual and 'sample_index' in actual[0]:
                            assert source['exported_centers']['first_index'] == int(actual[0]['sample_index'])
                            assert source['exported_centers']['last_index'] == int(actual[-1]['sample_index'])
                        if actual and plot['kind'] == 'spectrum':
                            assert source['processing']['method']['nfft'] == int(actual[0]['method_nfft'])
                            assert source['processing']['method']['bin_spacing_hz'] == float(actual[0]['method_bin_spacing_hz'])
            packages.append({'file': path.name, 'format': document['format'], 'plots': len(document['plots']), 'bytes': path.stat().st_size})
            return document

    extension = temporary / 'extension'; extension.mkdir()
    (extension/'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Metadata zoom', 'version': '1.0',
        'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (extension/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as playwright:
        request = playwright.request.new_context(base_url=api+'/'); xy.fixtures(request)
        for test in (xy.A, xy.B): edit(request, test, '{'+BASE+'} - 1')
        hashes = dataset_hashes(dataset)
        context = playwright.chromium.launch_persistent_context(str(temporary/'profile'), channel='chromium', headless=True,
            no_viewport=True, accept_downloads=True, args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]; state = {'fail': False, 'hold': False, 'freeze': None}; held = []
        page.on('download', lambda item: downloads.append(item.suggested_filename))
        page.on('pageerror', lambda error: errors.append(error.stack or str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
        page.on('request', lambda req: writes.append((req.method, req.url)) if req.url.startswith(api)
                and req.method not in ('GET', 'HEAD', 'OPTIONS') and not any(path in req.url for path in ('/export-progress','/plot-export','/spectrum-export','/xy-export','/plot-image-export')) else None)
        def images(route):
            raw = route.request.post_data_buffer
            metadata = json.loads(raw.split(b'\n', 1)[0]); image_requests.append(metadata)
            if state['fail']:
                response = route.fetch(post_data=b'bad metadata\n')
                assert response.status == 400 and 'content-disposition' not in response.headers
                route.fulfill(response=response)
            elif state['hold']: held.append((route, route.fetch()))
            else: route.continue_()
        context.route('**/plot-image-export', images)
        context.route('**/src/utils/uplotSync.ts*', instrument)
        context.route('**/api/tests', lambda route: route.fulfill(json=state['freeze']) if state['freeze'] else route.continue_())
        try:
            seed(page); page.goto(web); settled(page)
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            modes = page.get_by_role('group', name='Plot mode', exact=True)
            modal = panel(page)
            for choice in ('Original', 'Filtered', 'Original and filtered'):
                modal.get_by_role('radio', name=choice, exact=True).check()
                doc = package(page, modal, 'tp-'+choice.replace(' ', '-'))
                processing = doc['plots'][0]['sources'][0]['processing']
                assert (processing['filter'] is None) == (choice == 'Original')
            modal.get_by_role('radio', name='Original', exact=True).check()
            doc = package(page, modal, 'tp-filtered-display', image=True)
            assert all(trace['kind'] == 'filtered' for source in doc['plots'][0]['sources'] for trace in source['traces'])
            close(page, modal)
            for mode in ('Full test', 'Spectrum', 'XY'):
                modes.get_by_role('button', name=mode, exact=True).click(); settled(page)
                if mode == 'Spectrum':
                    page.get_by_role('combobox', name='Estimator', exact=True).select_option('welch'); settled(page)
                modal = panel(page); package(page, modal, mode+'-single'); package(page, modal, mode+'-single-image', image=True); close(page, modal)
                multi.trigger(page).click(); modal = page.get_by_role('dialog', name='Export selected plots', exact=True)
                expect(modal.get_by_role('checkbox', name='Include analysis metadata (ZIP)', exact=True)).to_be_checked()
                package(page, modal, mode+'-layout', multiple=True); package(page, modal, mode+'-layout-image', image=True, multiple=True); close(page, modal)
            print('PASS: real Time original/filtered/both, Full, Welch and XY CSV/image sidecars, equations, native rows/counts and file hashes', flush=True)
            assert dataset_hashes(dataset) == hashes

            # Hold catalog identity while a real edit changes source equations.
            # Image must use its existing loaded response; CSV must use new data.
            state['freeze'] = request.get('tests').json()
            edit(request, xy.A, '{'+BASE+'} * 2', replace=True)
            modal = panel(page)
            image = package(page, modal, 'loaded-before-edit', image=True)
            numerical = package(page, modal, 'calculated-after-edit')
            old = image['plots'][0]['sources'][0]['loaded']['analysis']['equations']
            new = numerical['plots'][0]['sources'][0]['equations']
            assert next(eq['expression'] for eq in old if eq['name'] == DERIVED) == '{'+BASE+'} - 1'
            assert next(eq['expression'] for eq in new if eq['name'] == DERIVED) == '{'+BASE+'} * 2'
            close(page, modal); state['freeze'] = None; page.reload(); settled(page)
            hashes = dataset_hashes(dataset)  # explicit fixture edit is complete
            modal = panel(page); before = len(downloads); state['fail'] = True
            modal.get_by_role('button', name='Download PNG', exact=True).click()
            expect(modal.get_by_role('alert')).to_contain_text('Invalid image metadata', timeout=20000); assert len(downloads) == before
            state['fail'] = False; package(page, modal, 'image-retry', image=True)
            before = len(downloads); state['hold'] = True
            modal.get_by_role('button', name='Download PNG', exact=True).click()
            deadline = time.monotonic()+15
            while not held and time.monotonic() < deadline: page.wait_for_timeout(50)
            assert held; close(page, modal); state['hold'] = False
            for route, response in held: route.fulfill(response=response)
            held.clear(); page.wait_for_timeout(250); assert len(downloads) == before
            print('PASS: PNG keeps old loaded equations while CSV records the executed new source; image failure/Retry/Close are atomic', flush=True)

            # Nine slots and zoomed desktop controls still use one package.
            page.evaluate('''cols=>{const s=JSON.parse(localStorage.getItem('ptt.analysis-session.v1'));
              s.plotDensity='nine';s.plotConfigs=[...cols,...cols,cols[0]];s.xyYCols=s.plotConfigs;
              s.xyXCols=Array(9).fill('position_N');localStorage.setItem('ptt.analysis-session.v1',JSON.stringify(s));}''', COLS)
            page.reload(); settled(page)
            multi.trigger(page).click(); modal = page.get_by_role('dialog', name='Export selected plots', exact=True)
            package(page, modal, 'nine-slots', multiple=True); package(page, modal, 'nine-slots-image', image=True, multiple=True); close(page, modal)
            cdp = context.new_cdp_session(page); window = cdp.send('Browser.getWindowForTarget')['windowId']; baseline = page.evaluate('devicePixelRatio')
            for width, factor in ((1100,1),(1440,1.25),(1440,1.5)):
                cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': 1000}})
                set_browser_zoom(worker, page, factor); page.wait_for_function('dpr=>Math.abs(devicePixelRatio-dpr)<.001', arg=baseline*factor); settled(page)
                modal = panel(page); single.check_panel_layout(modal)
                capture_browser_view(cdp, output/f'dialog-{width}-{factor}.png')
                package(page, modal, f'desktop-{width}-{factor}', image=True)
                toggle = modal.get_by_role('checkbox', name='Include analysis metadata (ZIP)', exact=True)
                toggle.focus(); page.keyboard.press('Space'); expect(toggle).not_to_be_checked()
                single.download(page, modal.get_by_role('button', name='Download PNG', exact=True), output, f'file-only-{factor}', 'png')
                toggle.check(); close(page, modal)
                expect(page.get_by_role('button', name=f'Export {DERIVED} versus {xy.X} plot', exact=True).first).to_be_focused()
            assert dataset_hashes(dataset) == hashes
            unexpected = [error for error in errors if '400' not in error]
            assert not unexpected, unexpected; assert not writes, writes
            (output/'results.json').write_text(json.dumps({'packages': packages, 'downloads': downloads,
                'image_requests': len(image_requests), 'unexpected_errors': unexpected, 'unexpected_writes': writes,
                'unchanged_source_files_after_explicit_edit': sorted(hashes)}, indent=2), encoding='utf-8')
            print(f'PASS: nine slots, 1100px/125%/150%, keyboard metadata/file-only toggle, {len(packages)} packages, {len(hashes)} unchanged files after explicit fixture edit', flush=True)
        except Exception:
            page.screenshot(path=str(output/'failure.png'), full_page=True)
            (output/'failure.json').write_text(json.dumps({'errors': errors, 'body': page.locator('body').inner_text()}, indent=2), encoding='utf-8')
            raise
        finally: context.close(); request.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3150); parser.add_argument('--backend-port', type=int, default=8150)
    args = parser.parse_args(); output = ROOT/'data/verification/analysis-metadata'; output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kiha-analysis-metadata-') as directory:
        temporary = ROOT.__class__(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, dataset):
            run_checks(web, api, dataset, temporary, output)
    print('PASS: owned servers stopped; temporary fixtures/profile removed', flush=True)


if __name__ == '__main__': main()
