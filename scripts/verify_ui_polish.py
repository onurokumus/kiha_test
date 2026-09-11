"""Isolated browser checks for compact analysis controls and readable dialogs.

Uses real uploaded fixtures, an owned Python 3.13 backend, and a private
Chromium profile. All screenshots/results stay in ignored data/verification.
"""
import argparse
import json
import math
from pathlib import Path
import re
import tempfile

from playwright.sync_api import expect, sync_playwright

from verify_data_quality import ROOT, servers, upload
from verify_filter_overlay import dataset_hashes
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout
from verify_time_y_zoom import instrument

TEST = 'propeller_endurance_2026_09_11'
COLS = ['load_N', 'position_m', 'reference_N', 'phase_V']


def fixtures(request):
    lines = ['time,' + ','.join(COLS)]
    for index in range(400):
        lines.append(f'{index / 20},{10 + math.sin(index / 15)},'
                     f'{index / 100},{20 + math.cos(index / 23)},{math.cos(index / 11)}')
    assert upload(request, TEST, '\n'.join(lines))['status'] == 'ready'
    points = [{'id': 1, 'name': 'First interval', 'start_s': 2.5, 'end_s': 7.5,
               'start_idx': 50, 'end_idx': 150},
              {'id': 2, 'name': 'Second interval', 'start_s': 10, 'end_s': 17.5,
               'start_idx': 200, 'end_idx': 350}]
    response = request.put(f'tests/{TEST}/testpoints', data={
        'version': 1, 'test': TEST, 'test_points': points})
    assert response.ok, response.text()
    for col in COLS:
        assert request.get(f'tests/{TEST}/tp_stats', params={'col': col}).ok


