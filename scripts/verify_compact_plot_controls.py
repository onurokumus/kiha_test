"""Compact plot headers, gesture isolation, and native menu action regression.

Uses temporary uploaded data, a private Chromium profile, and owned Python 3.13
backend/Vite processes. Global Python runs only stdlib/Playwright. Source sample
files are fingerprinted before and after; all fixture state is removed afterward.
"""
import argparse
import csv
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile

from playwright.sync_api import expect, sync_playwright
import verify_plot_exports as fixture
from verify_data_quality import ROOT, servers
from verify_time_y_zoom import instrument, assert_auto_y_plot
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout


def run_checks(web, api, dataset, temporary, output):
    extension = temporary / 'extension'
    extension.mkdir()
    (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Compact plot controls zoom checks',
        'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    with sync_playwright() as p:
        request = p.request.new_context(base_url=api + '/')
        fixture.fixtures(request)
        catalog = request.get('analysis-sources').json()['sources']
        before = fixture.dataset_hashes(dataset)
        context = p.chromium.launch_persistent_context(str(temporary / 'profile'), channel='chromium', headless=True,
            no_viewport=True, args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]
        page.set_default_timeout(10000)
        errors, downloads, payloads = [], [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('request', lambda r: payloads.append([r.url, r.post_data_json])
            if r.method == 'POST' and re.search(r'/(plot|spectrum|xy)-export$', r.url) else None)
        page.route('**/src/utils/uplotSync.ts*', instrument)
        settings = {'scatterX': fixture.LOAD, 'scatterY': fixture.REFERENCE, 'gridColumns': fixture.COLS, 'clustering': False}
        session = {'version': 1, 'sources': catalog, 'currentTest': fixture.A, 'xAxis': fixture.LOAD, 'yAxis': fixture.REFERENCE,
            'axesUserSet': True, 'plotConfigs': fixture.COLS * 2 + [fixture.LOAD], 'plotsUserEdited': True, 'plotDensity': 'quad',
            'plotFilters': [fixture.DESPIKE, fixture.MOVING, {}, {}], 'plotShowOriginal': [True, False],
            'scatterCollapsed': True, 'viewMode': 'tp', 'xyXCol': fixture.REFERENCE,
            'selections': [{'test': test, 'tpId': tp, 'hidden': False}
                for test, tp in ((fixture.A, 7), (fixture.A, 9), (fixture.B, 7))]}
        page.add_init_script(f'''if(!sessionStorage.getItem('menus-seeded')){{
          sessionStorage.setItem('menus-seeded','1');
          localStorage.setItem('ptt.settings.v1',{json.dumps(json.dumps(settings))});
          localStorage.setItem('ptt.analysis-session.v1',{json.dumps(json.dumps(session))});}}''')
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp = context.new_cdp_session(page)

        def settle():
            page.wait_for_load_state('networkidle')
            page.locator('.analyze-plots-pane .uplot').first.wait_for()
            wait_for_chart_layout(page)

        def plots(): return page.locator('.analyze-plots-pane [role="group"][aria-label$="plot"]')
        def menu(): return page.get_by_role('menu', name=re.compile('^Plot actions for '))
        def button(target): return target.get_by_role('button', name=re.compile('^Plot actions for '))
        def axes(target): return target.locator('.uplot').evaluate('(el)=>{const u=el.__verificationPlot;return [u.scales.x.min,u.scales.x.max,u.scales.y.min,u.scales.y.max]}')
        def set_axes(target, x, y=None):
            target.locator('.uplot').evaluate('(el,q)=>{const u=el.__verificationPlot;u.batch(()=>{u.setScale("x",{min:q.x[0],max:q.x[1]});if(q.y)u.setScale("y",{min:q.y[0],max:q.y[1]})})}', {'x': x, 'y': y})
            settle()

        def open_menu(target, method='right', fraction=.4):
            target.locator('.uplot').wait_for()
            before_axes = axes(target)
            if method == 'right':
                # Locator hover retries if ResizeObserver replaces a canvas.
                # scroll_into_view_if_needed holds one element handle and can
                # fail during the existing plot rebuild after browser zoom.
                over = target.locator('.u-over'); over.hover()
                rect = over.bounding_box()
                page.mouse.click(rect['x'] + rect['width'] * fraction, rect['y'] + rect['height'] * .5, button='right')
            elif method == 'key':
                target.locator('[aria-label^="Plot canvas for "]').focus(); page.keyboard.press('Shift+F10')
            else:
                button(target).focus(); page.keyboard.press('Enter')
            expect(menu()).to_be_visible()
            assert menu().count() == 1
            assert axes(target) == before_axes, 'Opening menu changed plot axes'
            assert menu().evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight&&el.scrollWidth<=el.clientWidth}'), 'Menu outside viewport'
            return menu()

        def action(target, name, method='right'):
            role = 'menuitemcheckbox' if name == 'Auto-fit Y to visible data' else 'menuitem'
            open_menu(target, method).get_by_role(role, name=name, exact=True).click()
            expect(menu()).not_to_be_visible()

        def mode(name):
            page.get_by_role('group', name='Plot mode', exact=True).get_by_role('button', name=name, exact=True).click(); settle()

        def compact_headers():
            # The native header itself has only its maximize/restore and menu
            # buttons; controls in explicitly opened panels stay available.
            for target in plots().all():
                header = target.locator('[data-plot-header]')
                expect(header).to_have_count(1)
                visible = header.locator('[data-plot-toolbar]').get_by_role('button').all()
                assert len(visible) == 2, [(b.get_attribute('aria-label'), b.inner_text()) for b in visible]
                expect(button(target)).to_be_visible()
                expect(target.get_by_role('button', name=re.compile('^(Expand|Minimize) '))).to_be_visible()
                assert header.evaluate('el=>el.scrollWidth<=el.clientWidth+1'), 'Plot header overflows'

        def drag(target, mouse='left', modifier=None):
            over = target.locator('.u-over'); over.scroll_into_view_if_needed()
            rect = over.bounding_box()
            if modifier: page.keyboard.down(modifier)
            page.mouse.move(rect['x'] + rect['width'] * .35, rect['y'] + rect['height'] * .35)
            page.mouse.down(button=mouse)
            page.mouse.move(rect['x'] + rect['width'] * .65, rect['y'] + rect['height'] * .65, steps=8)
            page.mouse.up(button=mouse)
            if modifier: page.keyboard.up(modifier)
            page.wait_for_timeout(300)

        def check_gestures(target, reset_name):
            baseline = axes(target)
            x = [baseline[0] + (baseline[1]-baseline[0])*.2, baseline[1] - (baseline[1]-baseline[0])*.2]
            set_axes(target, x)
            if not target.get_attribute('aria-label').endswith('XY plot'):
                assert_auto_y_plot(page, target)
            for modifier in (None, 'Alt', 'Shift'):
                before_axes = axes(target)
                before_selection = target.locator('.uplot').evaluate('el=>({...el.__verificationPlot.select})')
                drag(target, 'right', modifier)
                assert axes(target) == before_axes, ('Right drag changed axes', modifier, before_axes, axes(target))
                assert target.locator('.uplot').evaluate('el=>({...el.__verificationPlot.select})') == before_selection, 'Right drag changed selection'
                expect(menu()).to_be_visible()
                page.keyboard.press('Escape')
            for mouse, modifier in (('left', None), ('left', 'Shift'), ('middle', None)):
                action(target, reset_name); settle()
                set_axes(target, x)
                before_axes = axes(target)
                drag(target, mouse, modifier)
                assert axes(target) != before_axes, ('Expected existing gesture to change axes', mouse, modifier)
                expect(menu()).not_to_be_visible()
                if not target.get_attribute('aria-label').endswith('XY plot'):
                    assert_auto_y_plot(page, target)
            action(target, reset_name); settle()
            assert axes(target) == baseline

        def settings_dialog(target, action_name, dialog_name):
            action(target, action_name, 'button')
            dialog = page.get_by_role('dialog', name=dialog_name, exact=True)
            expect(dialog).to_be_visible()
            assert dialog.evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1}'), 'Settings dialog outside viewport'
            page.keyboard.press('Escape')
            expect(dialog).not_to_be_visible()
            expect(button(target)).to_be_focused()

        def annotation_dialog(): return page.get_by_role('dialog', name=f'Time notes for {fixture.LOAD}', exact=True)
        def annotation_ready(): expect(annotation_dialog().get_by_role('button', name='Reload saved notes', exact=True)).to_be_enabled()
        def close_notes():
            page.keyboard.press('Escape'); expect(annotation_dialog()).not_to_be_visible()
            expect(button(plots().first)).to_be_focused()

        def export(target, label):
            action(target, 'Export CSV / PNG…', 'key')
            dialog = page.get_by_role('dialog', name=re.compile('^Export '))
            expect(dialog).to_be_visible()
            for kind in ('CSV', 'PNG'):
                path, name = fixture.download(page, dialog.get_by_role('button', name=f'Download {kind}', exact=True), output, label + '-' + kind, 'zip')
                with zipfile.ZipFile(path) as archive:
                    meta = json.loads(archive.read('analysis.json'))
                    filename = next(n for n in archive.namelist() if n.endswith('.' + kind.lower()))
                    body = archive.read(filename)
                    if kind == 'CSV':
                        rows = list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
                        assert rows and 'unplotted_secret' not in rows[0], rows[:1]
                        url, payload = payloads[-1]
                        replay = request.post(url, data={**payload, 'include_metadata': False})
                        assert replay.ok and replay.body() == body, replay.text()[:100]
                        assert payload['column'] == fixture.LOAD
                    else:
                        png = output / (label + '.png'); png.write_bytes(body)
                        fixture.png_inspect(page, png, [])
                    downloads.append({'name': name, 'mode': label, 'kind': kind, 'metadata': meta['schema_version'] if 'schema_version' in meta else True})
            page.keyboard.press('Escape'); expect(dialog).not_to_be_visible()
            expect(button(target)).to_be_focused()

        try:
            page.goto(web); settle()
            target = plots().first
            compact_headers()
            check_gestures(target, 'Reset linked time / TP Y axes')
            settings_dialog(target, 'Filter settings…', f'Filter settings for {fixture.LOAD}')
            # Pointer, both keyboard paths, roving focus, disabled items, Tab and outside dismissal.
            for method in ('right', 'key', 'button'):
                open_menu(target, method)
                expect(menu().get_by_role('menuitem').first).to_be_focused()
                page.keyboard.press('End'); expect(menu().get_by_role('menuitemcheckbox').last).to_be_focused()
                page.keyboard.press('Home'); expect(menu().get_by_role('menuitem').first).to_be_focused()
                page.keyboard.press('ArrowUp'); expect(menu().get_by_role('menuitemcheckbox').last).to_be_focused()
                page.keyboard.press('ArrowDown'); expect(menu().get_by_role('menuitem').first).to_be_focused()
                page.keyboard.press('m'); expect(menu().get_by_role('menuitem', name='Manage time notes…', exact=True)).to_be_focused()
                page.keyboard.press('Escape'); expect(menu()).not_to_be_visible(); expect(button(target)).to_be_focused()
            open_menu(target, 'key'); page.keyboard.press('Tab'); expect(menu()).not_to_be_visible()
            assert page.evaluate('document.activeElement?.tabIndex') == 0
            open_menu(target); page.get_by_role('group', name='Plot mode', exact=True).click(position={'x': 1, 'y': 1}); expect(menu()).not_to_be_visible()
            open_menu(target); open_menu(plots().nth(1)); expect(menu()).to_have_attribute('aria-label', f'Plot actions for {fixture.SECOND}')
            page.keyboard.press('Escape')
            open_menu(plots().nth(2))
            expect(menu().get_by_role('menuitemcheckbox', name=re.compile('^Show original'))).to_have_attribute('aria-disabled', 'true')
            page.keyboard.press('Escape')
            # Native menu remains available on toolbar and in modal editable fields.
            assert not page.get_by_role('group', name='Plot mode', exact=True).evaluate('el=>{const e=new MouseEvent("contextmenu",{bubbles:true,cancelable:true});el.dispatchEvent(e);return e.defaultPrevented}')
            action(target, 'Manage time notes…'); annotation_ready()
            assert not annotation_dialog().get_by_role('textbox', name='Note', exact=True).evaluate('el=>{const e=new MouseEvent("contextmenu",{bubbles:true,cancelable:true});el.dispatchEvent(e);return e.defaultPrevented}')
            close_notes()
            print('PASS: plot-only native suppression, right-click/Shift+F10/button, arrows/Home/End/typeahead/Escape/Tab/focus and single-menu targeting', flush=True)

            # Same overlay state, independent slots, and no extra filter request for display-only toggles.
            filter_calls = []
            listener = lambda r: filter_calls.append(r.url) if '/filter?' in r.url else None
            page.on('request', listener)
            open_menu(target).get_by_role('menuitemcheckbox', name='Show original with filtered', exact=True).click()
            open_menu(target); expect(menu().get_by_role('menuitemcheckbox', name='Show original with filtered', exact=True)).to_have_attribute('aria-checked', 'false')
            menu().get_by_role('menuitemcheckbox', name='Show original with filtered', exact=True).click(); settle()
            open_menu(target); expect(menu().get_by_role('menuitemcheckbox', name='Show original with filtered', exact=True)).to_have_attribute('aria-checked', 'true')
            page.keyboard.press('Escape')
            assert not filter_calls, filter_calls
            page.remove_listener('request', listener)
            auto = axes(target)
            set_axes(target, [.05, .5], [5, 30])
            action(target, 'Auto-fit Y to visible data'); settle()
            assert axes(target)[:2] == [.05, .5] and axes(target)[2:] != [5, 30]
            action(target, 'Reset linked time / TP Y axes'); settle()
            assert axes(target) == auto
            print('PASS: shared persisted overlay without filtering again; independent Y reset and linked time/all-TP-Y reset', flush=True)

            # Clicked TP-relative time maps to actual source origin, never rounded TP descriptors.
            set_axes(target, [.05, .5])
            click_time = .05 + (.5 - .05) * .4
            action(target, 'Add time marker…'); annotation_ready()
            dialog = annotation_dialog()
            origins = {}
            for test in (fixture.A, fixture.B):
                data = request.get(f'tests/{test}/testpoints/7/data', params={'cols': fixture.LOAD}).json()
                origins[test] = data['series'][fixture.LOAD]['analysis']['source_centers']['first_time_s']
            time_input = dialog.get_by_label('Time (test seconds)', exact=True)
            # Native pointer coordinates are integer CSS pixels: compare to actual uPlot conversion with <= 1 pixel tolerance.
            tolerance = .45 / target.locator('.u-over').bounding_box()['width'] + 1e-9
            assert math.isclose(float(time_input.input_value()), origins[fixture.A] + click_time, abs_tol=tolerance)
            source = dialog.get_by_role('combobox', name='Source', exact=True)
            value = source.locator('option').evaluate_all('(els)=>els.find(e=>e.textContent.includes("plot_export_beta · TP 7")).value')
            source.select_option(value); annotation_ready()
            assert math.isclose(float(time_input.input_value()), origins[fixture.B] + click_time, abs_tol=tolerance)
            expected_time = float(time_input.input_value())
            dialog.get_by_role('textbox', name='Note', exact=True).fill('Marker created from plot menu')
            url = api + f'/tests/{fixture.B}/annotations'
            page.route(url, lambda r: r.fulfill(status=503, json={'detail': 'Isolated menu save failure'}) if r.request.method == 'PUT' else r.continue_())
            dialog.get_by_role('button', name='Save annotation', exact=True).click()
            expect(dialog.get_by_role('alert')).to_contain_text('Isolated menu save failure')
            expect(dialog.get_by_role('textbox', name='Note', exact=True)).to_have_value('Marker created from plot menu')
            page.unroute(url)
            dialog.get_by_role('button', name='Save annotation', exact=True).click(); expect(dialog.get_by_role('status')).to_have_text('Annotation saved')
            stored = request.get(f'tests/{fixture.B}/annotations').json()['annotations']
            assert len(stored) == 1 and stored[0]['start_s'] == expected_time
            assert request.get(f'tests/{fixture.A}/annotations').json()['annotations'] == []
            close_notes()
            action(target, 'Add interval note…', 'button'); annotation_ready()
            expect(dialog.get_by_role('combobox', name='Kind', exact=True)).to_have_value('interval')
            assert math.isclose(float(dialog.get_by_label('Start (test seconds)', exact=True).input_value()), origins[fixture.A] + .05)
            assert math.isclose(float(dialog.get_by_label('End (test seconds)', exact=True).input_value()), origins[fixture.A] + .5)
            dialog.get_by_role('textbox', name='Note', exact=True).fill('Visible interval from menu')
            dialog.get_by_role('button', name='Save annotation', exact=True).click(); expect(dialog.get_by_role('status')).to_have_text('Annotation saved'); close_notes()
            action(target, 'Add time marker…', 'key'); annotation_ready()
            dialog.get_by_role('textbox', name='Note', exact=True).fill('Do not save this draft')
            page.keyboard.press('Escape'); expect(dialog.get_by_role('alert')).to_contain_text('discard')
            dialog.get_by_role('button', name='Confirm', exact=True).click(); expect(dialog).not_to_be_visible()
            open_menu(target).get_by_role('menuitemcheckbox', name='Show time notes (all plots)', exact=True).click()
            expect(page.get_by_role('checkbox', name='Show time notes', exact=True)).not_to_be_checked()
            page.get_by_role('checkbox', name='Show time notes', exact=True).check()
            print('PASS: real source-specific marker/interval, exact origin, failure/retry, draft guard and shared note visibility', flush=True)

            action(target, 'Reset linked time / TP Y axes'); settle()
            export(target, 'tp')
            # Failed exports share the existing Retry/Close behavior, with no partial download.
            action(target, 'Export CSV / PNG…')
            modal = page.get_by_role('dialog', name=re.compile('^Export '))
            page.route(api + '/plot-export', lambda r: r.fulfill(status=503, json={'detail': 'Isolated menu export failure'}))
            modal.get_by_role('button', name='Download CSV', exact=True).click()
            expect(modal.get_by_text(re.compile('Isolated menu export failure'))).to_be_visible()
            page.unroute(api + '/plot-export'); page.keyboard.press('Escape'); expect(modal).not_to_be_visible()
            for name, key in [('Full test', 'full'), ('Spectrum', 'spectrum'), ('XY', 'xy')]:
                mode(name); target = plots().first
                print(f'Checking {name}: {target.get_attribute("aria-label")}', flush=True)
                compact_headers()
                if key == 'spectrum':
                    settings_dialog(target, 'Analysis details…', f'Spectrum details for {fixture.LOAD}')
                    page.wait_for_timeout(350)
                    assert page.evaluate('JSON.parse(localStorage.getItem("ptt.analysis-session.v1")).plotViewports.spectrum[0]') is None, 'Initial Spectrum autoscale became saved manual viewport'
                    action(target, 'Export CSV / PNG…')
                    partial = page.get_by_role('dialog', name=re.compile('^Export '))
                    expect(partial.get_by_role('button', name='Download CSV', exact=True)).to_be_disabled()
                    page.keyboard.press('Escape')
                    page.get_by_role('button', name=re.compile('Expand selection tray')).click()
                    page.get_by_role('button', name=f'Hide Long spike from {fixture.A}', exact=True).click()
                    page.get_by_role('button', name=re.compile('Collapse selection tray')).click(); settle()
                if key == 'full':
                    settings_dialog(target, 'Filter settings…', f'Filter settings for {fixture.LOAD}')
                    action(target, 'Add time marker…', 'button'); annotation_ready()
                    assert math.isclose(float(dialog.get_by_label('Time (test seconds)', exact=True).input_value()), sum(axes(target)[:2]) / 2)
                    close_notes()
                    action(target, 'Reset linked time zoom')
                else:
                    open_menu(target)
                    assert menu().get_by_role('menuitemcheckbox').count() == 0
                    assert menu().get_by_role('menuitem', name=re.compile('note|marker')).count() == 0
                    page.keyboard.press('Escape')
                    original_axes = axes(target)
                    target.locator('.u-over').hover(); page.mouse.wheel(0, -200); settle()
                    assert axes(target) != original_axes
                    action(target, 'Reset axes'); settle(); assert axes(target) == original_axes
                check_gestures(target, 'Reset linked time zoom' if key == 'full' else 'Reset axes')
                if key == 'spectrum':
                    page.wait_for_function('JSON.parse(localStorage.getItem("ptt.analysis-session.v1")).plotViewports.spectrum[0] === null')
                    target.locator('.u-over').hover(); page.mouse.wheel(0, -120); settle()
                    page.wait_for_function('JSON.parse(localStorage.getItem("ptt.analysis-session.v1")).plotViewports.spectrum[0] !== null')
                    target.locator('.u-over').dblclick(); settle()
                    page.wait_for_function('JSON.parse(localStorage.getItem("ptt.analysis-session.v1")).plotViewports.spectrum[0] === null')
                    assert_auto_y_plot(page, target)
                    print('PASS: Spectrum initial/reset/double-click keep automatic saved viewport; user wheel saves explicit X', flush=True)
                export(target, key)
            print('PASS: compact headers, right-click/Alt-right/Shift-right do not change axes or selection; preserved left drag/Shift/middle pan; eight native CSV/PNG downloads and exact API replay', flush=True)

            # Overview action is only its existing reset; no sample export or time inference on aggregate points.
            page.get_by_role('button', name='Show scatter panel', exact=True).click(); settle()
            overview = page.get_by_label('Plot canvas for test-point overview', exact=True)
            overview.focus(); page.keyboard.press('Shift+F10')
            expect(menu()).to_be_visible(); expect(menu().get_by_role('menuitem')).to_have_count(1)
            menu().get_by_role('menuitem', name='Reset axes', exact=True).click()
            page.get_by_role('button', name='Hide scatter panel', exact=True).click(); settle()

            for name in ('Test points', 'Full test', 'Spectrum', 'XY'):
                mode(name)
                for zoom in (1.25, 1.5):
                    set_browser_zoom(worker, page, zoom); settle()
                    page.get_by_role('group', name='Plot layout', exact=True).get_by_role('button', name='9', exact=True).click(); settle()
                    assert plots().count() == 9, 'All nine persisted slots must have menu access'
                    compact_headers()
                    if name == 'XY':
                        page.get_by_role('button', name='Edit plots', exact=True).click()
                        for target in plots().all():
                            for picker in target.locator('[data-plot-header]').get_by_role('button').all():
                                assert picker.evaluate('el=>{const r=el.getBoundingClientRect(),h=el.closest("[data-plot-header]").getBoundingClientRect();return r.left>=h.left-1&&r.right<=h.right+1}'), 'XY editor picker outside header'
                        capture_browser_view(cdp, output / f'XY-edit-{zoom}.png')
                        page.get_by_role('button', name='Editing plots', exact=True).click()
                    for index in (0, plots().count() - 1):
                        target = plots().nth(index)
                        open_menu(target, 'right', .98)
                        capture_browser_view(cdp, output / f'{name}-{zoom}-{index}.png')
                        page.keyboard.press('Escape')
                    target = plots().first
                    target.get_by_role('button', name=re.compile('^Expand ')).click(); settle()
                    compact_headers()
                    open_menu(plots().first, 'key'); page.keyboard.press('Escape')
                    plots().first.get_by_role('button', name=re.compile('^Minimize ')).click(); settle()
                    open_menu(plots().first, 'button'); set_browser_zoom(worker, page, 1); settle(); expect(menu()).not_to_be_visible()
                page.get_by_role('group', name='Plot layout', exact=True).get_by_role('button', name='4', exact=True).click(); settle()
            cdp.send('Browser.setWindowBounds', {'windowId': cdp.send('Browser.getWindowForTarget')['windowId'], 'bounds': {'width': 1100, 'height': 760}})
            settle(); open_menu(plots().first, 'button'); capture_browser_view(cdp, output / 'desktop-1100-menu.png'); page.keyboard.press('Escape')
            page.reload(); settle(); open_menu(plots().first, 'key'); page.keyboard.press('Escape')
            # Programmatic context changes must close a captured menu and never
            # resurrect it on switching back. Use the existing mode controls.
            open_menu(plots().first)
            page.get_by_role('group', name='Plot mode', exact=True).get_by_role('button', name='Spectrum', exact=True).evaluate('el=>el.click()')
            settle(); expect(menu()).not_to_be_visible(); mode('XY'); expect(menu()).not_to_be_visible()
            # Failed trace loads still expose the menu/export explanations; no
            # marker may be seeded from absent/stale source timing.
            mode('Test points')
            page.wait_for_function('JSON.parse(localStorage.getItem("ptt.analysis-session.v1")).viewMode === "tp"')
            page.route('**/testpoints/*/data?*', lambda r: r.fulfill(status=503, json={'detail': 'Isolated missing trace'}))
            page.route('**/filter?*', lambda r: r.fulfill(status=503, json={'detail': 'Isolated missing filter'}))
            page.reload(); page.wait_for_load_state('networkidle')
            target = plots().first
            expect(target.locator('.uplot')).not_to_be_attached()
            button(target).click(); expect(menu()).to_be_visible()
            expect(menu().get_by_role('menuitem', name=re.compile('^Add time marker'))).to_have_attribute('aria-disabled', 'true')
            menu().get_by_role('menuitem', name='Export CSV / PNG…', exact=True).click()
            empty = page.get_by_role('dialog', name=re.compile('^Export '))
            expect(empty.get_by_role('button', name='Download PNG', exact=True)).to_be_disabled()
            page.keyboard.press('Escape')
            page.unroute('**/testpoints/*/data?*'); page.unroute('**/filter?*')
            page.reload(); settle()
            assert fixture.dataset_hashes(dataset) == before, 'Source files changed (annotations are separately stored)'
            assert not errors, errors
            (output / 'results.json').write_text(json.dumps({'downloads': downloads, 'unchanged_source_files': sorted(before), 'errors': errors}, indent=2))
            print(f'PASS: overview reset, nine slots/all-mode desktop/maximize/restore/125%/150%, stale/failed traces/reload; {len(before)} source files unchanged, no page errors', flush=True)
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            (output / 'failure-state.json').write_text(json.dumps({'errors': errors, 'buttons': page.get_by_role('button').all_text_contents(), 'body': page.locator('body').inner_text()}, indent=2))
            raise
        finally:
            context.close(); request.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3300)
    parser.add_argument('--backend-port', type=int, default=8300)
    parser.add_argument('--regressions', action='store_true', help='Also run isolated Time Y and rendering regression suites')
    parser.add_argument('--regressions-only', action='store_true', help='Run only isolated Time Y and rendering regression suites')
    args = parser.parse_args()
    output = ROOT / 'data/verification/compact-plot-controls'; output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-compact-controls-') as directory:
        temporary = Path(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, dataset):
            if not args.regressions_only:
                run_checks(web, api, dataset, temporary, output)
            if args.regressions or args.regressions_only:
                for name in ('verify_time_y_zoom.py', 'verify_rendering.py'):
                    print(f'Running {name}', flush=True)
                    result = subprocess.run([sys.executable, str(ROOT/'scripts'/name), '--url', web], cwd=ROOT, capture_output=True, text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    print(result.stdout, end='', flush=True)
                    if result.returncode:
                        print(result.stderr, end='', flush=True)
                        result.check_returncode()
    print('PASS: isolated data/profile and owned servers cleaned', flush=True)


if __name__ == '__main__': main()
