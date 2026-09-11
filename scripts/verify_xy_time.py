"""Isolated XY time-axis selection, source eligibility, persistence and exports.

Global Python runs Playwright; the owned backend uses its Python 3.13 venv.
Uses real uploaded measured/generated-time fixtures and a private browser profile.
"""
import argparse
import csv
import io
import json
from pathlib import Path
import re
import tempfile
import zipfile

from playwright.sync_api import expect, sync_playwright
from verify_data_quality import ROOT, servers, upload
from verify_filter_overlay import dataset_hashes
from verify_time_y_zoom import instrument
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout
from verify_plot_exports import download

A, B, C = 'xy_time_measured', 'xy_time_other', 'xy_time_generated'
TIMES = {A: 'clock', B: 'elapsed', C: 'generated_s'}


def fixtures(request):
    for name, tcol in TIMES.items():
        output = io.StringIO()
        writer = csv.writer(output)
        generated = name == C
        writer.writerow(([] if generated else [tcol]) + ['load_N', 'position_m'])
        for i in range(200):
            writer.writerow(([] if generated else [100 + i / 10]) + [i / 5, (i % 23) / 10])
        result = upload(request, name, output.getvalue(), time_mode='generated' if generated else 'column',
                        time_column=tcol, fs_hz=10)
        assert result['status'] == 'ready', result
        points = [{'id': 1, 'name': 'Later interval', 'start_s': 5, 'end_s': 10, 'start_idx': 50, 'end_idx': 100},
                  {'id': 2, 'name': 'Last interval', 'start_s': 12, 'end_s': 18, 'start_idx': 120, 'end_idx': 180}]
        response = request.put(f'tests/{name}/testpoints', data={'version': 1, 'test': name, 'test_points': points})
        assert response.ok, response.text()
        for col in ('load_N', 'position_m'):
            assert request.get(f'tests/{name}/tp_stats', params={'col': col}).ok