def run_checks(web, api, dataset, temporary, output):
    extension = temporary / 'zoom-extension'
    extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3,
        'name': 'UI polish zoom verification', 'version': '1.0', 'permissions': ['tabs'],
        'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as p:
        request = p.request.new_context(base_url=api + '/')
        fixtures(request)
        before = dataset_hashes(dataset)
        catalog = request.get('analysis-sources').json()['sources']
        context = p.chromium.launch_persistent_context(str(temporary / 'profile'), channel='chromium',
            headless=True, no_viewport=True, args=[f'--disable-extensions-except={extension}',
                f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]
        page.set_default_timeout(10000)
        errors, metrics, writes = [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda req: writes.append(req.url)
                if '/api/' in req.url and req.method in ('PUT', 'PATCH', 'DELETE') else None)
        page.route('**/src/utils/uplotSync.ts*', instrument)
        settings = {'scatterX': 'load_N', 'scatterY': 'position_m', 'clustering': False,
                    'gridColumns': COLS, 'xyXCols': ['reference_N'], 'xyYCols': ['load_N']}
        session = {'version': 1, 'sources': catalog, 'currentTest': TEST, 'xAxis': 'load_N',
            'yAxis': 'position_m', 'axesUserSet': True, 'plotConfigs': COLS, 'plotsUserEdited': True,
            'plotDensity': 'quad', 'viewMode': 'tp', 'scatterCollapsed': True,
            'annotationsVisible': False,
            'selections': [{'test': TEST, 'tpId': tp, 'hidden': False} for tp in (1, 2)]}
        page.add_init_script(f'''if(!sessionStorage.getItem('ui-polish-seeded')){{
          sessionStorage.setItem('ui-polish-seeded','1');
          localStorage.setItem('ptt.settings.v1',{json.dumps(json.dumps(settings))});
          localStorage.setItem('ptt.analysis-session.v1',{json.dumps(json.dumps(session))});}}''')
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp = context.new_cdp_session(page)
        window = cdp.send('Browser.getWindowForTarget')['windowId']

        def plot():
            return page.locator('.analyze-plots-pane [role=group][aria-label$=" plot"]').first

        def settle():
            page.wait_for_load_state('networkidle')
            plot().locator('.uplot').wait_for()
            wait_for_chart_layout(page)

        def screenshot(name):
            # Wait for all visible grid slots, including siblings recreated by
            # maximize/restore. The first plot may settle before its neighbors.
            expect(page.get_by_text(re.compile(
                r'^(Applying filter|Loading paired samples|Updating XY view)$'))).to_have_count(0)
            capture_browser_view(cdp, output / name)

        def icon(name):
            button = page.get_by_role('button', name=name, exact=True)
            expect(button).to_be_visible()
            assert button.inner_text().strip() == '', f'{name} is not icon-only'
            assert button.locator('svg').count() == 1, f'{name} has no icon'
            assert button.bounding_box()['width'] >= 28
            return button

        def close_dialog(dialog):
            page.keyboard.press('Escape')
            expect(dialog).to_have_count(0)

        def filter_dialog(kind=None):
            opener = plot().get_by_role('button', name=re.compile('^Plot actions for '))
            opener.focus()
            page.keyboard.press('Enter')
            page.get_by_role('menuitem', name='Filter settings…', exact=True).click()
            dialog = page.get_by_role('dialog', name=re.compile('^Filter settings for '))
            expect(dialog).to_be_visible()
            if kind is not None:
                dialog.get_by_role('combobox').first.select_option(kind)
            return dialog, opener

        def filter_sizes(dialog, tag):
            actual = dialog.evaluate('''el => {
              const r=el.getBoundingClientRect();
              return {width:r.width, left:r.left, right:r.right, viewport:innerWidth,
                scroll:el.scrollWidth, client:el.clientWidth,
                fields:[...el.querySelectorAll('label')].map(label=>{
                  const field=label.querySelector('input,select'); if(!field)return null;
                  const q=field.getBoundingClientRect(),s=getComputedStyle(field);
                  return {label:label.innerText.split('\\n')[0],width:q.width,height:q.height,
                    font:parseFloat(s.fontSize),labelFont:parseFloat(getComputedStyle(label.querySelector('span')).fontSize),
                    left:q.left,right:q.right,border:s.borderTopWidth};}).filter(Boolean)};
            }''')
            assert actual['left'] >= 0 and actual['right'] <= actual['viewport'] + 1, actual
            assert actual['scroll'] <= actual['client'] + 1, actual
            assert actual['fields'], actual
            for field in actual['fields']:
                assert field['height'] >= 35 and field['width'] >= 139, (tag, field)
                assert field['font'] >= 13 and field['labelFont'] >= 12, (tag, field)
                assert field['left'] >= actual['left'] and field['right'] <= actual['right'], (tag, field)
                assert field['border'] != '0px', (tag, field)
            metrics.append({'tag': tag, **actual})

        def choose(button, value):
            button.focus()
            page.keyboard.press('Enter')
            page.locator('input[role="combobox"]:visible').fill(value)
            page.get_by_role('option').filter(has=page.get_by_text(value, exact=True)).click()
            settle()

        try:
            page.goto(web)
            settle()
            notes = icon('Show time notes')
            expect(notes).to_have_attribute('aria-pressed', 'false')
            expect(notes).to_have_attribute('data-tooltip', 'Show or hide time notes')
            notes.hover()
            expect(page.get_by_role('tooltip')).to_have_text('Show or hide time notes')
            notes.focus()
            page.keyboard.press('Enter')
            expect(notes).to_have_attribute('aria-pressed', 'true')
            expect(page.get_by_role('tooltip')).to_have_text('Show or hide time notes')
            page.mouse.move(5, 5)
            page.get_by_role('button', name='Analyze', exact=True).focus()
            expect(page.get_by_role('tooltip')).to_have_count(0)
            notes.hover()
            expect(page.get_by_role('tooltip')).to_have_text('Show or hide time notes')
            notes.focus()
            page.keyboard.press('Space')
            expect(notes).to_have_attribute('aria-pressed', 'false')
            page.mouse.move(5, 5)
            page.get_by_role('button', name='Analyze', exact=True).focus()
            expect(page.get_by_role('tooltip')).to_have_count(0)
            export = icon('Export selected plots')
            expect(export).to_have_attribute('data-tooltip', 'Export selected plots')
            export.hover()
            expect(page.get_by_role('tooltip')).to_have_text('Export selected plots')
            page.mouse.move(5, 5)
            expect(page.get_by_role('tooltip')).to_have_count(0)
            export.focus()
            page.keyboard.press('Enter')
            dialog = page.get_by_role('dialog', name='Export selected plots', exact=True)
            expect(dialog).to_be_visible()
            close_dialog(dialog)
            expect(export).to_be_focused()
            print('PASS: icon-only note/export controls, tooltips, keyboard toggles/dialog and focus return', flush=True)

            for kind in ('despike', 'bandpass', 'moving_avg', 'detrend', ''):
                dialog, opener = filter_dialog(kind)
                filter_sizes(dialog, 'tp-' + (kind or 'none'))
                if kind == 'despike':
                    screenshot('tp-despike.png')
                close_dialog(dialog)
                expect(opener).to_be_focused()
            print('PASS: all filter-field layouts readable, styled, contained, keyboard modal focus restored', flush=True)

            page.get_by_role('group', name='Plot mode', exact=True).get_by_role('button', name='Full test', exact=True).click()
            settle()
            for width, factor in ((1440, 1), (1100, 1), (1440, 1.25), (1440, 1.5)):
                cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': 1000}})
                set_browser_zoom(worker, page, factor)
                settle()
                active = page.get_by_role('button', name='Active test', exact=True)
                rect = active.bounding_box()
                assert rect['width'] >= 239, (width, factor, rect)
                assert active.evaluate('el=>el.getBoundingClientRect().right<=innerWidth'), rect
                expect(active).to_contain_text(TEST)
                metrics.append({'tag': f'active-test-{width}-{factor}', 'width': rect['width']})
                active.click()
                option = page.get_by_role('option').filter(has=page.get_by_text(TEST, exact=True))
                expect(option).to_be_visible()
                assert option.evaluate('el=>el.scrollWidth<=el.clientWidth+1')
                page.keyboard.press('Escape')
                expect(option).to_have_count(0)
                expect(active).to_be_focused()
                dialog, _ = filter_dialog('despike')
                filter_sizes(dialog, f'full-{width}-{factor}')
                screenshot(f'full-filter-{width}-{factor}.png')
                close_dialog(dialog)
                plot().get_by_role('button', name=re.compile('^Expand ')).click()
                settle()
                expect(icon('Export selected plots')).to_be_disabled()
                dialog, _ = filter_dialog()
                filter_sizes(dialog, f'expanded-{width}-{factor}')
                close_dialog(dialog)
                plot().get_by_role('button', name=re.compile('^Minimize ')).click()
                settle()
            print('PASS: wider active test, filter sizing, maximize/restore at 1100px and actual 125%/150% zoom', flush=True)

            set_browser_zoom(worker, page, 1)
            page.get_by_role('group', name='Plot mode', exact=True).get_by_role('button', name='XY', exact=True).click()
            page.get_by_role('button', name='Edit plots', exact=True).click()
            settle()
            for factor in (1, 1.5):
                set_browser_zoom(worker, page, factor)
                settle()
                x = plot().get_by_role('button', name='X variable', exact=True)
                y = plot().get_by_role('button', name='Y variable', exact=True)
                xr, yr = x.bounding_box(), y.bounding_box()
                assert xr['x'] + xr['width'] <= yr['x'], (xr, yr)
                assert abs(xr['y'] - yr['y']) <= 1, (xr, yr)
                if factor == 1:
                    choose(x, 'position_m')
                    choose(y, 'load_N')
                axes = plot().locator('.uplot').evaluate('el=>el.__verificationPlot.axes.map(axis=>axis.label)')
                assert axes == ['position_m', 'load_N'], axes
                screenshot(f'xy-order-{factor}.png')
                plot().get_by_role('button', name=re.compile('^Expand ')).click()
                settle()
                assert x.bounding_box()['x'] < y.bounding_box()['x']
                plot().get_by_role('button', name=re.compile('^Minimize ')).click()
                settle()
            print('PASS: XY X left/Y right, correct variable callbacks/axes, maximize/restore and 150% zoom', flush=True)
            assert before == dataset_hashes(dataset), 'Fixture source bytes changed'
            assert not errors, errors
            assert not writes, writes
            (output / 'results.json').write_text(json.dumps({'metrics': metrics,
                'unchanged_files': len(before), 'page_errors': errors, 'unexpected_writes': writes}, indent=2))
            print(f'PASS: {len(before)} source files unchanged; no page errors or source writes', flush=True)
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            (output / 'failure.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
            raise
        finally:
            context.close()
            request.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3191)
    parser.add_argument('--backend-port', type=int, default=8191)
    args = parser.parse_args()
    output = ROOT / 'data/verification/ui-polish'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kiha-ui-polish-') as directory:
        temporary = Path(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, dataset):
            run_checks(web, api, dataset, temporary, output)
    print('PASS: temporary fixtures/profile removed; both owned servers stopped', flush=True)


if __name__ == '__main__':
    main()
