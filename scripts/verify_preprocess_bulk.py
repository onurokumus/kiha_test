"""Isolated browser checks for bulk Uploads preprocessing configuration.

Start an owned Vite preview, then run with global Python + Playwright:
  python -X utf8 scripts/verify_preprocess_bulk.py --url http://127.0.0.1:8107/ptt/
All /api requests are intercepted. Fixtures and the accepted preprocessing job
exist only in memory; neither a backend nor user recordings are accessed.
The Chromium extension changes real browser zoom in a temporary profile.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlparse

from playwright.sync_api import expect, sync_playwright

from verify_browser_zoom import capture_browser_view, set_browser_zoom


NAME = 'bulk_preprocess_fixture'
OUTPUT = NAME + '_filtered'
PARAMETERS = ['thrust_n', 'torque_nm', 'rpm', '__proto__', 'constructor', 'toString'] + [f'sensor_{i:02d}_temperature_c' for i in range(30)]
SOURCE = {'name': NAME, 'id': 'f148ace7-fa60-40b5-9cbe-2e032fe765b7', 'revision': 'bulk-fixture-v1'}
INFO = {'name': NAME, 'status': 'ready', 'n_rows': 1200, 'fs_hz': 200,
        'duration_s': 6, 'n_columns': len(PARAMETERS) + 1,
        'source_file': 'bulk-original.csv', 'created_at': '2026-10-09T10:00:00Z'}
META = {**INFO, 'columns': ['time_s', *PARAMETERS], 'time_column': 'time_s',
        'time_source': 'measured', 't_start': 0, 'nan_counts': {}, 'inf_counts': {}}


def install_fixture(context, report):
    """Fail closed for every unknown API route, including GETs."""
    before = json.dumps((SOURCE, INFO, META), sort_keys=True)
    jobs = []

    def handle(route):
        request = route.request
        path = unquote(urlparse(request.url).path).split('/api', 1)[-1]
        if request.method != 'GET':
            if request.method != 'POST' or path != f'/tests/{NAME}/preprocess':
                report['blockedWrites'].append({'method': request.method, 'path': path})
                route.abort(); return
            payload = request.post_data_json
            report['posts'].append(payload)
            assert payload['name'] == OUTPUT
            assert payload['source_id'] == SOURCE['id'] and payload['source_revision'] == SOURCE['revision']
            recipe = {'version': 1, 'source': copy.deepcopy(SOURCE), 'filters': copy.deepcopy(payload['filters']),
                      'created_at': '2026-10-09T11:00:00Z', 'completed_at': '2026-10-09T11:00:01Z',
                      'method': 'whole_native_recording', 'warnings': [], 'original_raw_available': True}
            jobs.append({**INFO, 'name': OUTPUT, 'preprocessing': recipe})
            route.fulfill(status=202, json={'name': OUTPUT, 'status': 'rebuilding'})
            return
        if path == '/settings/defaults':
            body = {'settings': None}
        elif path == '/analysis-sources':
            body = {'version': 1, 'sources': [{**SOURCE, 'status': 'ready',
                     'columns': META['columns'], 'test_points': []}, *[
                     {**SOURCE, 'name': job['name'], 'id': 'c2b1a28c-dfdc-426f-9649-e9aa96d4a62d',
                      'status': 'ready', 'columns': META['columns'], 'test_points': []} for job in jobs]]}
        elif path == '/components':
            body = {'version': 1, 'components': []}
        elif path == '/trash':
            body = {'entries': [], 'retention_seconds': 3600}
        elif path == '/tests':
            body = [INFO, *jobs]
        elif path == f'/tests/{NAME}':
            body = META
        elif path == f'/tests/{NAME}/preprocess':
            body = {'source': SOURCE, 'meta': META, 'preprocessing': None, 'max_samples': 8000000}
        elif path == f'/tests/{NAME}/testpoints':
            body = {'version': 1, 'test': NAME, 'test_points': []}
        elif path == f'/tests/{NAME}/tp_stats':
            body = []
        elif jobs and path == f'/tests/{OUTPUT}':
            body = {**META, **jobs[0]}
        elif jobs and path == f'/tests/{OUTPUT}/testpoints':
            body = {'version': 1, 'test': OUTPUT, 'test_points': []}
        elif jobs and path == f'/tests/{OUTPUT}/tp_stats':
            body = []
        elif jobs and path == f'/tests/{OUTPUT}/preprocess':
            body = {'source': {**SOURCE, 'name': OUTPUT}, 'meta': {**META, **jobs[0]},
                    'preprocessing': jobs[0]['preprocessing'], 'max_samples': 8000000}
        else:
            report['unexpectedRequests'].append({'method': request.method, 'path': path})
            route.fulfill(status=404, json={'detail': 'Unknown fixture route'})
            return
        route.fulfill(status=200, json=body)

    context.route('**/api/**', handle)
    return lambda: before == json.dumps((SOURCE, INFO, META), sort_keys=True)


def verify(args):
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    report = {'checks': [], 'posts': [], 'blockedWrites': [], 'unexpectedRequests': [],
              'pageErrors': [], 'consoleErrors': [], 'layouts': []}

    def passed(label):
        report['checks'].append(label); print('PASS:', label, flush=True)

    with tempfile.TemporaryDirectory(prefix='ptt-preprocess-bulk-') as temporary, sync_playwright() as p:
        temporary = Path(temporary)
        extension = temporary / 'extension'; extension.mkdir()
        (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3,
            'name': 'Bulk preprocessing browser verification', 'version': '1.0',
            'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}), encoding='utf-8')
        (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});', encoding='utf-8')
        context = p.chromium.launch_persistent_context(str(temporary / 'profile'), channel='chromium',
            headless=True, no_viewport=True, args=[f'--disable-extensions-except={extension}',
            f'--load-extension={extension}', '--window-size=1440,1000'])
        unchanged = install_fixture(context, report)
        page = context.pages[0]; page.set_default_timeout(15000)
        page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))
        page.on('console', lambda event: report['consoleErrors'].append(event.text) if event.type == 'error' else None)
        page.add_init_script("""localStorage.clear();localStorage.setItem('ptt.theme.v1','light');
            localStorage.setItem('ptt.settings.v1',JSON.stringify({scatterX:'rpm',scatterY:'thrust_n',
            clustering:false,gridColumns:['thrust_n'],defaultViewMode:'tp'}));""")
        cdp = context.new_cdp_session(page)
        try:
            page.goto(args.url); page.wait_for_load_state('networkidle')
            report['bundle'] = page.locator('script[type="module"][src]').get_attribute('src')
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            window = cdp.send('Browser.getWindowForTarget')['windowId']
            baseline = page.evaluate('devicePixelRatio')
            page.get_by_role('navigation', name='Main navigation').get_by_role('button', name='Uploads', exact=True).click()
            opener = page.get_by_role('button', name='Pre-process ' + NAME, exact=True)
            opener.focus(); page.keyboard.press('Enter')
            dialog = page.get_by_role('dialog', name='Pre-process test', exact=True)
            expect(dialog).to_be_visible()
            expect(dialog.get_by_role('searchbox', name='Search parameters', exact=True)).to_be_visible()
            # Record rendered accessibility state before choosing interaction locators.
            (output / 'initial-aria.txt').write_text(dialog.aria_snapshot(), encoding='utf-8')
            capture_browser_view(cdp, output / 'initial-light.png')
            if args.inspect:
                print(dialog.aria_snapshot(), flush=True)
                return
            run_checks(page, dialog, opener, cdp, worker, window, baseline, output, report, passed)
            assert unchanged(), 'The original source fixture changed'
            assert not report['blockedWrites'] and not report['unexpectedRequests'], report
            assert not report['pageErrors'] and not report['consoleErrors'], report
        except Exception:
            capture_browser_view(cdp, output / 'failure.png')
            print(page.locator('body').aria_snapshot()[-14000:], flush=True)
            raise
        finally:
            (output / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            context.close()
    print(f"PASS {len(report['checks'])} browser groups. Evidence: {output}", flush=True)


def run_checks(page, dialog, opener, cdp, worker, window, baseline, output, report, passed):
    search = dialog.get_by_role('searchbox', name='Search parameters', exact=True)
    editor = dialog.get_by_role('region', name='Parameter filter settings', exact=True)
    kind = editor.get_by_role('combobox', name='Filter', exact=True)
    save = dialog.get_by_role('button', name='Save filtered copy', exact=True)
    master = dialog.get_by_role('checkbox', name='Select all parameters', exact=True)
    selected = dialog.get_by_role('button', name='Edit filter for selected parameters', exact=True)
    apply = editor.get_by_role('button', name='Apply filter to selected', exact=True)
    clear = dialog.get_by_role('button', name='Clear selection', exact=True)

    def checkbox(column):
        return dialog.get_by_role('checkbox', name=f'Select {column} for bulk filtering', exact=True)

    def row(column):
        return dialog.get_by_role('button', name=f'Configure {column}', exact=True)

    def configure(column, filter_kind, **fields):
        search.fill(column); row(column).click()
        kind.select_option(filter_kind)
        for label, value in fields.items():
            editor.get_by_role('textbox', name=label, exact=True).fill(str(value))
        search.fill('')

    def check_values(column, filter_kind, **fields):
        search.fill(column); row(column).click()
        expect(kind).to_have_value(filter_kind)
        for label, value in fields.items():
            expect(editor.get_by_role('textbox', name=label, exact=True)).to_have_value(str(value))
        search.fill('')

    expect(save).to_be_disabled()
    expect(master).not_to_be_checked()
    expect(checkbox('time_s')).to_have_count(0)
    expect(row('time_s')).to_have_count(0)
    expect(dialog.get_by_text('Time preserved', exact=True)).to_be_visible()
    expect(dialog.get_by_role('checkbox', name=re.compile('^Select .+ for bulk filtering$'))).to_have_count(len(PARAMETERS))
    page.keyboard.press('Escape'); expect(dialog).not_to_be_visible(); expect(opener).to_be_focused()
    opener.press('Enter'); expect(dialog).to_be_visible(); expect(search).to_be_visible()
    passed('keyboard opener, Escape focus return, empty recipe guard and protected time column')

    configure('rpm', 'detrend')
    configure('thrust_n', 'lowpass', **{'Order': 4, 'Cutoff (Hz)': 8})
    configure('torque_nm', 'moving_avg', **{'Window (s)': '.025'})
    expect(save).to_be_enabled()
    checkbox('thrust_n').focus(); page.keyboard.press('Space')
    expect(checkbox('thrust_n')).to_be_checked()
    checkbox('torque_nm').check()
    expect(selected).to_contain_text('2 selected')
    assert master.evaluate('el => el.indeterminate')
    kind.select_option('bandpass')
    editor.get_by_role('textbox', name='Order', exact=True).fill('3')
    editor.get_by_role('textbox', name='Low cutoff (Hz)', exact=True).fill('3')
    editor.get_by_role('textbox', name='High cutoff (Hz)', exact=True).fill('100')
    expect(apply).to_be_disabled(); expect(save).to_be_disabled()
    expect(row('thrust_n')).to_contain_text('Low-pass')
    expect(row('torque_nm')).to_contain_text('Moving average')
    expect(editor.get_by_role('status')).to_contain_text('below 100 Hz')
    editor.get_by_role('textbox', name='High cutoff (Hz)', exact=True).fill('2')
    expect(apply).to_be_disabled()
    editor.get_by_role('textbox', name='High cutoff (Hz)', exact=True).fill('12')
    apply.focus(); page.keyboard.press('Enter')
    expect(row('thrust_n')).to_contain_text('Band-pass')
    expect(row('torque_nm')).to_contain_text('Band-pass')
    expect(row('rpm')).to_contain_text('Detrend')
    expect(save).to_be_enabled()
    expect(dialog.get_by_text('Applied Band-pass to 2 parameters.', exact=True)).to_be_visible()
    passed('multi-selection and keyboard Apply validate a separate bulk draft and preserve untargeted settings')

    check_values('thrust_n', 'bandpass', **{'Order': 3, 'Low cutoff (Hz)': 3, 'High cutoff (Hz)': 12})
    editor.get_by_role('textbox', name='Low cutoff (Hz)', exact=True).fill('4')
    check_values('torque_nm', 'bandpass', **{'Order': 3, 'Low cutoff (Hz)': 3, 'High cutoff (Hz)': 12})
    expect(checkbox('thrust_n')).to_be_checked(); expect(checkbox('torque_nm')).to_be_checked()
    selected.click(); kind.select_option('lowpass')
    editor.get_by_role('textbox', name='Cutoff (Hz)', exact=True).fill('100')
    row('rpm').click(); expect(save).to_be_disabled()
    dialog.get_by_role('button', name='Discard bulk changes', exact=True).focus()
    page.keyboard.press('Enter'); expect(kind).to_be_focused()
    expect(save).to_be_enabled()
    check_values('thrust_n', 'bandpass', **{'Low cutoff (Hz)': 4})
    check_values('torque_nm', 'bandpass', **{'Low cutoff (Hz)': 3})
    clear.focus(); page.keyboard.press('Enter'); expect(master).to_be_focused()
    passed('bulk settings are independent copies; individual overrides and discard retain existing filters')

    # Adding a target after Apply must not imply that it already has the filter.
    row('sensor_00_temperature_c').click(); checkbox('sensor_00_temperature_c').check()
    kind.select_option('detrend'); apply.click()
    expect(save).to_be_enabled()
    checkbox('sensor_01_temperature_c').check()
    expect(save).to_be_disabled()
    expect(row('sensor_01_temperature_c')).to_contain_text('None')
    apply.click(); expect(save).to_be_enabled()
    expect(row('sensor_01_temperature_c')).to_contain_text('Detrend')
    clear.click()
    passed('adding a target to an applied group requires Apply before Save and never silently filters the new target')

    for column in ('__proto__', 'constructor', 'toString'):
        configure(column, 'detrend')
        check_values(column, 'detrend')
        expect(editor).not_to_contain_text('missing or invalid samples')
        checkbox(column).check(); kind.select_option('moving_avg')
        editor.get_by_role('textbox', name='Window (s)', exact=True).fill('.035')
        apply.click(); check_values(column, 'moving_avg', **{'Window (s)': '.035'})
        selected.click(); kind.select_option('')
        editor.get_by_role('button', name='Remove filters from selected', exact=True).click()
        expect(row(column)).to_contain_text('None'); clear.click()
    passed('special JavaScript property names remain ordinary parameter names for configure, bulk apply and clear')

    search.fill('sensor_0')
    dialog.get_by_role('button', name='Select visible', exact=True).click()
    expect(selected).to_contain_text('10 selected')
    assert master.evaluate('el => el.indeterminate')
    checkbox('sensor_00_temperature_c').uncheck()
    expect(selected).to_contain_text('9 selected')
    search.fill('rpm')
    expect(selected).to_contain_text('9 hidden')
    dialog.get_by_role('button', name='Select visible', exact=True).click()
    expect(selected).to_contain_text('10 selected')
    expect(checkbox('rpm')).to_be_checked()
    clear.click(); expect(master).not_to_be_checked()
    assert not master.evaluate('el => el.indeterminate')
    search.fill('no_matching_parameter')
    expect(dialog.get_by_role('button', name='Select visible', exact=True)).to_be_disabled()
    master.check(); expect(master).to_be_checked()
    expect(selected).to_contain_text(f'{len(PARAMETERS)} selected'); expect(selected).to_contain_text(f'{len(PARAMETERS)} hidden')
    kind.select_option('moving_avg'); editor.get_by_role('textbox', name='Window (s)', exact=True).fill('.025')
    apply.click(); search.fill('')
    for column in PARAMETERS:
        expect(row(column)).to_contain_text('Moving average')
        expect(checkbox(column)).to_be_checked()
    expect(checkbox('time_s')).to_have_count(0)
    checkbox('thrust_n').uncheck(); assert master.evaluate('el => el.indeterminate')
    master.check(); expect(master).to_be_checked()
    master.uncheck(); expect(selected).to_have_count(0)
    passed('Select visible adds only search results; global Select all includes hidden signals and excludes time')

    configure('thrust_n', 'lowpass', **{'Order': 4, 'Cutoff (Hz)': 8})
    configure('torque_nm', 'bandpass', **{'Order': 2, 'Low cutoff (Hz)': 3, 'High cutoff (Hz)': 16})
    checkbox('rpm').check(); checkbox('sensor_00_temperature_c').check()
    kind.select_option('')
    remove = editor.get_by_role('button', name='Remove filters from selected', exact=True)
    expect(save).to_be_disabled(); remove.click()
    expect(row('rpm')).to_contain_text('None'); expect(row('sensor_00_temperature_c')).to_contain_text('None')
    expect(row('thrust_n')).to_contain_text('Low-pass'); expect(row('torque_nm')).to_contain_text('Band-pass')
    clear.click()
    configured_only = dialog.get_by_role('checkbox', name='Configured only', exact=True)
    configured_only.check()
    expect(row('rpm')).to_have_count(0); expect(row('sensor_00_temperature_c')).to_have_count(0)
    dialog.get_by_role('button', name='Select visible', exact=True).click()
    expect(selected).to_contain_text(f'{len(PARAMETERS)-2} selected')
    clear.click(); configured_only.uncheck()
    expect(save).to_be_enabled()
    assert not report['posts'], 'Draft configuration must never submit API writes'
    passed('None removes only the selected filters; configured-only selection and clear selection preserve other settings')

    dialog.get_by_role('textbox', name='Filtered test name', exact=True).fill(OUTPUT)
    save.click()
    expect(dialog.get_by_text('Filtered copy saved', exact=True)).to_be_visible()
    expected = [{'column': column, 'filter': ({'kind': 'lowpass', 'order': 4, 'f1': 8} if column == 'thrust_n'
               else {'kind': 'bandpass', 'order': 2, 'f1': 3, 'f2': 16} if column == 'torque_nm'
               else {'kind': 'moving_avg', 'window_s': .025})}
               for column in PARAMETERS if column not in ('rpm', 'sensor_00_temperature_c')]
    assert len(report['posts']) == 1 and report['posts'][0]['filters'] == expected, report['posts']
    recipe = dialog.get_by_role('region', name='Saved preprocessing recipe', exact=True)
    expect(recipe.locator('li')).to_have_count(len(PARAMETERS)-2)
    expect(dialog.get_by_role('link', name='Filtered CSV', exact=True)).to_be_visible()
    expect(dialog.get_by_role('link', name='Original uploaded CSV', exact=True)).to_be_visible()
    passed('one final save serializes every independent parameter filter exactly and retains original/filtered downloads')
    page.keyboard.press('Escape'); expect(dialog).not_to_be_visible(); expect(opener).to_be_focused()

    for theme in ('light', 'dark'):
        switch = page.get_by_role('switch', name='Dark mode', exact=True)
        if (switch.get_attribute('aria-checked') == 'true') != (theme == 'dark'):
            switch.click()
        opener.click(); expect(search).to_be_visible()
        master.check(); kind.select_option('despike')
        editor.get_by_role('textbox', name='Window (ms)', exact=True).fill('35')
        for zoom in (1, 1.25, 1.5):
            width, height = (1440, 1000) if zoom == 1 else (1100, 780)
            cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': height}})
            set_browser_zoom(worker, page, zoom)
            page.wait_for_function('dpr => Math.abs(devicePixelRatio-dpr)<.01', arg=baseline*zoom)
            layout = dialog.evaluate('''el => {
                const r = el.getBoundingClientRect();
                return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:innerWidth,height:innerHeight,
                    overflow:el.scrollWidth-el.clientWidth};
            }''')
            assert layout['left'] >= 0 and layout['top'] >= 0 and layout['right'] <= layout['width'] + 1 and layout['bottom'] <= layout['height'] + 1, layout
            assert layout['overflow'] <= 1, layout
            report['layouts'].append({'theme': theme, 'zoom': zoom, **layout})
            search.scroll_into_view_if_needed(); expect(master).to_be_in_viewport()
            capture_browser_view(cdp, output / f'bulk-selection-{theme}-{zoom}.png')
            for label in ('Window (ms)', 'Max spike (ms)', 'Threshold (MAD)', 'Min jump (units)'):
                field = editor.get_by_role('textbox', name=label, exact=True)
                field.scroll_into_view_if_needed(); field.focus(); expect(field).to_be_focused(); expect(field).to_be_in_viewport()
            apply.scroll_into_view_if_needed(); apply.focus(); expect(apply).to_be_focused(); expect(apply).to_be_in_viewport()
            expect(save).to_be_in_viewport()
            capture_browser_view(cdp, output / f'bulk-editor-{theme}-{zoom}.png')
        page.keyboard.press('Escape'); expect(dialog).not_to_be_visible(); expect(opener).to_be_focused()
        set_browser_zoom(worker, page, 1)
        cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': 1440, 'height': 1000}})
    passed('light/dark styling, dense filter keyboard access, resized desktop and actual 100/125/150% browser zoom')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8107/ptt/')
    parser.add_argument('--output', type=Path, default=Path(tempfile.gettempdir()) / 'ptt-preprocess-bulk-verification')
    parser.add_argument('--inspect', action='store_true', help='Capture initial accessibility tree and screenshot only')
    verify(parser.parse_args())
