"""Read-only browser checks for retired time notes; uses existing demo sources."""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen
from zipfile import ZipFile

from playwright.sync_api import expect, sync_playwright

WEB = os.environ.get('PTT_WEB', 'http://127.0.0.1:8087/ptt/')
OUT = Path(os.environ.get('TEMP', '/tmp')) / 'ptt-time-notes-removal'
OUT.mkdir(exist_ok=True)


def download(page, button, filename):
    with page.expect_download() as event:
        button.click()
    path = OUT / filename
    event.value.save_as(path)
    return path


def check_png_zip(path):
    with ZipFile(path) as archive:
        metadata = archive.read('analysis.json').decode('utf-8')
        assert 'annotations' not in metadata, metadata
        assert 'Time annotations' not in metadata, metadata
        png = next(name for name in archive.namelist() if name.endswith('.png'))
        assert archive.read(png).startswith(b'\x89PNG\r\n\x1a\n')


def run():
    sources = json.load(urlopen(WEB + 'api/analysis-sources'))['sources']
    errors, retired_requests, writes = [], [], []
    checks = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for mode, legacy_visibility in [('tp', True), ('full', False)]:
            context = browser.new_context(viewport={'width': 1600, 'height': 1000}, reduced_motion='reduce')
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))

            def guard(route):
                request = route.request
                if '/annotations' in request.url:
                    retired_requests.append(request.url)
                    route.abort()
                elif request.method == 'POST' and urlsplit(request.url).path.endswith(('/export-progress', '/plot-image-export')):
                    route.continue_()
                elif request.method not in ['GET', 'HEAD', 'OPTIONS']:
                    writes.append(request.url)
                    route.abort()
                else:
                    route.continue_()

            page.route('**/api/**', guard)
            seed = {'version': 1, 'sources': sources, 'currentTest': 'ptt_demo_run_a',
                    'xAxis': 'rpm', 'yAxis': 'thrust_n', 'axesUserSet': True,
                    'selections': [{'test': 'ptt_demo_run_a', 'tpId': 1, 'hidden': False}],
                    'plotConfigs': ['thrust_n', 'torque_nm', 'rpm', 'electrical_power_w'],
                    'plotsUserEdited': True, 'plotDensity': 'quad', 'viewMode': mode,
                    'scatterCollapsed': True, 'annotationsVisible': legacy_visibility}
            page.add_init_script('if (!localStorage.getItem("ptt.analysis-session.v1")) '
                                 'localStorage.setItem("ptt.analysis-session.v1",'
                                 + json.dumps(json.dumps(seed)) + ')')
            page.goto(WEB)
            page.wait_for_load_state('networkidle')
            suffix = 'time plot' if mode == 'tp' else 'full test plot'
            plots = page.get_by_role('group', name=re.compile(' ' + suffix + '$'))
            expect(plots).to_have_count(4)
            plot = page.get_by_role('group', name='thrust_n ' + suffix, exact=True)
            expect(plot.locator('.u-over')).to_be_visible()
            expect(page.get_by_role('button', name=re.compile('time notes', re.I))).to_have_count(0)

            trigger = plot.get_by_role('button', name='Plot actions for thrust_n', exact=True)
            trigger.focus()
            page.keyboard.press('ArrowDown')
            menu = page.get_by_role('menu', name='Plot actions for thrust_n', exact=True)
            expect(menu).to_be_visible()
            assert not re.search(r'time notes|time marker|interval note', menu.inner_text(), re.I)
            expect(menu.get_by_role('menuitem', name='Filter settings\u2026')).to_be_visible()
            page.keyboard.press('Escape')
            expect(trigger).to_be_focused()
            plot.locator('.u-over').click(button='right', position={'x': 50, 'y': 50})
            expect(menu).to_be_visible()
            menu.get_by_role('menuitem', name='Export CSV / PNG\u2026').click()
            dialog = page.get_by_role('dialog', name='Export thrust_n plot', exact=True)
            expect(dialog.get_by_role('button', name='Download PNG', exact=True)).to_be_enabled()
            check_png_zip(download(page, dialog.get_by_role('button', name='Download PNG', exact=True), mode + '-plot.zip'))
            dialog.get_by_role('button', name='Close export', exact=True).click()

            page.get_by_role('button', name='Export selected plots', exact=True).click()
            multi = page.get_by_role('dialog', name='Export selected plots', exact=True)
            check_png_zip(download(page, multi.get_by_role('button', name='Download combined PNG', exact=True), mode + '-grid.zip'))
            multi.get_by_role('button', name='Close', exact=True).click()

            plot.get_by_role('button', name='Expand thrust_n', exact=True).click()
            expect(plots).to_have_count(1)
            plot.get_by_role('button', name='Minimize thrust_n', exact=True).click()
            expect(plots).to_have_count(4)
            page.set_viewport_size({'width': 1100, 'height': 900})
            expect(plot.locator('.u-over')).to_be_visible()
            page.screenshot(path=str(OUT / (mode + '-1100.png')))

            page.wait_for_function("mode=>JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).viewMode===mode", arg=mode)
            saved = page.evaluate("JSON.parse(localStorage.getItem('ptt.analysis-session.v1'))")
            assert 'annotationsVisible' not in saved
            assert saved['viewMode'] == mode
            saved['annotationsVisible'] = legacy_visibility
            page.evaluate("saved=>localStorage.setItem('ptt.analysis-session.v1',JSON.stringify(saved))", saved)
            page.reload()
            page.wait_for_load_state('networkidle')
            expect(plots).to_have_count(4)
            page.wait_for_function('!JSON.parse(localStorage.getItem("ptt.analysis-session.v1")).hasOwnProperty("annotationsVisible")')
            page.reload()
            page.wait_for_load_state('networkidle')
            expect(plots).to_have_count(4)
            checks.append(mode + ': menus, PNG/ZIP, maximize, resize, legacy browser state, reload')
            print('PASS:', checks[-1], flush=True)
            context.close()
        browser.close()
    assert not errors, errors
    assert not retired_requests, retired_requests
    assert not writes, writes
    (OUT / 'report.json').write_text(json.dumps({'checks': checks, 'page_errors': errors,
        'retired_requests': retired_requests, 'blocked_writes': writes}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    run()
