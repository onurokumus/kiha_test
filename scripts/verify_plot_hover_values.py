"""Hover values across plot modes with copied demo data and private Chromium.

Owns hidden Vite/Python 3.13 child servers and a disposable browser profile.
Only GET and ephemeral PNG packaging/progress requests reach the isolated API.
Original datasets and copied source samples are fingerprinted before/after.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from verify_browser_zoom import set_browser_zoom, wait_for_chart_layout, capture_browser_view
from verify_time_y_zoom import instrument

ROOT = Path(__file__).resolve().parents[1]
KEY = 'ptt.analysis-session.v1'
TEST = 'ptt_demo_run_a'
PLOTS = '.analyze-plots-pane [role="group"][aria-label$=" plot"]'
COLS = ['thrust_n', 'torque_nm', 'rpm', 'current_a', 'voltage_v', 'shaft_power_w',
        'motor_temp_c', 'vibration_x_g', 'electrical_power_w']


def hashes(root, samples_only=False):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file() and
            (not samples_only or path.suffix in {'.parquet', '.csv'})}


@contextmanager
def servers(tmp, output, python, web_port, api_port):
    for port in (web_port, api_port):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', port))
    dataset = tmp / 'data'
    for test in (TEST, 'ptt_demo_run_b'):
        shutil.copytree(ROOT / 'data/tests' / test, dataset / 'tests' / test)
    web, api = f'http://127.0.0.1:{web_port}/', f'http://127.0.0.1:{api_port}/api/'
    children, logs = [], []
    try:
        configurations = [
            ('backend', [str(python), 'run.py'], ROOT / 'backend',
             {'KIHA_DATA_DIR': str(dataset), 'KIHA_PORT': str(api_port), 'KIHA_CORS_ORIGINS': web.rstrip('/')}),
            ('frontend', [shutil.which('node'), 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1',
                          '--port', str(web_port), '--strictPort'], ROOT / 'frontend',
             {'VITE_API_BASE': api.rstrip('/'), 'VITE_BASE_PATH': '/', 'BROWSER': 'none'}),
        ]
        for name, command, cwd, env in configurations:
            log = (output / f'{name}.log').open('w', encoding='utf-8'); logs.append(log)
            children.append(subprocess.Popen(command, cwd=cwd, env={**os.environ, **env}, stdout=log,
                stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
        for url in (api + 'tests', web):
            end = time.monotonic() + 30
            while True:
                try:
                    urlopen(url, timeout=1).close(); break
                except OSError:
                    assert time.monotonic() < end, f'Server startup failed: {url}; see {output}'
                    time.sleep(.15)
        yield web, api, dataset
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=10)
                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
        for log in logs: log.close()


def verify(web, api, dataset, tmp, output, waterfall_only=False):
    sources = json.load(urlopen(api + 'analysis-sources'))['sources']
    before = hashes(dataset, True)
    report = {'checks': [], 'pageErrors': [], 'blockedWrites': []}
    base = {'version': 1, 'sources': sources, 'currentTest': TEST, 'xAxis': 'rpm', 'yAxis': 'thrust_n',
        'axesUserSet': True, 'selections': [{'test': TEST, 'tpId': i, 'hidden': False} for i in (1, 3)],
        'plotConfigs': COLS, 'plotsUserEdited': True, 'plotDensity': 'quad', 'expandedPlot': None,
        'scatterCollapsed': True, 'viewMode': 'tp', 'fullPlotMode': 'line', 'specMode': 'fft',
        'specSource': 'tp', 'specLogY': False, 'xySource': 'tp', 'xyXCols': ['rpm'] * 9,
        'xyYCols': COLS, 'waterfallResolution': .5, 'waterfallBand': 'low'}
    extension = tmp / 'extension'; extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Hover value QA',
        'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')

    def passed(label):
        report['checks'].append(label); print('PASS:', label, flush=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(tmp / 'profile'), channel='chromium', headless=True,
            no_viewport=True, accept_downloads=True, args=[f'--disable-extensions-except={extension}',
            f'--load-extension={extension}', '--window-size=1500,1000'])
        page = context.pages[0]; page.set_default_timeout(20000)
        page.on('pageerror', lambda e: report['pageErrors'].append(str(e)))

        def guard(route):
            if route.request.method not in {'GET', 'HEAD', 'OPTIONS'} and not (
                route.request.method == 'POST' and route.request.url.split('?')[0].endswith(
                    ('/plot-image-export', '/export-progress'))):
                report['blockedWrites'].append(route.request.url); route.abort()
            else: route.continue_()

        def heatmap_instrument(route):
            response = route.fetch(); source = response.text(); needle = 'plot.current = u;'
            assert source.count(needle) == 1
            route.fulfill(response=response, body=source.replace(needle,
                needle + ' u.root.__verificationPlot = u; u.root.__hoverData = data;'))

        context.route('**/api/**', guard)
        context.route('**/src/utils/uplotSync.ts*', instrument)
        context.route('**/src/components/plots/WaterfallPlot.tsx*', heatmap_instrument)
        context.add_init_script(f"if(!sessionStorage.getItem('hover-seeded')){{sessionStorage.setItem('hover-seeded','1');localStorage.setItem('{KEY}',{json.dumps(json.dumps(base))});localStorage.setItem('ptt.theme.v1','light');localStorage.setItem('ptt.plot-values.v1','true');}}")
        page.goto(web); page.wait_for_load_state('networkidle')
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp = context.new_cdp_session(page)

        def seed(changes):
            page.mouse.move(0, 0)
            page.evaluate('(s)=>localStorage.setItem("ptt.analysis-session.v1",JSON.stringify(s))', {**base, **changes})
            page.reload(); page.wait_for_load_state('networkidle')
            expect(page.locator(PLOTS).first.locator('.uplot').first).to_be_visible()
            wait_for_chart_layout(page)

        def box(target=None):
            if target is None: return page.locator('.plot-hover-values')
            slot = target.evaluate('el=>el.closest("[data-plot-hover-slot]").dataset.plotHoverSlot')
            return page.locator(f'.plot-hover-values[data-plot-hover-slot="{slot}"]').first
        def plot(i=0): return page.locator(PLOTS).nth(i)

        def hover(target, fx=.53, fy=.41):
            over = target.locator('.u-over').first
            over.hover(); rect = over.bounding_box()
            visible = target.evaluate('el=>el.closest("[data-plot-hover-slot]").getBoundingClientRect()')
            top, bottom = max(rect['y'], visible['top']), min(rect['y'] + rect['height'], visible['bottom'])
            page.mouse.move(rect['x'] + rect['width'] * fx, top + (bottom - top) * fy)
            count = page.locator(PLOTS).locator('.uplot').evaluate_all('''els=>els.filter(el=>{
              const r=el.querySelector('.u-over').getBoundingClientRect(),s=el.closest('[data-plot-hover-slot]').getBoundingClientRect();
              return r.width>0&&r.height>0&&r.bottom>Math.max(0,s.top)&&r.top<Math.min(innerHeight,s.bottom)&&
                r.right>Math.max(0,s.left)&&r.left<Math.min(innerWidth,s.right);}).length''')
            choice = values.locator('span').last.inner_text().lower()
            if choice != 'all': count = 1 if choice == 'current' else 0
            try:
                expect(box()).to_have_count(count)
            except AssertionError:
                capture_browser_view(cdp, output / 'hover-failure.png')
                geometry = page.locator(PLOTS).locator('.uplot').evaluate_all('''els=>els.map(el=>{
                  const u=el.__verificationPlot,r=u.over.getBoundingClientRect();return {
                    width:r.width,height:r.height,cursor:{left:u.cursor.left,top:u.cursor.top},
                    slot:el.closest('[data-plot-hover-slot]').getBoundingClientRect()};})''')
                (output / 'hover-failure.json').write_text(json.dumps(geometry, indent=2), encoding='utf-8')
                raise
            if choice == 'none': return rect
            expect(box(target)).to_be_visible()
            assert box().evaluate_all('''els=>els.every(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.top>=0&&
                r.right<=innerWidth+.1&&r.bottom<=innerHeight+.1&&el.scrollWidth<=el.clientWidth+1})'''), box().all_inner_texts()
            return rect

        def numeric_rows(target, faceted):
            expected = target.locator('.uplot').first.evaluate('''(el,faceted)=>{const u=el.__verificationPlot;
              return u.series.slice(1).flatMap((s,j)=>{const i=j+1,index=u.cursor.idxs?.[i]??u.cursor.idx;
                if(s.show===false||index==null||index<0)return [];
                const xs=faceted?u.data[i][0]:u.data[0],ys=faceted?u.data[i][1]:u.data[i];
                if(!Number.isFinite(xs[index]))return [];
                return [[s.label,xs[index],ys[index]]];});}''', faceted)
            actual = box(target).locator('tbody tr').evaluate_all('''rows=>rows.map(r=>[
              r.querySelector('th').getAttribute('aria-label'),
              ...[...r.querySelectorAll('[data-hover-value]')].map(c=>c.textContent)])''')
            if not expected:
                assert actual and all(value == '—' for row in actual for value in row[1:]), actual
                return
            assert len(actual) == len(expected) and actual, (actual, expected)
            for row, wanted in zip(actual, expected):
                assert row[0] == wanted[0] or row[0] == re.sub(r' original(?= (?:max|min)$|$)', '', wanted[0]), (row, wanted)
                for rendered, value in zip(row[1:], wanted[1:]):
                    if value is None: assert rendered == '—'
                    else: assert math.isclose(float(rendered), value, rel_tol=6e-6, abs_tol=1e-12), (row, wanted)

        values = page.get_by_role('combobox', name='Plot values', exact=True)
        def expect_choice(choice):
            expect(values).to_contain_text(choice.capitalize())
        def choose_values(choice):
            values.click()
            page.get_by_role('listbox', name='Plot values', exact=True).get_by_role('option', name=choice.capitalize(), exact=True).click()
            expect_choice(choice)
            expect(values).to_have_attribute('aria-expanded', 'false')
            expect(values).to_be_focused()

        def keyboard_values(choice):
            values.focus(); page.keyboard.press('Enter')
            menu = page.get_by_role('listbox', name='Plot values', exact=True)
            expect(menu).to_be_focused()
            menu.press('Home' if choice == 'current' else 'End' if choice == 'none' else 'Home')
            if choice == 'all': menu.press('ArrowDown')
            expected_option = menu.get_by_role('option', name=choice.capitalize(), exact=True)
            expect(menu).to_have_attribute('aria-activedescendant', expected_option.get_attribute('id'))
            menu.press('Enter')
            expect_choice(choice)
            expect(values).to_have_attribute('aria-expanded', 'false')
            expect(values).to_be_focused()

        def inspect_values_menu(name=None):
            values.click()
            menu = page.get_by_role('listbox', name='Plot values', exact=True)
            expect(menu).to_be_visible()
            assert menu.get_by_role('option').all_inner_texts() == ['Current', 'All', 'None']
            expect(menu.get_by_role('option', name=values.locator('span').last.inner_text(), exact=True)).to_have_attribute('aria-selected', 'true')
            assert menu.locator('[aria-selected="true"] svg').count() == 1
            popup = menu.locator('..')
            popup.evaluate('el=>Promise.all(el.getAnimations().map(animation=>animation.finished))')
            assert popup.locator('input, [role="status"]').count() == 0
            assert values.evaluate('''el=>[el,...el.querySelectorAll('span')].every(
              node=>node.scrollWidth<=node.clientWidth+1)'''), values.inner_text()
            rect, trigger = popup.bounding_box(), values.bounding_box()
            assert 140 <= rect['width'] <= 184 and rect['height'] <= 124, rect
            assert rect['x'] >= 0 and rect['y'] >= 0
            viewport = page.evaluate('({width:innerWidth,height:innerHeight})')
            assert rect['x'] + rect['width'] <= viewport['width'] + .1
            assert rect['y'] + rect['height'] <= viewport['height'] + .1
            if name:
                x, y = min(rect['x'], trigger['x'])-5, min(rect['y'], trigger['y'])-5
                right = max(rect['x']+rect['width'], trigger['x']+trigger['width'])+5
                bottom = max(rect['y']+rect['height'], trigger['y']+trigger['height'])+5
                page.screenshot(path=str(output / name), clip={'x':x,'y':y,'width':right-x,'height':bottom-y})
            page.keyboard.press('Escape')
            expect(values).to_have_attribute('aria-expanded', 'false')
            expect(values).to_be_focused()

        expect_choice('all')
        if not waterfall_only:
            choose_values('current'); inspect_values_menu('values-dropdown-light.png')
            values.click(); page.mouse.click(4, 4)
            expect(values).to_have_attribute('aria-expanded', 'false'); expect_choice('current')
            values.focus(); page.keyboard.press('Space')
            expect(page.get_by_role('listbox', name='Plot values', exact=True)).to_be_visible()
            page.keyboard.press('Tab')
            expect(values).to_have_attribute('aria-expanded', 'false')
            expect(page.get_by_role('group', name='Plot layout', exact=True).get_by_role('button', name='1', exact=True)).to_be_focused()
            choose_values('all')
            passed('Styled Values menu has three checked choices, no clipped labels, compact sizing and Escape/outside/Tab focus behavior')

            seed({'plotConfigs': ['Test_ID'], 'selections': [{'test': 'ptt_demo_run_b', 'tpId': 4, 'hidden': False}]})
            hover(plot()); numeric_rows(plot(), True)
            assert 'ptt_demo_run_b' not in box(plot()).inner_text() and 'original' not in box(plot()).inner_text()
            assert box().locator('thead, strong').count() == 0
            dimensions = box(plot()).bounding_box()
            assert dimensions['width'] <= 190 and dimensions['height'] <= 34, dimensions
            box(plot()).screenshot(path=str(output / 'compact-test-id.png'))
            passed('Supplied Test_ID example uses one short TP/value row without heading or table headers')

            for name, changes, faceted in [
                ('Time', {}, True), ('Full line', {'viewMode': 'full'}, False),
                ('Full envelope', {'viewMode': 'full', 'fullPlotMode': 'envelope'}, False),
                ('FFT', {'viewMode': 'spectrum'}, True),
                ('PSD log', {'viewMode': 'spectrum', 'specMode': 'welch', 'specLogY': True}, True),
                ('Order', {'viewMode': 'spectrum', 'specXAxis': 'per_rev', 'specRpmCol': 'rpm'}, True),
                ('XY', {'viewMode': 'xy'}, True),
            ]:
                seed(changes)
                for i in range(4):
                    hover(plot(i))
                    for j in range(4): numeric_rows(plot(j), faceted)
                text = box(plot()).inner_text()
                if name == 'Full envelope': assert 'envelope' in box(plot()).get_attribute('aria-label') and 'max' in text and 'min' in text
                if name == 'PSD log': assert 'log10(U²/Hz)' in text
                if name == 'Order': assert 'ord' in text
                page.mouse.move(0, 0); expect(box()).to_have_count(0)
                passed(f'{name}: all four collapsed cards show actual plotted samples and dismiss on leave')

            seed({'viewMode': 'full', 'fullPlotExtraColumns': [['torque_nm', 'current_a']] + [[]] * 8})
            hover(plot()); numeric_rows(plot(), False)
            assert box(plot()).locator('tbody tr').count() == 3
            page.keyboard.press('Escape'); expect(box()).to_have_count(0)
            passed('Full-test variable comparisons retain all three values and Escape dismisses')

            seed({})
            hover(plot())
            assert page.locator('.plot-hover-values').count() == 4
            page.mouse.down(); expect(box()).to_have_count(0)
            rect = plot().locator('.u-over').bounding_box()
            page.mouse.move(rect['x'] + rect['width'] * .7, rect['y'] + rect['height'] * .65, steps=6)
            expect(box()).to_have_count(0); page.mouse.up(); page.mouse.move(0, 0)
            wait_for_chart_layout(page); hover(plot()); numeric_rows(plot(), True)
            plot().get_by_role('button', name=re.compile('^Expand ')).click()
            expect(box()).to_have_count(0); wait_for_chart_layout(page)
            expect(plot().locator('.u-legend')).to_be_visible()
            hover(plot()); numeric_rows(plot(), True)
            plot().get_by_role('button', name=re.compile('^Minimize ')).click()
            wait_for_chart_layout(page); hover(plot()); numeric_rows(plot(), True)
            page.keyboard.press('Escape'); expect(box()).to_have_count(0)
            passed('Linked cursors open all cards; drag zoom and maximize/restore preserve inspection')

            for mode in ('tp', 'full', 'spectrum', 'xy'):
                for density, count in (('single', 1), ('quad', 4), ('nine', 9)):
                    seed({'viewMode': mode, 'plotDensity': density})
                    for choice in ('all', 'current', 'none'):
                        choose_values(choice)
                        hover(plot(count-1))
                        if choice == 'all':
                            for i in range(count): numeric_rows(plot(i), mode != 'full')
                        elif choice == 'current':
                            numeric_rows(plot(count-1), mode != 'full')
                            hover(plot()); numeric_rows(plot(), mode != 'full')
                        else: expect(box()).to_have_count(0)
                    passed(f'{mode}: Current/All/None control values across {count} cards without stale peer readouts')
            choose_values('all')

            def snapshot():
                return page.locator(PLOTS).locator('.uplot').evaluate_all('''els=>els.map(el=>{
                  const u=el.__verificationPlot;el.__togglePlot=u;return {
                    x:[u.scales.x.min,u.scales.x.max],y:[u.scales.y.min,u.scales.y.max],data:JSON.stringify(u.data)};})''')
            before_toggle = snapshot()
            keyboard_values('none')
            expect_choice('none'); expect(box()).to_have_count(0)
            plot().locator('.u-over').hover(); expect(box()).to_have_count(0)
            assert page.locator(PLOTS).locator('.uplot').evaluate_all('els=>els.every(el=>el.__togglePlot===el.__verificationPlot)')
            assert snapshot() == before_toggle
            page.get_by_role('group', name='Plot layout', exact=True).get_by_role('button', name='4', exact=True).click()
            page.wait_for_load_state('networkidle'); wait_for_chart_layout(page)
            plot().locator('.u-over').hover(); expect(box()).to_have_count(0)
            page.reload(); page.wait_for_load_state('networkidle'); wait_for_chart_layout(page)
            expect_choice('none')
            keyboard_values('current')
            expect_choice('current')
            hover(plot(3)); numeric_rows(plot(3), True)
            page.reload(); page.wait_for_load_state('networkidle'); wait_for_chart_layout(page)
            expect_choice('current'); hover(plot()); numeric_rows(plot(), True)
            keyboard_values('all')
            expect_choice('all')
            hover(plot());
            for i in range(4): numeric_rows(plot(i), True)
            passed('Keyboard Values choices preserve plot instances/data/axes; None/Current survive layout changes/reload')

            for saved, expected_mode in (('false', 'none'), ('true', 'all'), ('bad-mode', 'all')):
                page.evaluate('(s)=>localStorage.setItem("ptt.plot-values.v1",s)', saved)
                page.reload(); page.wait_for_load_state('networkidle'); wait_for_chart_layout(page)
                expect_choice(expected_mode)
                hover(plot())
            passed('Legacy on/off preferences migrate to All/None; invalid values safely default to All')

            for theme in ('light', 'dark'):
                for factor in (1, 1.25, 1.5):
                    set_browser_zoom(worker, page, factor)
                    page.evaluate('(theme)=>{localStorage.setItem("ptt.theme.v1",theme);document.documentElement.dataset.theme=theme}', theme)
                    for mode in ('tp', 'full', 'spectrum', 'xy'):
                        seed({'viewMode': mode, 'plotDensity': 'nine'})
                        choose_values('current'); hover(plot(8), .95, .92); numeric_rows(plot(8), mode != 'full')
                        choose_values('all')
                        for i in (0, 8):
                            hover(plot(i), .95, .92)
                            for j in range(9): numeric_rows(plot(j), mode != 'full')
                    capture_browser_view(cdp, output / f'hover-{theme}-{factor}.png')
                    choose_values('current')
                    inspect_values_menu(f'values-dropdown-{theme}.png' if factor == 1 else None)
                    choose_values('all')
                    passed(f'Nine-slot {theme} cards at actual {factor*100:g}% browser zoom stay inside viewport')
            set_browser_zoom(worker, page, 1)
            cdp.send('Browser.setWindowBounds', {'windowId': cdp.send('Browser.getWindowForTarget')['windowId'],
                'bounds': {'width': 1000, 'height': 750}})
            seed({'viewMode': 'full', 'plotDensity': 'nine'})
            hover(plot(8), .96, .95); numeric_rows(plot(8), False)
            passed('Resized 1000×750 desktop window retains unclipped values in its final slot')

        def waterfall_rows(target):
            text = box(target).inner_text()
            assert all(label in text for label in ('Hz', 's', 'U')), text
            assert box(target).evaluate('el=>el.scrollHeight<=el.clientHeight+1'), text
            grid = target.locator('.uplot').first.evaluate('el=>el.__hoverData')
            cursor = target.locator('.uplot').first.evaluate('''el=>{const u=el.__verificationPlot;return
                [u.posToVal(u.cursor.left,'x'),u.posToVal(u.cursor.top,'y')]}'''.replace('return\n', 'return '))
            def cell(edges, value): return next(i for i in range(len(edges)-1) if edges[i] <= value <= edges[i+1])
            x, y = cell(grid['frequency_edges_hz'], cursor[0]), cell(grid['time_edges_s'], cursor[1])
            magnitude = box(target).locator('tbody tr').filter(has_text='U').locator('td').inner_text()
            assert math.isclose(float(magnitude.split()[0]), grid['magnitude'][y][x], rel_tol=6e-5)

        cdp.send('Browser.setWindowBounds', {'windowId': cdp.send('Browser.getWindowForTarget')['windowId'],
            'bounds': {'width': 1500, 'height': 1000}})
        for log in (False, True):
            for density, count in (('single', 1), ('quad', 4), ('nine', 9)):
                seed({'viewMode': 'spectrum', 'specMode': 'waterfall', 'specSource': 'full',
                      'specLogY': log, 'plotDensity': density})
                choose_values('none'); hover(plot(count-1)); expect(box()).to_have_count(0)
                choose_values('current'); hover(plot(count-1)); waterfall_rows(plot(count-1))
                choose_values('all'); hover(plot(count-1))
                for i in range(count): waterfall_rows(plot(i))
                page.mouse.move(0, 0); expect(box()).to_have_count(0)
                passed(f'Waterfall {"log" if log else "linear"}: Current/All/None work across {count} cards with accurate cell values')
        set_browser_zoom(worker, page, 1.5)
        seed({'viewMode': 'spectrum', 'specMode': 'waterfall', 'specSource': 'full', 'plotDensity': 'nine'})
        hover(plot(8))
        for i in range(9): waterfall_rows(plot(i))
        capture_browser_view(cdp, output / 'hover-waterfall-1.5.png')
        plot(8).get_by_role('button', name=re.compile('^Expand ')).click()
        wait_for_chart_layout(page); hover(plot()); waterfall_rows(plot())
        choose_values('none'); expect(box()).to_have_count(0)
        plot().locator('.u-over').hover(); expect(box()).to_have_count(0)
        choose_values('current'); hover(plot()); waterfall_rows(plot())
        choose_values('all'); hover(plot()); waterfall_rows(plot())
        passed('Nine Waterfall readouts at actual 150% zoom, maximized view and all Values choices remain complete')
        set_browser_zoom(worker, page, 1)

        for density, count in (('single', 1), ('quad', 4), ('nine', 9)):
            seed({'viewMode': 'spectrum', 'specMode': 'waterfall', 'specSource': 'tp', 'plotDensity': density})
            choose_values('current'); hover(plot(count-1), .53, .15); waterfall_rows(plot(count-1))
            choose_values('all')
            hover(plot(count-1), .53, .15)
            for i in range(count): waterfall_rows(plot(i))
            if density == 'nine':
                assert box().count() == 9, 'Scrolled-out source facets must not create overlapping readouts'
                capture_browser_view(cdp, output / 'hover-waterfall-selected-nine.png')
            passed(f'Selected-point Waterfall: {count} cards update visible source facets and suppress scrolled-out facets')

        seed({'viewMode': 'full'})
        hover(plot())
        canvas_before = plot().locator('canvas').first.evaluate('c=>c.toDataURL()')
        plot().get_by_role('button', name=re.compile('^Plot actions for ')).click()
        expect(box()).to_have_count(0)
        page.get_by_role('menuitem', name='Export CSV / PNG…', exact=True).click()
        dialog = page.get_by_role('dialog', name=re.compile('^Export '))
        dialog.get_by_role('checkbox', name='Include analysis metadata (ZIP)', exact=True).uncheck()
        with page.expect_download() as event: dialog.get_by_role('button', name='Download PNG', exact=True).click()
        event.value.save_as(output / 'hover-export.png')
        page.keyboard.press('Escape')
        assert plot().locator('canvas').first.evaluate('c=>c.toDataURL()') == canvas_before
        passed('Real PNG export dismisses hover and leaves the plotted canvas unchanged')
        assert not report['pageErrors'], report['pageErrors']
        assert not report['blockedWrites'], report['blockedWrites']
        context.close()
    assert hashes(dataset, True) == before, 'Copied source samples changed'
    report['copiedSourceSamplesUnchanged'] = True
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-python', type=Path, default=ROOT / 'backend/.venv/Scripts/python.exe')
    parser.add_argument('--web-port', type=int, default=3118)
    parser.add_argument('--api-port', type=int, default=8018)
    parser.add_argument('--waterfall-only', action='store_true', help='Run focused Waterfall layout/toggle/export regressions')
    parser.add_argument('--output', type=Path, default=Path(tempfile.gettempdir()) / 'ptt-plot-hover-values')
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    original = hashes(ROOT / 'data/tests')
    with tempfile.TemporaryDirectory(prefix='ptt-hover-values-') as directory:
        tmp = Path(directory)
        with servers(tmp, args.output, args.backend_python, args.web_port, args.api_port) as (web, api, dataset):
            verify(web, api, dataset, tmp, args.output, args.waterfall_only)
    assert hashes(ROOT / 'data/tests') == original, 'Original datasets changed'
    print('Original datasets unchanged; owned servers stopped. Report:', args.output, flush=True)


if __name__ == '__main__': main()
