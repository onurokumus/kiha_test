"""Focused native plot-appearance checks in private Chromium and copied fixtures.

Starts only owned hidden Vite/Python 3.13 servers. Reuses the existing demo-copy
server helper; user data is fingerprinted and API writes are blocked except PNG
packaging/progress. Inspection hooks affect only intercepted browser modules.
"""
import argparse
import json
import math
from pathlib import Path
import re
import tempfile
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout
from verify_plot_hover_values import COLS, KEY, PLOTS, ROOT, TEST, hashes, servers
from verify_time_y_zoom import instrument
from verify_scatter_exports import Fixture, open_dialog, png

APPEARANCE = 'ptt.plot-appearance.v1'


def verify(web, api, dataset, tmp, output, supplemental_only=False, inputs_only=False, compact_only=False):
    sources = json.load(urlopen(api + 'analysis-sources'))['sources']
    before = hashes(dataset, True)
    report = {'checks': [], 'pageErrors': [], 'blockedWrites': []}
    base = {'version': 1, 'sources': sources, 'currentTest': TEST, 'xAxis': 'rpm', 'yAxis': 'thrust_n',
        'axesUserSet': True, 'selections': [{'test': TEST, 'tpId': i, 'hidden': False} for i in (1, 3)],
        'plotConfigs': COLS, 'plotsUserEdited': True, 'plotDensity': 'single', 'expandedPlot': None,
        'scatterCollapsed': True, 'viewMode': 'tp', 'fullPlotMode': 'line', 'specMode': 'fft',
        'specSource': 'tp', 'specLogY': False, 'xySource': 'tp', 'xyXCols': ['rpm'] * 9, 'xyYCols': COLS}
    extension = tmp / 'extension'; extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Plot appearance QA',
        'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')

    def passed(label):
        report['checks'].append(label)
        print('PASS:', label, flush=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(tmp / 'profile'), channel='chromium', headless=True,
            no_viewport=True, accept_downloads=True, args=[f'--disable-extensions-except={extension}',
            f'--load-extension={extension}', '--window-size=1500,1000'])
        page = context.pages[0]; page.set_default_timeout(20000)
        page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))

        def guard(route):
            if route.request.method not in {'GET', 'HEAD', 'OPTIONS'} and not (
                route.request.method == 'POST' and route.request.url.split('?')[0].endswith(
                    ('/plot-image-export', '/export-progress'))):
                report['blockedWrites'].append(route.request.url); route.abort()
            else: route.continue_()

        def export_instrument(route):
            response = route.fetch(); source = response.text()
            needle = 'const source = plot.ctx.canvas;'
            assert source.count(needle) == 1, 'PNG capture inspection location changed'
            route.fulfill(response=response, body=source.replace(needle, needle + '''
              window.__appearanceExport = {widths:plot.series.slice(1).map(s=>s.width),
                sizes:plot.series.slice(1).map(s=>s.points?.size), canvas:source.toDataURL()};'''))

        def split_instrument(route):
            response = route.fetch(); source = response.text()
            needle = re.compile(r'plotRef\.current = (u\d*);')
            assert needle.search(source), 'Split inspection location changed'
            route.fulfill(response=response, body=needle.sub(
                lambda match: match[0] + f' {match[1]}.root.__verificationPlot = {match[1]};', source))

        context.route('**/api/**', guard)
        context.route('**/src/utils/uplotSync.ts*', instrument)
        context.route('**/src/utils/plotPngExport.ts*', export_instrument)
        context.route('**/src/components/split/SplitPlot.tsx*', split_instrument)
        context.add_init_script(f"if(!sessionStorage.getItem('appearance-seeded')){{sessionStorage.setItem('appearance-seeded','1');localStorage.setItem('{KEY}',{json.dumps(json.dumps(base))});localStorage.setItem('ptt.theme.v1','light');localStorage.removeItem('{APPEARANCE}');}}")
        cdp = context.new_cdp_session(page)
        try:
            page.goto(web); page.wait_for_load_state('networkidle')
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')

            def plot(index=0): return page.locator(PLOTS).nth(index)
            def settle():
                page.wait_for_load_state('networkidle')
                expect(plot().locator('.uplot').first).to_be_visible()
                page.wait_for_function('''selector=>[...document.querySelectorAll(selector)].every(el=>
                  !el.getClientRects().length || (el.querySelector('.uplot') &&
                  !/Loading paired samples|Loading flights|Updating XY view|Updating flight comparison/.test(el.innerText)))''', arg=PLOTS)
                wait_for_chart_layout(page)

            def seed(changes, appearance=None):
                page.evaluate('(s)=>localStorage.setItem("ptt.analysis-session.v1",JSON.stringify(s))', {**base, **changes})
                page.evaluate('a=>localStorage.setItem("ptt.plot-appearance.v1",JSON.stringify(a))',
                              appearance or {'lineScale': 1, 'pointScale': 1})
                page.reload(); settle()

            options = page.get_by_role('button', name='Options', exact=True)
            panel = page.get_by_role('region', name='Analysis options', exact=True)
            line = page.get_by_role('textbox', name='Line thickness', exact=True)
            points = page.get_by_role('textbox', name='Scatter size', exact=True)

            def open_options():
                expect(options).to_be_enabled()
                if options.get_attribute('aria-expanded') != 'true': options.click()
                expect(panel).to_be_visible()
                expect(line).to_be_visible(); expect(points).to_be_visible()

            def change(label, percentage):
                open_options()
                control = page.get_by_role('textbox', name=label, exact=True)
                control.fill(str(percentage))
                expect(control).to_have_value(str(percentage))
                page.keyboard.press('Escape')
                expect(panel).not_to_be_visible(); expect(options).to_be_focused()
                wait_for_chart_layout(page)

            def styles():
                return plot().locator('.uplot').first.evaluate('''el=>{
                  const u=el.__verificationPlot;return {widths:u.series.slice(1).map(s=>s.width),
                    sizes:u.series.slice(1).map(s=>s.points?.size)};}''')

            def remember():
                page.locator(PLOTS).locator('.uplot').evaluate_all('''els=>{
                  window.__appearanceRemembered=els.map(el=>{const u=el.__verificationPlot;return {
                    element:el,u,data:u.data,json:JSON.stringify(u.data),
                    scales:JSON.stringify(Object.fromEntries(Object.entries(u.scales).map(([k,s])=>[k,[s.min,s.max]]))),
                    visibility:u.series.map(s=>s.show)};});}''')

            def retained():
                result = page.evaluate('''()=>window.__appearanceRemembered.map(before=>{
                  const u=before.element.__verificationPlot;return {
                    instance:before.element.isConnected && u===before.u,
                    data:u.data===before.data && JSON.stringify(u.data)===before.json,
                    scales:JSON.stringify(Object.fromEntries(Object.entries(u.scales).map(([k,s])=>[k,[s.min,s.max]])))===before.scales,
                    visibility:JSON.stringify(u.series.map(s=>s.show))===JSON.stringify(before.visibility)};})''')
                assert result and all(all(item.values()) for item in result), result

            def ratio(actual, original, factor):
                assert len(actual) == len(original) and actual, (actual, original)
                assert any(value and value > 0 for value in original), original
                for value, previous in zip(actual, original):
                    assert math.isclose(value, previous * factor, rel_tol=1e-8, abs_tol=1e-8), (actual, original, factor)

            def reset():
                open_options()
                panel.get_by_role('button', name='Reset appearance', exact=True).click()
                expect(line).to_have_value('100'); expect(points).to_have_value('100')
                page.keyboard.press('Escape'); wait_for_chart_layout(page)

            def input_checks():
                settle(); baseline = styles(); remember()
                options.focus(); page.keyboard.press('Enter')
                expect(panel).to_be_visible(); expect(panel.get_by_role('slider')).to_have_count(0)
                for control in (line, points):
                    expect(control).to_have_attribute('type', 'text')
                    expect(control).to_have_attribute('inputmode', 'decimal')
                    expect(control).to_have_value('100')
                page.keyboard.press('Tab'); expect(line).to_be_focused()
                line.press('ControlOrMeta+A'); line.press_sequentially('137.5')
                expect(line).to_have_value('137.5'); expect(line).to_be_focused()
                ratio(styles()['widths'], baseline['widths'], 1.375); retained()
                page.keyboard.press('Tab'); expect(points).to_be_focused()
                points.press('ControlOrMeta+A'); points.press_sequentially('82')
                expect(points).to_have_value('82'); expect(points).to_be_focused()
                assert page.evaluate('JSON.parse(localStorage.getItem("ptt.plot-appearance.v1"))') == {'lineScale': 1.375, 'pointScale': .82}
                retained()
                passed('Text percentage inputs support keyboard selection and arbitrary decimal values; thickness applies immediately without resetting plots')

                applied = styles()
                for control, valid in ((line, '137.5'), (points, '82')):
                    for invalid in ('', 'text', '49.9', '300.1', '-', 'Infinity'):
                        control.fill(invalid)
                        expect(control).to_have_value(invalid)
                        expect(control).to_have_attribute('aria-invalid', 'true')
                        described = control.get_attribute('aria-describedby').split()
                        warnings = [page.locator(f'[id="{identifier}"]') for identifier in described if identifier.endswith('-warning')]
                        assert len(warnings) == 1 and warnings[0].is_visible() and warnings[0].inner_text().strip(), described
                        assert styles() == applied; retained()
                        assert page.evaluate('JSON.parse(localStorage.getItem("ptt.plot-appearance.v1"))') == {'lineScale': 1.375, 'pointScale': .82}
                        control.press('Tab')
                        open_options()
                        expect(control).to_have_value(valid)
                        expect(control).not_to_have_attribute('aria-invalid', 'true')
                line.fill('146.25'); line.press('Enter')
                expect(line).to_have_value('146.25')
                ratio(styles()['widths'], baseline['widths'], 1.4625); retained()
                page.keyboard.press('Escape'); expect(options).to_be_focused()
                passed('Empty, nonnumeric and out-of-range drafts show inline validation, retain applied geometry/storage, and restore on blur; Enter accepts valid decimals')

                seed({'viewMode': 'xy', 'plotDensity': 'quad'})
                baseline_xy = styles(); remember()
                open_options(); points.fill('82')
                ratio(styles()['sizes'], baseline_xy['sizes'], .82); retained()
                line.fill('137.5'); ratio(styles()['sizes'], baseline_xy['sizes'], .82); retained()
                page.keyboard.press('Escape')
                passed('Typing 82% updates actual XY point diameters in place across four cards independently of 137.5% line thickness')

                page.reload(); settle(); open_options()
                expect(line).to_have_value('137.5'); expect(points).to_have_value('82')
                points.fill(''); expect(points).to_have_attribute('aria-invalid', 'true')
                page.keyboard.press('Escape'); expect(panel).not_to_be_visible(); expect(options).to_be_focused()
                open_options(); expect(points).to_have_value('82')
                reset(); page.reload(); settle(); open_options()
                expect(line).to_have_value('100'); expect(points).to_have_value('100')
                expect(panel.get_by_role('button', name='Reset appearance', exact=True)).to_be_disabled()
                page.keyboard.press('Escape')
                passed('Exact decimal percentages survive reload; dismissed invalid drafts revert; Reset appearance restores and persists 100%')

                window_id = cdp.send('Browser.getWindowForTarget')['windowId']
                for theme in ('light', 'dark'):
                    page.evaluate('theme=>localStorage.setItem("ptt.theme.v1",theme)', theme)
                    cdp.send('Browser.setWindowBounds', {'windowId': window_id, 'bounds': {'width': 1050, 'height': 1000}})
                    set_browser_zoom(worker, page, 1.5)
                    seed({'viewMode': 'xy', 'plotDensity': 'quad'}, {'lineScale': 1.375, 'pointScale': .82})
                    open_options()
                    assert panel.evaluate('''el=>{const r=el.getBoundingClientRect();return (
                      r.left>=0&&r.top>=0&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1&&el.scrollWidth<=el.clientWidth+1);}''')
                    expect(line).to_have_value('137.5'); expect(points).to_have_value('82')
                    capture_browser_view(cdp, output / f'inputs-{theme}-1050-1.5.png')
                    line.fill('301'); expect(line).to_have_attribute('aria-invalid', 'true')
                    assert panel.evaluate('el=>el.scrollWidth<=el.clientWidth+1')
                    capture_browser_view(cdp, output / f'inputs-invalid-{theme}-1050-1.5.png')
                    line.press('Tab'); expect(line).to_have_value('137.5')
                    page.keyboard.press('Escape')
                    plot().get_by_role('button', name=re.compile('^Expand ')).click(); settle()
                    change('Scatter size', 82.5)
                    plot().get_by_role('button', name=re.compile('^Minimize ')).click(); settle()
                    passed(f'{theme} compact 1050px desktop at actual 150% zoom: text fields/validation remain in bounds and work maximized/restored')

            def compact_checks():
                appearance = page.get_by_role('region', name='Plot appearance', exact=True)
                reset_button = panel.get_by_role('button', name='Reset appearance', exact=True)
                window_id = cdp.send('Browser.getWindowForTarget')['windowId']

                def in_bounds():
                    assert panel.evaluate('''el=>{const r=el.getBoundingClientRect();return (
                      r.left>=0&&r.top>=0&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1&&el.scrollWidth<=el.clientWidth+1);}''')
                    assert appearance.evaluate('el=>el.scrollWidth<=el.clientWidth+1')

                for theme in ('light', 'dark'):
                    for zoom in (1, 1.5):
                        page.evaluate('theme=>localStorage.setItem("ptt.theme.v1",theme)', theme)
                        cdp.send('Browser.setWindowBounds', {'windowId': window_id, 'bounds': {'width': 1050, 'height': 1000}})
                        set_browser_zoom(worker, page, zoom)
                        view = 'tp' if zoom == 1 else 'xy'
                        seed({'viewMode': view, 'plotDensity': 'quad'})
                        options.focus(); page.keyboard.press('Enter'); expect(panel).to_be_visible()
                        in_bounds()
                        dimensions = panel.bounding_box()
                        assert abs(dimensions['width'] - 360) <= 1, dimensions
                        assert dimensions['height'] <= 120, dimensions
                        content = appearance.inner_text()
                        assert 'Line' in content and 'Scatter' in content, content
                        assert all(text not in content for text in ('Appearance', 'Shared across', 'default sizes',
                            'reference curves', 'overview markers', 'Reset appearance')), content
                        assert reset_button.inner_text().strip() == ''
                        fields = [line.bounding_box(), points.bounding_box(), reset_button.bounding_box()]
                        assert max(field['y'] for field in fields) - min(field['y'] for field in fields) <= 8, fields
                        page.keyboard.press('Tab'); expect(line).to_be_focused()
                        line.fill('137.5'); page.keyboard.press('Tab'); expect(points).to_be_focused()
                        points.fill('82'); page.keyboard.press('Tab'); expect(reset_button).to_be_focused()
                        page.keyboard.press('Enter')
                        expect(line).to_have_value('100'); expect(points).to_have_value('100')
                        expect(reset_button).to_be_disabled()
                        line.fill('301'); expect(line).to_have_attribute('aria-invalid', 'true'); in_bounds()
                        capture_browser_view(cdp, output / f'compact-invalid-{theme}-{zoom}.png')
                        line.press('Tab'); expect(line).to_have_value('100')
                        capture_browser_view(cdp, output / f'compact-{theme}-{zoom}.png')
                        page.keyboard.press('Escape'); expect(options).to_be_focused()
                        passed(f'{theme} {view} compact Options at {zoom * 100:g}% zoom: 360px inline controls, keyboard reset and unclipped validation')

                for mode in ('full', 'spectrum'):
                    seed({'viewMode': mode, 'plotDensity': 'quad'})
                    open_options(); in_bounds()
                    assert abs(panel.bounding_box()['width'] - 520) <= 1, panel.bounding_box()
                    if mode == 'full':
                        group = panel.get_by_role('group', name='Full-test trace style', exact=True)
                        expect(group).to_be_visible()
                        assert group.locator('button').count() == 3
                        assert group.evaluate('el=>el.scrollWidth<=el.clientWidth+1')
                        group.get_by_role('button', name='Min/max', exact=True).click()
                        expect(group.get_by_role('button', name='Min/max', exact=True)).to_have_attribute('aria-pressed', 'true')
                    else:
                        group = panel.get_by_role('group', name='Spectrum x-axis', exact=True)
                        expect(group).to_be_visible()
                        expect(group.get_by_role('button', name='Hz', exact=True)).to_be_visible()
                        expect(group.get_by_role('button', name='Per rev', exact=True)).to_be_visible()
                        log = panel.get_by_role('button', name='Log scale', exact=True)
                        log.click(); expect(log).to_have_attribute('aria-pressed', 'true')
                    line.fill('137.5'); expect(line).to_have_value('137.5'); in_bounds()
                    capture_browser_view(cdp, output / f'compact-{mode}-dark-1.5.png')
                    page.keyboard.press('Escape')
                    passed(f'{mode} retains its existing controls and 520px panel alongside compact appearance fields at 150% zoom')

            def supplemental():
                comparison = {'flights': [
                    {'test': TEST, 'color': '#d55e00', 'hidden': False, 'offset': 0},
                    {'test': 'ptt_demo_run_b', 'color': '#cc79a7', 'hidden': False, 'offset': 1}],
                    'timeBasis': 'elapsed'}
                seed({'viewMode': 'full', 'fullFlightComparison': comparison,
                      'fullPlotExtraColumns': [['torque_nm']] + [[]] * 8})
                baseline = styles(); assert len(baseline['widths']) == 4, baseline
                remember(); change('Line thickness', 200)
                ratio(styles()['widths'], baseline['widths'], 2); retained()
                passed('Multi-flight Time preserves four native comparison traces and their axes while line widths scale in place')

                seed({'viewMode': 'xy'}, {'lineScale': 1, 'pointScale': 2})
                open_options(); expect(panel.locator('[class*="optionsSummary"]')).to_be_empty()
                page.keyboard.press('Escape')
                before_export = styles(); canvas = plot().locator('canvas').first.evaluate('c=>c.toDataURL()')
                plot().get_by_role('button', name=re.compile('^Plot actions for ')).click()
                page.get_by_role('menuitem', name='Export CSV / PNG…', exact=True).click()
                dialog = page.get_by_role('dialog', name=re.compile('^Export '))
                dialog.get_by_role('checkbox', name='Include analysis metadata (ZIP)', exact=True).uncheck()
                with page.expect_download() as event: dialog.get_by_role('button', name='Download PNG', exact=True).click()
                event.value.save_as(output / 'appearance-xy-export.png'); page.keyboard.press('Escape')
                export = page.evaluate('window.__appearanceExport')
                assert export['sizes'] == before_export['sizes'] and export['canvas'] == canvas
                assert plot().locator('canvas').first.evaluate('c=>c.toDataURL()') == canvas
                passed('XY PNG capture includes the actual scaled point diameters and identical plotted pixels')

                seed({'scatterCollapsed': False})
                dots = page.locator('.recharts-scatter-symbol circle[fill]:not([fill="none"])')
                expect(dots.first).to_be_visible()
                dots.first.hover(); page.mouse.move(0, 0)
                page.wait_for_timeout(250)
                for percentage in (300, 50, 100):
                    change('Scatter size', percentage)
                    page.wait_for_timeout(250)
                    actual = dots.evaluate_all('els=>els.map(el=>({attribute:Number(el.getAttribute("r")),animated:el.r.animVal.value}))')
                    assert all(math.isclose(v['attribute'], v['animated'], rel_tol=1e-6) for v in actual), actual
                    assert set(v['attribute'] for v in actual) == {6 * percentage / 100, 8 * percentage / 100}, actual
                passed('Overview markers retain the requested visible SVG radius after prior hover animation and size changes')

                seed({}, {'lineScale': 2, 'pointScale': 1})
                page.get_by_role('button', name='Split', exact=True).click()
                page.wait_for_load_state('networkidle')
                split = page.locator('[data-split-plot] .uplot').first
                expect(split).to_be_visible(); wait_for_chart_layout(page)
                widths = split.evaluate('el=>el.__verificationPlot.series.slice(1).map(s=>s.width)')
                assert widths and all(width > 0 for width in widths), widths
                page.get_by_role('button', name='Analyze', exact=True).click(); settle()
                change('Line thickness', 100)
                page.get_by_role('button', name='Split', exact=True).click()
                page.wait_for_load_state('networkidle'); expect(split).to_be_visible(); wait_for_chart_layout(page)
                baseline = split.evaluate('el=>el.__verificationPlot.series.slice(1).map(s=>s.width)')
                ratio(widths, baseline, 2)
                passed('Split previews apply the shared line preference on entry and reflect subsequent analysis-control changes')

                # Existing controlled scatter fixture supplies overlapping points
                # (a cluster) and a reference curve without modifying native data.
                mock_page = context.new_page(); fixture = Fixture()
                mock_page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))
                mock_page.add_init_script('localStorage.removeItem("ptt.analysis-session.v1");')
                fixture.configure(mock_page)
                mock_page.add_init_script('''{
                  const s=JSON.parse(localStorage.getItem('ptt.analysis-session.v1'));
                  s.datasheetVisible=true;localStorage.setItem('ptt.analysis-session.v1',JSON.stringify(s));
                  localStorage.setItem('ptt.plot-appearance.v1',JSON.stringify({lineScale:1,pointScale:1}));
                }''')
                mock_page.goto(web); mock_page.wait_for_load_state('networkidle')
                cluster = mock_page.locator('[data-scatter-hover-target="cluster"] circle')
                reference = mock_page.locator('.datasheet-scatter-series circle')
                expect(cluster.first).to_be_visible(); expect(reference.first).to_be_visible()
                baseline_cluster = cluster.evaluate_all('els=>els.map(el=>Number(el.getAttribute("data-export-radius")))')
                baseline_reference = reference.evaluate_all('els=>els.map(el=>Number(el.getAttribute("r")))')
                counts = mock_page.locator('[data-scatter-hover-target="cluster"] text').all_text_contents()
                mock_page.get_by_role('button', name='Options', exact=True).click()
                mock_points = mock_page.get_by_role('textbox', name='Scatter size', exact=True)
                mock_points.fill('200')
                mock_page.keyboard.press('Escape'); mock_page.wait_for_timeout(250)
                ratio(cluster.evaluate_all('els=>els.map(el=>Number(el.getAttribute("data-export-radius")))'), baseline_cluster, 2)
                ratio(reference.evaluate_all('els=>els.map(el=>el.r.animVal.value)'), baseline_reference, 2)
                assert mock_page.locator('[data-scatter-hover-target="cluster"] text').all_text_contents() == counts
                dialog = open_dialog(mock_page)
                png_result = png(mock_page, dialog, output, 'appearance-overview-export', datasheet=True)
                svg = mock_page.evaluate('window.__scatterSvgs[0]')
                assert 'r="7"' in svg and 'r="16"' in svg, svg
                report['overviewPNG'] = png_result
                assert not fixture.blocked and not fixture.errors, (fixture.blocked, fixture.errors)
                mock_page.close()
                passed('Cluster count/membership and reference samples survive marker resizing; overview PNG retains selected/reference marker sizes')

            if supplemental_only or inputs_only or compact_only:
                if compact_only: compact_checks()
                elif inputs_only: input_checks()
                else: supplemental()
                assert not report['pageErrors'], report['pageErrors']
                assert not report['blockedWrites'], report['blockedWrites']
                assert hashes(dataset, True) == before, 'Copied samples changed'
                report['copiedSourceSamplesUnchanged'] = True
                return

            settle(); options.focus(); page.keyboard.press('Enter')
            expect(panel).to_be_visible()
            for control in (line, points):
                expect(control).to_have_attribute('type', 'text')
                expect(control).to_have_attribute('inputmode', 'decimal')
                expect(control).to_have_value('100')
            page.keyboard.press('Tab'); expect(line).to_be_focused()
            line.fill('125'); expect(line).to_have_value('125')
            page.keyboard.press('Tab'); expect(points).to_be_focused()
            points.fill('125'); expect(points).to_have_value('125')
            page.keyboard.press('Escape'); expect(options).to_be_focused()
            reset()
            passed('Options is enabled for TP Time; accessible percentage text fields support keyboard focus, Escape and reset')

            for name, changes in [
                ('TP Time', {}), ('Full line', {'viewMode': 'full'}),
                ('Full envelope', {'viewMode': 'full', 'fullPlotMode': 'envelope'}),
                ('FFT', {'viewMode': 'spectrum'}),
                ('PSD log', {'viewMode': 'spectrum', 'specMode': 'welch', 'specLogY': True}),
                ('Full multi-variable', {'viewMode': 'full', 'fullPlotExtraColumns': [['torque_nm', 'current_a']] + [[]] * 8}),
            ]:
                seed(changes)
                baseline = styles(); remember()
                change('Line thickness', 200)
                ratio(styles()['widths'], baseline['widths'], 2); retained()
                change('Line thickness', 50)
                ratio(styles()['widths'], baseline['widths'], .5); retained()
                reset(); assert styles() == baseline; retained()
                passed(f'{name}: actual trace widths change in place without changing native arrays, axes or visibility')

            seed({'viewMode': 'full', 'plotDensity': 'quad'})
            bounds = plot().locator('.u-over').first.bounding_box()
            page.mouse.move(bounds['x'] + bounds['width'] * .2, bounds['y'] + bounds['height'] * .2)
            page.mouse.down()
            page.mouse.move(bounds['x'] + bounds['width'] * .7, bounds['y'] + bounds['height'] * .7, steps=7)
            page.mouse.up(); settle(); page.mouse.move(0, 0)
            remember(); change('Line thickness', 250); retained()
            passed('Four linked plots retain their instances, data and drag-zoomed X/Y bounds when thickness changes')

            for source in ('tp', 'full'):
                seed({'viewMode': 'xy', 'xySource': source})
                baseline = styles(); remember()
                change('Scatter size', 200)
                ratio(styles()['sizes'], baseline['sizes'], 2); retained()
                change('Line thickness', 200)
                ratio(styles()['sizes'], baseline['sizes'], 2); retained()
                reset(); assert styles() == baseline; retained()
                passed(f'XY {source}: actual point diameter changes in place independently from line thickness')

            seed({'scatterCollapsed': False})
            dots = page.locator('.recharts-scatter-symbol circle[fill]:not([fill="none"])')
            expect(dots.first).to_be_visible()
            radii = dots.evaluate_all('els=>els.map(el=>Number(el.getAttribute("r")))')
            assert radii and all(value > 0 for value in radii), radii
            change('Scatter size', 200)
            ratio(dots.evaluate_all('els=>els.map(el=>Number(el.getAttribute("r")))'), radii, 2)
            change('Line thickness', 175)
            assert page.evaluate('JSON.parse(localStorage.getItem("ptt.plot-appearance.v1"))') == {'lineScale': 1.75, 'pointScale': 2}
            page.reload(); settle(); open_options()
            expect(line).to_have_value('175'); expect(points).to_have_value('200')
            page.keyboard.press('Escape')
            ratio(dots.evaluate_all('els=>els.map(el=>Number(el.getAttribute("r")))'), radii, 2)
            reset(); page.reload(); settle(); open_options()
            expect(line).to_have_value('100'); expect(points).to_have_value('100')
            page.keyboard.press('Escape')
            passed('Overview symbol radii scale with scatter size; both independent preferences and reset survive reload')

            seed({'viewMode': 'full'}, {'lineScale': 2, 'pointScale': 2})
            before_export = styles(); canvas = plot().locator('canvas').first.evaluate('c=>c.toDataURL()')
            plot().get_by_role('button', name=re.compile('^Plot actions for ')).click()
            page.get_by_role('menuitem', name='Export CSV / PNG…', exact=True).click()
            dialog = page.get_by_role('dialog', name=re.compile('^Export '))
            dialog.get_by_role('checkbox', name='Include analysis metadata (ZIP)', exact=True).uncheck()
            with page.expect_download() as event: dialog.get_by_role('button', name='Download PNG', exact=True).click()
            event.value.save_as(output / 'appearance-export.png'); page.keyboard.press('Escape')
            export = page.evaluate('window.__appearanceExport')
            assert export['widths'] == before_export['widths'] and export['canvas'] == canvas
            assert plot().locator('canvas').first.evaluate('c=>c.toDataURL()') == canvas
            assert (output / 'appearance-export.png').stat().st_size > 2000
            passed('PNG capture uses the displayed scaled widths and identical source pixels, then retains the live canvas')

            window_id = cdp.send('Browser.getWindowForTarget')['windowId']
            for theme, width, zoom in [('light', 1500, 1), ('dark', 1050, 1), ('light', 1500, 1.5), ('dark', 1050, 1.25)]:
                page.evaluate('theme=>localStorage.setItem("ptt.theme.v1",theme)', theme)
                cdp.send('Browser.setWindowBounds', {'windowId': window_id, 'bounds': {'width': width, 'height': 1000}})
                set_browser_zoom(worker, page, zoom)
                seed({'viewMode': 'xy', 'plotDensity': 'nine'}, {'lineScale': 3, 'pointScale': 3})
                open_options()
                assert panel.evaluate('''el=>{const r=el.getBoundingClientRect();return (
                  r.left>=0&&r.top>=0&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1&&el.scrollWidth<=el.clientWidth+1);}''')
                expect(line).to_have_value('300'); expect(points).to_have_value('300')
                capture_browser_view(cdp, output / f'options-{theme}-{width}-{zoom}.png')
                page.keyboard.press('Escape')
                plot().get_by_role('button', name=re.compile('^Expand ')).click(); settle()
                open_options(); change('Scatter size', 150)
                plot().get_by_role('button', name=re.compile('^Minimize ')).click(); settle()
                assert page.locator(PLOTS).count() == 9
                passed(f'{theme} {width}px desktop at actual {zoom * 100:g}% zoom: Options, nine cards, maximize/restore and size changes')

            assert not report['pageErrors'], report['pageErrors']
            assert not report['blockedWrites'], report['blockedWrites']
            assert hashes(dataset, True) == before, 'Copied samples changed'
            report['copiedSourceSamplesUnchanged'] = True
        except Exception:
            capture_browser_view(cdp, output / 'failure.png')
            (output / 'failure-dom.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
            raise
        finally:
            (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-python', type=Path, default=ROOT / 'backend/.venv/Scripts/python.exe')
    parser.add_argument('--web-port', type=int, default=3128)
    parser.add_argument('--api-port', type=int, default=8028)
    parser.add_argument('--supplemental-only', action='store_true', help='Only multi-flight, point PNG, SVG animation and Split checks')
    parser.add_argument('--inputs-only', action='store_true', help='Only text-input editing, validation, decimal geometry, persistence and compact desktop checks')
    parser.add_argument('--compact-only', action='store_true', help='Only compact appearance layout, keyboard/validation and existing full/spectrum controls')
    parser.add_argument('--output', type=Path, default=Path(tempfile.gettempdir()) / 'ptt-plot-appearance')
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    original = hashes(ROOT / 'data/tests')
    with tempfile.TemporaryDirectory(prefix='ptt-plot-appearance-') as directory:
        tmp = Path(directory)
        with servers(tmp, args.output, args.backend_python, args.web_port, args.api_port) as (web, api, dataset):
            verify(web, api, dataset, tmp, args.output, args.supplemental_only, args.inputs_only, args.compact_only)
    assert hashes(ROOT / 'data/tests') == original, 'Original datasets changed'
    print('Original datasets unchanged; owned servers stopped. Report:', args.output, flush=True)


if __name__ == '__main__': main()
