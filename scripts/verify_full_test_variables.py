"""Native multi-variable Full test UI/CSV/PNG/session checks with isolated data.

Global Python drives Playwright; all native data operations use repository Python
3.13 via the existing temporary-server helper. No user source files are touched.
"""
import argparse
import csv
import io
import json
import math
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright
from verify_data_quality import ROOT, servers, upload
from verify_filter_overlay import dataset_hashes, FILTER
from verify_time_y_zoom import instrument
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout, check_crosshair

TEST = 'compare_fixture'
OTHER = 'partial_compare_fixture'
COLS = ['load_N', 'second_N', 'reference_N', 'phase_V', 'temperature_C', 'motor_A', 'spare_V']


def fixtures(request):
    lines = ['time,' + ','.join(COLS)]
    for i in range(32768):
        load = 500 if i in (400, 4000) else 10 + math.sin(i / 150)
        values = [load, load * 2, 20, math.cos(i / 100), 22 + math.sin(i / 900), 5 + math.cos(i / 800), 0]
        lines.append(f'{100+i/1000:.3f},' + ','.join(f'{value:.12g}' for value in values))
    assert upload(request, TEST, '\n'.join(lines))['status'] == 'ready'
    assert request.put(f'tests/{TEST}/testpoints', data={'version': 1, 'test': TEST, 'test_points': [
        {'id': 7, 'name': 'Comparison', 'start_s': 0, 'end_s': 32.768, 'start_idx': 0, 'end_idx': 32768}]}).ok
    for column in COLS:
        assert request.get(f'tests/{TEST}/tp_stats', params={'col': column}).ok
    assert upload(request, OTHER, 'time,load_N,phase_V\n' + '\n'.join(
        f'{i/1000:.3f},{math.sin(i/100):.12g},0' for i in range(32768)))['status'] == 'ready'