def run_checks(web, api, dataset, temporary, output):
    extension = temporary / 'extension'
    extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'XY time zoom verification',
        'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as p:
        request = p.request.new_context(base_url=api + '/')
        fixtures(request)
        before = dataset_hashes(dataset)
        catalog = request.get('analysis-sources').json()['sources']
        context = p.chromium.launch_persistent_context(str(temporary / 'profile'), channel='chromium', headless=True,
            no_viewport=True, args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]
        page.set_default_timeout(10000)
        errors, exports = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/src/utils/uplotSync.ts*', instrument)
        settings = {'scatterX': 'load_N', 'scatterY': 'position_m', 'clustering': False,
                    'gridColumns': ['load_N', 'position_m'], 'xyXCols': ['clock'], 'xyYCols': ['load_N']}
        session = {'version': 1, 'sources': catalog, 'currentTest': A, 'xAxis': 'load_N', 'yAxis': 'position_m',
            'axesUserSet': True, 'plotConfigs': ['load_N', 'position_m'], 'plotsUserEdited': True,
            'plotDensity': 'single', 'viewMode': 'xy', 'xySource': 'tp', 'scatterCollapsed': True,
            'selections': [{'test': test, 'tpId': tp, 'hidden': False} for test, tp in ((A, 1), (A, 2), (B, 1), (C, 1))]}
        page.add_init_script(f'''if(!sessionStorage.getItem('time-seeded')){{
          sessionStorage.setItem('time-seeded','1');
          localStorage.setItem('ptt.settings.v1',{json.dumps(json.dumps(settings))});
          localStorage.setItem('ptt.analysis-session.v1',{json.dumps(json.dumps(session))});}}''')
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp = context.new_cdp_session(page)

        def plot(): return page.locator('.analyze-plots-pane [role=group][aria-label$=" XY plot"]').first

        def settle():
            page.wait_for_load_state('networkidle')
            plot().locator('.uplot').wait_for()
            expect(plot()).not_to_contain_text('Loading paired samples')
            expect(plot()).not_to_contain_text('Updating XY view')
            wait_for_chart_layout(page)

        def choose(button, value, keyboard=False):
            button.focus()
            page.keyboard.press('Enter')
            search = page.locator('input[role="combobox"]:visible')
            if search.count():
                search.fill(value)
            option = page.get_by_role('option').filter(has=page.get_by_text(value, exact=True))
            expect(option).to_be_visible()
            if keyboard:
                page.keyboard.press('Enter')
            else:
                option.click()

        def axis(which, value, keyboard=False):
            choose(plot().get_by_role('button', name=f'{which} variable', exact=True), value, keyboard)
            settle()

        def data():
            return plot().locator('.uplot').evaluate('el=>{const u=el.__verificationPlot;return {axes:u.axes.map(a=>a.label),data:u.data,labels:u.series.slice(1).map(s=>s.label),range:[u.scales.x.min,u.scales.x.max,u.scales.y.min,u.scales.y.max]}}')

        def check(x, y, tests, full=False):
            actual = data()
            assert actual['axes'] == [x, y], actual['axes']
            assert len(actual['labels']) == len(tests), actual['labels']
            for facet, label, test in zip(actual['data'][1:], actual['labels'], tests):
                assert test in label, label
                tp = 2 if 'TP 2' in label else 1
                start, end = (0, 200) if full else (120, 180) if tp == 2 else (50, 100)
                expected = [i / 10 for i in range(start, end)]
                t_axis = 0 if x == TIMES[test] else 1
                assert len(facet[t_axis]) == len(expected)
                assert all(abs(a-b) < 1e-10 for a,b in zip(facet[t_axis], expected))
                if x == y: assert facet[0] == facet[1]

        def action(name):
            plot().get_by_role('button', name=re.compile('^Plot actions for ')).focus()
            page.keyboard.press('Enter')
            page.get_by_role('menuitem', name=name, exact=True).click()

        def export(tag, tcol, role, png=False):
            action('Export CSV / PNG…')
            dialog = page.get_by_role('dialog', name=re.compile('^Export '))
            for kind in (('CSV', 'PNG') if png else ('CSV',)):
                path, _ = download(page, dialog.get_by_role('button', name=f'Download {kind}', exact=True), output, tag + kind, 'zip')
                with zipfile.ZipFile(path) as archive:
                    assert 'analysis.json' in archive.namelist()
                    payload = archive.read(next(name for name in archive.namelist() if name.endswith('.' + kind.lower())))
                    if kind == 'CSV':
                        rows = list(csv.DictReader(io.StringIO(payload.decode('utf-8-sig'))))
                        assert rows and all(row['time_column'] == tcol for row in rows)
                        assert all(float(row[f'{tcol} [{role}]']) == float(row['time_s']) for row in rows)
                    else: assert payload.startswith(b'\x89PNG\r\n\x1a\n')
                    exports.append(path.name)
            page.keyboard.press('Escape')

        try:
            page.goto(web)
            settle()
            # Settings-only time defaults must seed before any explicit XY edits.
            check('clock', 'load_N', [A, A])
            export('tp-time-x-', 'clock', 'X', png=True)
            page.get_by_role('button', name='Edit plots', exact=True).click()
            axis('Y', 'clock', keyboard=True)
            check('clock', 'clock', [A, A])
            export('tp-time-both-', 'clock', 'X/Y')
            axis('X', 'load_N')
            check('load_N', 'clock', [A, A])
            export('tp-time-y-', 'clock', 'Y')
            # Actual time-column identity controls eligibility across tests.
            axis('Y', 'elapsed')
            check('load_N', 'elapsed', [B])
            axis('Y', 'generated_s')
            check('load_N', 'generated_s', [C])
            export('generated-time-', 'generated_s', 'Y')
            axis('Y', 'load_N')
            axis('X', 'clock')
            page.get_by_role('button', name='Editing plots', exact=True).click()
            page.reload()
            settle()
            check('clock', 'load_N', [A, A])

            source = page.get_by_role('group', name='Data source', exact=True)
            source.get_by_role('button', name='Full test', exact=True).click()
            settle()
            check('clock', 'load_N', [A], full=True)
            export('full-time-', 'clock', 'X', png=True)
            baseline = data()['range']
            over = plot().locator('.u-over')
            rect = over.bounding_box()
            page.mouse.move(rect['x'] + rect['width'] * .5, rect['y'] + rect['height'] * .5)
            page.mouse.wheel(0, -200)
            page.wait_for_timeout(250)
            assert data()['range'] != baseline
            export('cropped-time-', 'clock', 'X')
            before_pan = data()['range']
            page.keyboard.down('Shift')
            page.mouse.down()
            page.mouse.move(rect['x'] + rect['width'] * .6, rect['y'] + rect['height'] * .55, steps=6)
            page.mouse.up()
            page.keyboard.up('Shift')
            page.wait_for_timeout(250)
            assert data()['range'] != before_pan
            action('Reset axes')
            settle()
            assert data()['range'] == baseline
            for factor in (1, 1.25, 1.5):
                set_browser_zoom(worker, page, factor)
                settle()
                plot().get_by_role('button', name=re.compile('^Expand ')).click()
                settle()
                check('clock', 'load_N', [A], full=True)
                capture_browser_view(cdp, output / f'time-expanded-{factor}.png')
                plot().get_by_role('button', name=re.compile('^Minimize ')).click()
                settle()
            set_browser_zoom(worker, page, 1)
            window = cdp.send('Browser.getWindowForTarget')['windowId']
            cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': 1100, 'height': 900}})
            settle()
            choose(page.get_by_role('button', name='Active test', exact=True), B)
            replacement = page.get_by_role('combobox', name='X variable for plot 1', exact=True)
            expect(replacement).to_be_visible()
            expect(replacement.locator('option[value="elapsed"]')).to_have_count(1)
            replacement.select_option('elapsed')
            settle()
            check('elapsed', 'load_N', [B], full=True)
            capture_browser_view(cdp, output / 'other-time-restored-1100.png')

            # XY defaults include time; signal-only defaults still exclude it.
            page.get_by_role('button', name='Settings', exact=True).click()
            choose(page.get_by_role('button', name='Preferred Y signal for XY cell 1', exact=True), 'elapsed', True)
            choose(page.get_by_role('button', name='Preferred X signal for XY cell 1', exact=True), 'load_N', True)
            page.get_by_role('button', name='Save', exact=True).click()
            page.get_by_role('button', name='Analyze', exact=True).click()
            settle()
            check('load_N', 'elapsed', [B], full=True)
            # Other plot modes retain the signal grid even after XY Y edits.
            page.get_by_role('group', name='Plot mode', exact=True).get_by_role('button', name='Full test', exact=True).click()
            page.wait_for_load_state('networkidle')
            target = page.locator('.analyze-plots-pane [role=group][aria-label$="plot"]').first
            expect(target).to_contain_text('load_N')
            page.get_by_role('button', name='Edit plots', exact=True).click()
            target.get_by_role('button', name='Plot variable', exact=True).click()
            expect(page.get_by_role('option', name='elapsed', exact=True)).to_have_count(0)
            page.keyboard.press('Escape')
            assert before == dataset_hashes(dataset), 'Fixture data changed'
            assert not errors, errors
            (output / 'results.json').write_text(json.dumps({'exports': exports, 'unchanged_files': len(before), 'errors': errors}, indent=2))
            print(f'PASS: XY time X/Y/both, measured/generated/custom names, TP/Full, settings/session restore, exports, zoom/pan/maximize/resize/125%/150%; {len(exports)} exports; {len(before)} unchanged files', flush=True)
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            (output / 'failure.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
            raise
        finally:
            context.close()
            request.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3185)
    parser.add_argument('--backend-port', type=int, default=8185)
    args = parser.parse_args()
    output = ROOT / 'data/verification/xy-time'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kiha-xy-time-') as directory:
        temporary = Path(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, dataset):
            run_checks(web, api, dataset, temporary, output)
    print('PASS: isolated servers stopped and temporary fixtures/profile removed', flush=True)


if __name__ == '__main__':
    main()