def run(web, api, dataset, temporary, output):
    extension = temporary / 'extension'
    extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Compare variables verification',
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
        context.route('**/src/utils/uplotSync.ts*', instrument)
        errors, checks, requests = [], [], []
        fail_filter = {'enabled': False}
        page.on('pageerror', lambda error: errors.append(str(error)))
        def data_route(route):
            if '/filter?' in route.request.url and fail_filter['enabled']:
                route.fulfill(status=503, json={'detail': 'Isolated filter failure'})
                return
            requests.append(parse_qs(urlparse(route.request.url).query))
            route.continue_()
        context.route(re.compile(r'/tests/[^/]+/(?:data|filter)\?'), data_route)
        session = {'version': 1, 'sources': sources, 'currentTest': TEST, 'xAxis': COLS[0], 'yAxis': COLS[1],
            'axesUserSet': True, 'selections': [{'test': TEST, 'tpId': 7, 'hidden': False}],
            'plotConfigs': COLS[:4], 'plotsUserEdited': True, 'plotDensity': 'quad',
            'viewMode': 'full', 'fullPlotMode': 'auto', 'scatterCollapsed': True}
        page.add_init_script(f'''if(!sessionStorage.getItem('compare-seeded')){{localStorage.clear();sessionStorage.setItem('compare-seeded','1');
            localStorage.setItem('ptt.analysis-session.v1',{json.dumps(json.dumps(session))});}}''')

        def plot():
            return page.locator('[role="group"][aria-label$=" full test plot"]').first
        def settled():
            page.wait_for_load_state('networkidle')
            expect(plot().locator('.uplot')).to_have_count(1)
            expect(plot()).not_to_contain_text('Applying filter')
            wait_for_chart_layout(page)
        def snapshot():
            return plot().locator('.uplot').evaluate('''el=>{const u=el.__verificationPlot;return {
                data:u.data, series:u.series.slice(1).map((s,i)=>({label:s.label,
                color:typeof s.stroke==='function'?s.stroke(u,i+1):s.stroke,dash:s.dash,width:s.width})),
                bands:u.bands.map(b=>b.series),x:[u.scales.x.min,u.scales.x.max]};}''')
        def add(column, keyboard=False):
            button = plot().get_by_role('button', name='Add variable to plot', exact=True)
            button.focus()
            page.keyboard.press('Enter')
            search = page.get_by_role('combobox', name='Search variables to add...', exact=True)
            search.fill(column)
            if keyboard:
                search.press('Enter')
            else:
                page.get_by_role('option', name=column, exact=True).click()
            settled()
        def legend():
            plot().get_by_role('button', name=re.compile('^Variable legend for')).click()
            panel = page.get_by_role('dialog', name='Plot variables and colors', exact=True)
            expect(panel).to_be_visible()
            return panel
        def remove(column):
            panel = legend()
            panel.get_by_role('button', name=f'Remove {column} from plot', exact=True).click()
            page.keyboard.press('Escape')
            settled()
        def menu(action):
            plot().get_by_role('button', name=re.compile('^Plot actions for')).focus()
            page.keyboard.press('Enter')
            page.get_by_role('menuitem', name=action, exact=True).click()
        def patch(changes):
            page.wait_for_timeout(400)
            page.evaluate('''changes=>{const s=JSON.parse(localStorage.getItem('ptt.analysis-session.v1'));
                Object.assign(s,changes);localStorage.setItem('ptt.analysis-session.v1',JSON.stringify(s));}''', changes)
            page.reload()
            settled()
        def passed(label):
            checks.append(label)
            print('PASS', label, flush=True)
        def header_layout():
            assert plot().evaluate('''el=>{const h=el.querySelector('[data-plot-header]'), c=h.querySelector('[data-full-test-variables]'),
                indicators=document.querySelectorAll('[data-plot-resolution]'), s=indicators[0],
                controls=document.querySelector('[aria-label="Analysis controls"]'), a=h.querySelector('[data-plot-toolbar]'),
                layout=controls.querySelector('[aria-label="Plot layout"]');
                if(indicators.length!==1 || !controls.contains(s)
                    || s.closest('[data-plot-header],.uplot,[role="group"][aria-label$=" full test plot"]'))return false;
                const cr=c.getBoundingClientRect(),sr=s.getBoundingClientRect(),ar=a.getBoundingClientRect(),
                    ctr=controls.getBoundingClientRect(),lr=layout.getBoundingClientRect();
                return sr.left>=ctr.left-1 && sr.right<=ctr.right+1 && sr.top>=ctr.top-1 && sr.bottom<=ctr.bottom+1
                    && sr.right<=lr.left+1 && sr.top<lr.bottom && sr.bottom>lr.top
                    && sr.width>0 && sr.height>0 && s.tabIndex===0
                    && (cr.right<=ar.left || cr.top>=ar.bottom || cr.bottom<=ar.top)
                    && el.scrollWidth<=el.clientWidth+1 && c.scrollWidth<=c.clientWidth+1;}'''), 'Overlapping controls or misplaced shared resolution'
        def export(fmt, tag):
            menu('Export CSV / PNG…')
            dialog = page.get_by_role('dialog', name=re.compile('^Export .* plot$'))
            with page.expect_download(timeout=60000) as download:
                dialog.get_by_role('button', name=f'Download {fmt}', exact=True).click()
            path = output / f'{tag}.zip'
            download.value.save_as(path)
            with zipfile.ZipFile(path) as archive:
                metadata = json.loads(archive.read('analysis.json'))
                for column in COLS[:3]:
                    assert column in json.dumps(metadata)
                name = next(name for name in archive.namelist() if name.endswith('.csv' if fmt == 'CSV' else '.png'))
                if fmt == 'CSV':
                    rows = list(csv.DictReader(io.StringIO(archive.read(name).decode())))
                    assert len(rows) == 32768, len(rows)
                    for column in COLS[:3]:
                        assert f'{column} [original]' in rows[0] and f'{column} [filtered]' in rows[0], rows[0].keys()
                    assert float(rows[400][f'{COLS[0]} [original]']) == 500
                    assert float(rows[400][f'{COLS[0]} [filtered]']) < 20
                    assert abs(float(rows[401][f'{COLS[1]} [original]']) - 2*float(rows[401][f'{COLS[0]} [original]'])) < 1e-8
                else:
                    (output / f'{tag}.png').write_bytes(archive.read(name))
            dialog.get_by_role('button', name='Close export').click()

        try:
            page.goto(web)
            settled()
            assert len(snapshot()['series']) == 2  # Legacy envelope remains one variable.
            page.get_by_role('button', name='Edit plots', exact=True).click()
            add(COLS[1], keyboard=True)
            add(COLS[2])
            actual = snapshot()
            assert len(actual['series']) == 6 and len(actual['bands']) == 3
            colors = [actual['series'][i]['color'] for i in (0, 2, 4)]
            assert len(set(colors)) == 3
            assert any(query.get('cols') == [','.join(COLS[:3])] for query in requests)
            panel = legend()
            for column in COLS[:3]:
                expect(panel).to_contain_text(column)
            page.keyboard.press('Escape')
            expect(plot().get_by_role('button', name=re.compile('^Variable legend for'))).to_be_focused()
            header_layout()
            page.screenshot(path=str(output / 'edit-three-variables.png'), full_page=True)
            passed('Legacy single variable; keyboard + search adds two; batched native envelope and compact color legend')

            for column in COLS[3:6]:
                add(column, keyboard=True)
            expect(plot().get_by_role('button', name='Add variable to plot', exact=True)).to_be_disabled()
            expect(plot().get_by_role('button', name=re.compile('^Variable legend for'))).to_be_focused()
            assert len(snapshot()['series']) == 12
            for column in COLS[3:6]:
                remove(column)
            assert [snapshot()['series'][i]['color'] for i in (0, 2, 4)] == colors
            panel = legend()
            page.keyboard.press('Tab')
            expect(panel.get_by_role('button', name=f'Remove {COLS[1]} from plot')).to_be_focused()
            page.keyboard.press('Shift+Tab')
            expect(panel).not_to_be_visible()
            expect(plot().get_by_role('button', name='Add variable to plot', exact=True)).to_be_focused()
            panel = legend()
            panel.get_by_role('button', name=f'Remove {COLS[2]} from plot').focus()
            page.keyboard.press('Tab')
            expect(panel).not_to_be_visible()
            expect(plot().get_by_role('button', name=re.compile('^Expand '))).to_be_focused()
            page.get_by_role('button', name='Editing plots', exact=True).click()
            passed('Six-variable bound, removal, stable colors, last-add focus and legend Tab/Shift+Tab boundaries')

            def choose_test(name):
                page.get_by_role('button', name='Active test', exact=True).click()
                page.get_by_role('option', name=re.compile('^' + re.escape(name) + r'\s')).click()
                settled()
            choose_test(OTHER)
            assert len(snapshot()['series']) == 2
            choose_test(TEST)
            assert len(snapshot()['series']) == 6
            assert [snapshot()['series'][i]['color'] for i in (0, 2, 4)] == colors
            passed('Temporary source with missing variables leaves comparison dormant and restores it on return')

            patch({'plotFilters': [FILTER], 'plotShowOriginal': [True]})
            expect(plot()).to_have_attribute('data-filter-display', 'overlay')
            actual = snapshot()
            assert len(actual['series']) == 12 and len(actual['bands']) == 6
            for i in range(6):
                original, filtered = actual['series'][i], actual['series'][i+6]
                assert original['color'][:7] == filtered['color'][:7]
                assert original['dash'] and not filtered['dash'] and original['width'] < filtered['width']
            panel = legend()
            expect(panel).to_contain_text('Filtered')
            expect(panel).to_contain_text('Original')
            page.screenshot(path=str(output / 'filtered-legend.png'), full_page=True)
            page.keyboard.press('Escape')
            export('CSV', 'three-both')
            export('PNG', 'three-both-image')
            passed('Same-hue filtered/original envelope pairs; real full-resolution CSV + PNG + all-variable metadata downloads')

            page.get_by_role('button', name='Sessions', exact=True).click()
            session_panel = page.get_by_role('dialog', name='Analysis sessions', exact=True)
            session_panel.get_by_label('Session name', exact=True).fill('Compare variables')
            with page.expect_download() as download:
                session_panel.get_by_role('button', name='Save session file', exact=True).click()
            session_path = output / 'comparison-session.json'
            download.value.save_as(session_path)
            saved = json.loads(session_path.read_text())['session']['fullPlotExtraColumns']
            assert saved[0] == COLS[1:3] and saved[1:] == [[]]*8, saved
            session_panel.get_by_role('button', name='Close', exact=True).click()
            page.get_by_role('button', name='Edit plots', exact=True).click()
            remove(COLS[1])
            page.get_by_role('button', name='Sessions', exact=True).click()
            session_panel.get_by_label('Session file', exact=True).set_input_files(session_path)
            expect(session_panel).to_contain_text('All saved source references are compatible.')
            session_panel.get_by_role('button', name='Open session', exact=True).click()
            settled()
            assert len(snapshot()['series']) == 12
            page.get_by_role('button', name='Test points', exact=True).click()
            page.wait_for_load_state('networkidle')
            expect(page.get_by_role('button', name='Add variable to plot', exact=True)).to_have_count(0)
            page.get_by_role('button', name='Full test', exact=True).click()
            settled()
            assert len(snapshot()['series']) == 12
            page.reload()
            settled()
            assert len(snapshot()['series']) == 12
            passed('Actual session Save/Open, independent slots, legacy defaults, mode switch retention and browser reload')

            page.get_by_role('button', name='Line', exact=True).click()
            settled()
            assert len(snapshot()['series']) == 6 and not snapshot()['bands']
            plot().get_by_role('button', name=re.compile('^Expand ')).click()
            settled()
            expect(plot().locator('.u-legend')).to_be_visible()
            assert plot().locator('.u-legend .u-series').count() == 7
            check_crosshair(page, 'expanded multi-variable')
            over = plot().locator('.u-over').bounding_box()
            page.mouse.move(over['x']+over['width']*.2, over['y']+over['height']*.4)
            page.mouse.down()
            page.mouse.move(over['x']+over['width']*.55, over['y']+over['height']*.6, steps=12)
            page.mouse.up()
            settled()
            zoomed = snapshot()['x']
            assert zoomed[1]-zoomed[0] < 20, zoomed
            page.mouse.move(over['x']+over['width']*.5, over['y']+over['height']*.5)
            page.mouse.wheel(0, -130)
            page.wait_for_timeout(600)
            settled()
            assert snapshot()['x'][1]-snapshot()['x'][0] < zoomed[1]-zoomed[0]
            page.get_by_role('button', name='Reset zoom', exact=True).click()
            settled()
            plot().get_by_role('button', name=re.compile('^Minimize ')).click()
            settled()
            passed('Line display, maximize/restore, aligned crosshair, drag and wheel time zoom, linked reset')

            fail_filter['enabled'] = True
            page.get_by_role('button', name='Min/max', exact=True).click()
            page.wait_for_load_state('networkidle')
            expect(plot()).to_have_attribute('data-filter-display', 'original')
            expect(plot()).to_contain_text('Showing original data')
            fail_filter['enabled'] = False
            plot().get_by_role('button', name='Retry', exact=True).click()
            settled()
            expect(plot()).to_have_attribute('data-filter-display', 'overlay')
            passed('Filter failure explicitly falls back to all originals; Retry restores all filtered traces')

            page.get_by_role('button', name='Export selected plots', exact=True).click()
            multi = page.get_by_role('dialog', name='Export selected plots', exact=True)
            with page.expect_download(timeout=60000) as download:
                multi.get_by_role('button', name='Download CSV ZIP', exact=True).click()
            path = output / 'selected-plots.zip'
            download.value.save_as(path)
            with zipfile.ZipFile(path) as archive:
                csvs = [name for name in archive.namelist() if name.endswith('.csv')]
                assert len(csvs) == 4, csvs
                assert sum(all(f'{column} [original]' in archive.read(name).decode().splitlines()[0] for column in COLS[:3]) for name in csvs) == 1
            multi.get_by_role('button', name='Close', exact=True).click()
            passed('Selected-plot CSV bundle keeps all variables together in their original slot')

            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            cdp = context.new_cdp_session(page)
            window = cdp.send('Browser.getWindowForTarget')['windowId']
            page.get_by_role('button', name='Edit plots', exact=True).click()
            for width, zoom in ((1100, 1), (1600, 1.25), (1600, 1.5)):
                cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': 1100}})
                set_browser_zoom(worker, page, zoom)
                settled()
                header_layout()
                legend()
                panel = page.get_by_role('dialog', name='Plot variables and colors', exact=True)
                assert panel.evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight}')
                capture_browser_view(cdp, output / f'desktop-{width}-{zoom}.png')
                page.keyboard.press('Escape')
            assert not errors, errors
            assert dataset_hashes(dataset) == before, 'Source dataset changed'
            passed('1100px desktop, actual 125%/150% zoom, unclipped dropdowns/resolution; unchanged sources; zero browser errors')
            (output / 'results.json').write_text(json.dumps({'checks': checks, 'source_files_unchanged': len(before), 'browser_errors': errors}, indent=2))
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
    parser.add_argument('--frontend-port', type=int, default=3353)
    parser.add_argument('--backend-port', type=int, default=8353)
    args = parser.parse_args()
    output = ROOT / 'data/verification/full-test-variables'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kiha-full-variables-') as directory:
        temporary = Path(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, dataset):
            run(web, api, dataset, temporary, output)
