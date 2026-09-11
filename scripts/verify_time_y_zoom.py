"""Phase 4a browser regression, isolated GET fixtures; no backend data writes.

Start Vite on 3100 (BROWSER=none), then run with global Python/Playwright.
Only the test browser's transformed module gains an instance inspection hook.
Production source and the user's browser/profile are never instrumented.
"""
import argparse
import json
import math
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import sync_playwright, expect
from verify_rendering import TEST, X, Y, COLS, POINTS, SOURCE, mock_api
from verify_browser_zoom import set_browser_zoom, wait_for_chart_layout, check_crosshair, capture_browser_view


def instrument(route):
    response = route.fetch()
    source = response.text()
    needle = 'a.plotRef.current = u;'
    assert source.count(needle) == 1, 'uPlot inspection hook location changed'
    route.fulfill(response=response, body=source.replace(needle, needle + '\n u.root.__verificationPlot = u;'))


def fixture(route):
    url = urlparse(route.request.url)
    path = unquote(url.path)
    query = parse_qs(url.query)
    if path.endswith('/filter') or ('/testpoints/' in path and path.endswith('/data')):
        assert route.request.method == 'GET'
        point_id = int(path.split('/')[-2]) if path.endswith('/data') else int(query.get('tp_id', ['1'])[0])
        point = POINTS[point_id - 1]
        times = [i / 100 for i in range(1000)]
        series = {}
        for col in query['cols'][0].split(','):
            if col == 'rpm':
                values = [1500] * len(times)
            elif col == 'temperature_c':
                values = [None] * len(times)
            else:
                values = [None if col == 'current_a' and i % 8 == 0 else 50 + 10 * math.sin(t) for i, t in enumerate(times)]
            if path.endswith('/filter'):
                series[col] = [None if value is None else value * .5 for value in values]
            else:
                series[col] = {'t': times, 'y': values}
        body = {'test': TEST, 'test_point': point, 'mode': 'raw', 'level': 1,
                'n_raw': 1000, 'i0': 0, 'i1': 1000, 'point_budget': 1500,
                'time_origin_s': point['start_s'], 'duration_s': 10, 'series': series}
        if path.endswith('/filter'):
            body['t'] = [t + point['start_s'] for t in times]
            body['relative_t'] = times
            body['tp_id'] = point['id']
        route.fulfill(json=body)
    else:
        mock_api(route)


def configure(page):
    settings = {'scatterX': X, 'scatterY': Y, 'clustering': False,
                'gridColumns': COLS[1:], 'defaultViewMode': 'tp'}
    session = {'version': 1, 'sources': [SOURCE], 'currentTest': TEST, 'xAxis': X, 'yAxis': Y,
               'axesUserSet': True, 'selections': [{'test': TEST, 'tpId': 1, 'hidden': False}],
               'plotConfigs': COLS[1:], 'plotsUserEdited': True, 'plotDensity': 'quad'}
    page.add_init_script(f"""if (!sessionStorage.getItem('fixture-seeded')) {{
      localStorage.clear(); sessionStorage.setItem('fixture-seeded', 'true');
      localStorage.setItem('ptt.settings.v1', {json.dumps(json.dumps(settings))});
      localStorage.setItem('ptt.analysis-session.v1', {json.dumps(json.dumps(session))});
    }}""")
    page.context.route('**/api/**', fixture)
    page.context.route('**/src/utils/uplotSync.ts*', instrument)


def plot(page, col=Y):
    return page.get_by_role('group', name=f'{col} time plot', exact=True).first


def scales(page, col=Y):
    wait_for_chart_layout(page)
    return plot(page, col).locator('.uplot').evaluate('''el => {
      const u = el.__verificationPlot;
      return {x: [u.scales.x.min, u.scales.x.max], y: [u.scales.y.min, u.scales.y.max]};
    }''')


def close_pair(actual, expected, label='range'):
    assert len(actual) == len(expected) == 2
    assert all(math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-8) for a, b in zip(actual, expected)), (label, actual, expected)


def assert_visible_auto_y(page, col=Y, allow_empty=False):
    return assert_auto_y_plot(page, plot(page, col), allow_empty)


def assert_auto_y_plot(page, target, allow_empty=False):
    """Independent Python oracle for displayed line segments and 10% padding."""
    wait_for_chart_layout(page)
    visible = target.locator('.uplot').evaluate('''el => {
      const u=el.__verificationPlot;
      return {x:[u.scales.x.min,u.scales.x.max],y:[u.scales.y.min,u.scales.y.max],
        traces:u.series.slice(1).flatMap((s,j)=>s.show===false||s.auto===false?[]:
          [{x:s.facets?u.data[j+1][0]:u.data[0],y:s.facets?u.data[j+1][1]:u.data[j+1]}])};
    }''')
    values = []
    finite = lambda value: isinstance(value, (int, float)) and math.isfinite(value)
    for trace in visible['traces']:
        pairs = list(zip(trace['x'], trace['y']))
        values.extend(y for x, y in pairs if finite(x) and finite(y) and visible['x'][0] <= x <= visible['x'][1])
        for (x0, y0), (x1, y1) in zip(pairs, pairs[1:]):
            if not all(finite(v) for v in (x0, y0, x1, y1)) or x0 == x1: continue
            for bound in visible['x']:
                if min(x0, x1) < bound < max(x0, x1):
                    fraction = (bound-x0)/(x1-x0)
                    values.append(y0*(1-fraction)+y1*fraction)
    if not values and allow_empty:
        assert all(math.isfinite(v) for v in visible['y']) and visible['y'][0] < visible['y'][1], 'Empty window needs a finite usable Y fallback'
        return visible['y']
    assert values, 'Oracle needs visible finite line data'
    low, high = min(values), max(values)
    span, magnitude = high-low, max(abs(low), abs(high))
    pad = .1*span if span > magnitude*1e-9 else .001*magnitude if magnitude else 1
    close_pair(visible['y'], [low-pad, high+pad], 'visible Y auto-fit')
    return visible['y']


def check_visible_auto_y(page):
    baseline = scales(page)
    plot(page).locator('.uplot').evaluate('el=>el.__verificationPlot.setScale("x",{min:.135,max:.385})')
    page.wait_for_timeout(300)
    narrow = assert_visible_auto_y(page)
    assert narrow[1]-narrow[0] < (baseline['y'][1]-baseline['y'][0])*.2
    wheel(page)
    assert_visible_auto_y(page)
    drag(page, pan='shift')
    assert_visible_auto_y(page)
    apply_range(page, [45, 55])
    plot(page).get_by_role('button', name=f'Plot actions for {Y}', exact=True).click()
    item = page.get_by_role('menuitemcheckbox', name='Auto-fit Y to visible data', exact=True)
    expect(item).to_have_attribute('aria-checked', 'false')
    item.click()
    assert_visible_auto_y(page)
    reset_all(page)
    close_pair(scales(page)['y'], baseline['y'])
    print('PASS: automatic Y follows visible X zoom/pan and clipped line edges; menu restores Auto after manual bounds', flush=True)


def editor(page, col=Y, keyboard=False):
    trigger = plot(page, col).get_by_role('button', name=f'Plot actions for {col}', exact=True)
    if keyboard:
        trigger.focus()
        # At actual browser zoom, focusing a header above its scrolled canvas
        # first scrolls the pane. Finish that focus scroll before opening a
        # menu whose intentional scroll behavior is to dismiss itself.
        wait_for_chart_layout(page)
        page.keyboard.press('Enter')
    else:
        trigger.click()
    menu = page.get_by_role('menu', name=f'Plot actions for {col}', exact=True)
    expect(menu).to_be_visible()
    item = menu.get_by_role('menuitem', name='Set Y-axis bounds…', exact=True)
    if keyboard:
        item.focus(); page.keyboard.press('Enter')
    else:
        item.click()
    panel = page.get_by_role('dialog', name=f'Y axis for {col}', exact=True)
    expect(panel).to_be_visible()
    return panel


def apply_range(page, values, col=Y, keyboard=False):
    panel = editor(page, col, keyboard)
    panel.get_by_label('Minimum', exact=True).fill(str(values[0]))
    panel.get_by_label('Maximum', exact=True).fill(str(values[1]))
    if keyboard:
        page.keyboard.press('Enter')
    else:
        panel.get_by_role('button', name='Apply range', exact=True).click()
    expect(panel).not_to_be_visible()
    expect(plot(page, col).get_by_role('button', name=f'Plot actions for {col}', exact=True)).to_be_focused()
    close_pair(scales(page, col)['y'], values)


def wheel(page, alt=False, delta=-120, col=Y):
    over = plot(page, col).locator('.u-over')
    over.scroll_into_view_if_needed()
    box = over.bounding_box()
    page.mouse.move(box['x'] + box['width'] * .4, box['y'] + box['height'] * .6)
    if alt:
        page.keyboard.down('Alt')
    page.mouse.wheel(0, delta)
    if alt:
        page.keyboard.up('Alt')
    page.wait_for_timeout(200)


def drag(page, alt=False, reverse=False, pan=None):
    over = plot(page).locator('.u-over')
    over.scroll_into_view_if_needed()
    box = over.bounding_box()
    start = (.3, .2) if alt else (.2, .5)
    end = (.7, .8) if alt else (.8, .5)
    if reverse:
        start, end = end, start
    if alt:
        page.keyboard.down('Alt')
    if pan == 'shift':
        page.keyboard.down('Shift')
    page.mouse.move(box['x'] + box['width'] * start[0], box['y'] + box['height'] * start[1])
    button = 'middle' if pan == 'middle' else 'left'
    page.mouse.down(button=button)
    page.mouse.move(box['x'] + box['width'] * end[0], box['y'] + box['height'] * end[1], steps=8)
    if alt:
        selections = page.locator('.u-select').evaluate_all('els => els.map(el => el.getBoundingClientRect().height)')
        assert selections[0] > 10 and all(h == 0 for h in selections[1:]), selections
    page.mouse.up(button=button)
    if alt:
        page.keyboard.up('Alt')
    if pan == 'shift':
        page.keyboard.up('Shift')
    page.wait_for_timeout(250)


def reset_all(page):
    page.get_by_role('button', name='Reset zoom', exact=True).click()
    page.wait_for_timeout(300)


def run(page, worker, cdp, output):
    initial = scales(page)
    rpm = scales(page, 'rpm')
    assert initial['y'][0] < 40 and initial['y'][1] > 60, initial
    assert rpm['y'][0] < 1500 < rpm['y'][1], rpm
    plot(page, 'temperature_c').get_by_role('button', name='Plot actions for temperature_c', exact=True).click()
    expect(page.get_by_role('menu').get_by_role('menuitem', name=re.compile('^Set Y-axis bounds'))).to_have_attribute('aria-disabled', 'true')
    page.keyboard.press('Escape')
    assert scales(page, 'current_a')['y'] == initial['y']
    print('PASS: legacy session automatic ranges, constant/all-missing/sparse trace states', flush=True)
    check_visible_auto_y(page)

    # Opening the ellipsis while the floating Y editor is present dismisses
    # that editor; selecting bounds with the keyboard must reopen it normally.
    panel = editor(page, keyboard=True)
    plot(page).get_by_role('button', name=f'Plot actions for {Y}', exact=True).click()
    expect(panel).not_to_be_visible()
    page.get_by_role('menuitem', name='Set Y-axis bounds…', exact=True).focus()
    page.keyboard.press('Enter')
    expect(panel).to_be_visible()
    page.keyboard.press('Escape')
    expect(plot(page).get_by_role('button', name=f'Plot actions for {Y}', exact=True)).to_be_focused()

    apply_range(page, [46, 54], keyboard=True)
    close_pair(scales(page)['x'], initial['x'])
    close_pair(scales(page, 'rpm')['y'], rpm['y'])
    panel = editor(page, keyboard=True)
    expect(panel.get_by_label('Minimum', exact=True)).to_be_focused()
    for invalid in [('', '4'), ('nan', '4'), ('Infinity', '4'), ('2', '2'), ('8', '-2'), ('1e308', '-1e308'), ('1000000000', '1000000000.00001')]:
        panel.get_by_label('Minimum', exact=True).fill(invalid[0])
        panel.get_by_label('Maximum', exact=True).fill(invalid[1])
        panel.get_by_role('button', name='Apply range').click()
        expect(panel.get_by_role('alert')).to_be_visible()
        close_pair(scales(page)['y'], [46, 54])
    page.keyboard.press('Escape')
    expect(panel).not_to_be_visible()
    print('PASS: keyboard precise range, finite/order/precision validation, Escape/focus, independent Y', flush=True)

    wheel(page, alt=True)
    after = scales(page)
    close_pair(after['x'], initial['x'])
    assert math.isclose(after['y'][1] - after['y'][0], 8 * .85, rel_tol=1e-5)
    close_pair(scales(page, 'rpm')['y'], rpm['y'])
    wheel(page, alt=True, delta=120)
    close_pair(scales(page)['y'], [46, 54])
    for reverse in [False, True]:
        previous = scales(page)
        drag(page, alt=True, reverse=reverse)
        after = scales(page)
        assert .56 < (after['y'][1] - after['y'][0]) / (previous['y'][1] - previous['y'][0]) < .64
        close_pair(after['x'], previous['x'])
    y = scales(page)['y']
    wheel(page)
    zoomed_x = scales(page)['x']
    assert zoomed_x != initial['x']
    close_pair(scales(page)['y'], y)
    close_pair(scales(page, 'rpm')['x'], zoomed_x)
    for pan in ['shift', 'middle', None]:
        before = scales(page)
        drag(page, pan=pan)
        assert scales(page)['x'] != before['x']
        close_pair(scales(page)['y'], y)
        close_pair(scales(page, 'rpm')['x'], scales(page)['x'])
    before_reset_x = scales(page)['x']
    editor(page).get_by_role('button', name='Reset Y', exact=True).click()
    assert scales(page)['x'] != initial['x']
    close_pair(scales(page)['x'], before_reset_x)
    assert_visible_auto_y(page, allow_empty=True)
    reset_all(page)
    close_pair(scales(page)['x'], initial['x'])
    print('PASS: Alt wheel/forward-reverse Y selection, unlinked Y rectangle, existing X wheel/drag/Shift/middle pan, local Y reset', flush=True)

    apply_range(page, [44, 56])
    apply_range(page, [1499, 1501], 'rpm')
    wheel(page)
    x = scales(page)['x']
    for col in [Y, 'rpm']:
        plot(page, col).get_by_role('button', name=f'Expand {col}', exact=True).click()
        wait_for_chart_layout(page)
        plot(page, col).get_by_role('button', name=f'Minimize {col}', exact=True).click()
        close_pair(scales(page)['y'], [44, 56])
        close_pair(scales(page, 'rpm')['y'], [1499, 1501])
        close_pair(scales(page)['x'], x)
    for label in ['1', '9', '4']:
        page.get_by_role('group', name='Plot layout', exact=True).get_by_role('button', name=label, exact=True).click()
        close_pair(scales(page)['y'], [44, 56])
    close_pair(scales(page, 'rpm')['y'], [1499, 1501])
    page.wait_for_timeout(400)
    page.reload()
    page.wait_for_load_state('networkidle')
    close_pair(scales(page)['y'], [44, 56])
    close_pair(scales(page, 'rpm')['y'], [1499, 1501])
    close_pair(scales(page)['x'], x)
    reset_all(page)
    close_pair(scales(page)['x'], initial['x'])
    close_pair(scales(page)['y'], initial['y'])
    close_pair(scales(page, 'rpm')['y'], rpm['y'])
    print('PASS: all slots survive maximize/density/reload; reset after rebuilt/saved X+Y zoom', flush=True)

    # Context changes through real UI; the last range belongs only to its
    # original variable and visible TP intervals, even when a slot is reused.
    apply_range(page, [44, 56])
    apply_range(page, [1499, 1501], 'rpm')
    page.get_by_role('button', name='Edit plots', exact=True).click()
    plot(page).get_by_role('button', name='Plot variable', exact=True).click()
    page.get_by_role('option', name='current_a', exact=True).click()
    close_pair(scales(page, 'current_a')['y'], initial['y'])
    close_pair(scales(page, 'rpm')['y'], [1499, 1501])
    plot(page, 'current_a').get_by_role('button', name='Plot variable', exact=True).click()
    page.get_by_role('option', name=Y, exact=True).click()
    page.get_by_role('button', name='Editing plots', exact=True).click()
    close_pair(scales(page)['y'], [44, 56])
    page.locator('.recharts-scatter-symbol').nth(2).click()
    close_pair(scales(page)['y'], initial['y'])
    close_pair(scales(page, 'rpm')['y'], rpm['y'])
    page.get_by_role('button', name=re.compile('2 of .*Expand selection tray')).click()
    page.get_by_role('button', name=f'Hide {POINTS[2]["name"]} from {TEST}', exact=True).click()
    close_pair(scales(page)['y'], [44, 56])
    page.get_by_role('button', name=f'Show {POINTS[2]["name"]} from {TEST}', exact=True).click()
    close_pair(scales(page)['y'], initial['y'])
    page.get_by_role('button', name=re.compile('2 of .*Collapse selection tray')).click()
    page.locator('.recharts-scatter-symbol').nth(2).click()
    close_pair(scales(page)['y'], [44, 56])
    reset_all(page)
    print('PASS: variable/selection/visibility context isolation and returning to the original context', flush=True)

    # Real filter control/request lifecycle, with deterministic mock signals.
    plot(page).get_by_role('button', name=f'Expand {Y}', exact=True).click()
    apply_range(page, [20, 30])
    plot(page).get_by_role('button', name=f'Plot actions for {Y}', exact=True).click()
    page.get_by_role('menuitem', name='Filter settings…', exact=True).click()
    filter_panel = page.get_by_role('dialog', name=f'Filter settings for {Y}', exact=True)
    filter_panel.get_by_title('Filter this plot only', exact=True).select_option('moving_avg')
    page.keyboard.press('Escape')
    expect(plot(page).get_by_text('filtered', exact=True)).to_be_visible()
    close_pair(scales(page)['y'], [20, 30])
    editor(page).get_by_role('button', name='Reset Y', exact=True).click()
    filtered_range = scales(page)['y']
    assert filtered_range[0] < 20 and 30 < filtered_range[1] < 40, filtered_range
    apply_range(page, [22, 28])
    plot(page).get_by_role('button', name=f'Plot actions for {Y}', exact=True).click()
    page.get_by_role('menuitem', name='Filter settings…', exact=True).click()
    filter_panel.get_by_role('button', name='Clear', exact=True).click()
    page.keyboard.press('Escape')
    close_pair(scales(page)['y'], [22, 28])
    editor(page).get_by_role('button', name='Reset Y', exact=True).click()
    close_pair(scales(page)['y'], initial['y'])
    plot(page).get_by_role('button', name=f'Minimize {Y}', exact=True).click()

    # A pending X wheel commit must not undo a reset, even when X was auto
    # and the shared button was visible solely because of a manual Y range.
    apply_range(page, [44, 56])
    plot(page).locator('.u-over').dispatch_event('wheel', {'deltaY': -120, 'clientX': 500, 'clientY': 300})
    page.get_by_role('button', name='Reset zoom', exact=True).evaluate('el => el.click()')
    page.wait_for_timeout(350)
    close_pair(scales(page)['x'], initial['x'])
    close_pair(scales(page)['y'], initial['y'])
    apply_range(page, [44, 56])
    wheel(page)
    plot(page).locator('.u-over').dblclick()
    page.wait_for_timeout(350)
    close_pair(scales(page)['x'], initial['x'])
    close_pair(scales(page)['y'], initial['y'])
    print('PASS: filtered range preservation/reset, clear filter, pending-wheel reset and double-click reset', flush=True)

    # Exercise the actual session parser, including adversarial stored values.
    result = page.evaluate('''async () => {
      const {normalizeAnalysisSession: normalize} = await import('/src/services/analysisSession.ts');
      const {timePlotContexts} = await import('/src/utils/timePlotRanges.ts');
      const invalid = [null, {}, 'bad', [1, 1], [2, 1], [NaN, 3], [0, Infinity], [-1e308, 1e308], [1e9, 1e9 + 1e-5], ['1', 2]];
      const rejected = invalid.every(range => normalize({timeYRanges: [{context:'test',range}]}).timeYRanges[0] === null);
      const legacy = normalize({version:1}).timeYRanges.every(value => value === null);
      const point = {test:'a', tpId:1, id:'a:1', tp:{start_s:0}, endS:1};
      const key = timePlotContexts(['signal'], [point], new Set())[0];
      const changedTest = timePlotContexts(['signal'], [{...point, test:'b'}], new Set())[0] !== key;
      const changedInterval = timePlotContexts(['signal'], [{...point, endS:2}], new Set())[0] !== key;
      return {rejected, legacy, changedTest, changedInterval, bounded:normalize({timeYRanges:Array(30).fill({context:key,range:[-2,2]})}).timeYRanges.length === 9};
    }''')
    assert all(result.values()), result
    print('PASS: existing session parser rejects unsafe ranges; nine-slot bound and test/interval identity', flush=True)

    baseline_dpr = page.evaluate('devicePixelRatio')
    window_id = cdp.send('Browser.getWindowForTarget')['windowId']
    for width, factor in [(1100, 1), (1440, 1.25), (1440, 1.5)]:
        cdp.send('Browser.setWindowBounds', {'windowId': window_id, 'bounds': {'width': width, 'height': 1000}})
        set_browser_zoom(worker, page, factor)
        page.wait_for_function('dpr => Math.abs(devicePixelRatio - dpr) < .001', arg=baseline_dpr * factor)
        wait_for_chart_layout(page)
        apply_range(page, [43, 57], keyboard=True)
        check_crosshair(page, f'Y zoom {width}/{factor}')
        trigger = plot(page).get_by_role('button', name=f'Expand {Y}', exact=True)
        trigger.focus()
        page.keyboard.press('Enter')
        close_pair(scales(page)['y'], [43, 57])
        check_crosshair(page, f'maximized {factor}')
        panel = editor(page, keyboard=True)
        bounds = panel.bounding_box()
        viewport = page.evaluate('({w:innerWidth,h:innerHeight})')
        assert bounds['x'] >= 0 and bounds['y'] >= 0 and bounds['x'] + bounds['width'] <= viewport['w'] + 1 and bounds['y'] + bounds['height'] <= viewport['h'] + 1
        capture_browser_view(cdp, output / f'controls-{factor}.png')
        page.keyboard.press('Escape')
        plot(page).get_by_role('button', name=f'Minimize {Y}', exact=True).focus()
        page.keyboard.press('Enter')
        close_pair(scales(page)['y'], [43, 57])
        wheel(page, alt=True)
        reset_all(page)
    set_browser_zoom(worker, page, 1)
    print('PASS: desktop resize, actual 125/150% browser zoom, editor bounds, keyboard maximize/restore and cursor geometry', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:3100')
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / 'data/verification/time-y-zoom'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-y-zoom-') as directory, sync_playwright() as p:
        temporary = Path(directory)
        extension = temporary / 'extension'
        extension.mkdir()
        (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Y zoom verification', 'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}), encoding='utf-8')
        (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(() => {});', encoding='utf-8')
        context = p.chromium.launch_persistent_context(str(temporary / 'profile'), channel='chromium', headless=True, no_viewport=True, args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            configure(page)
            page.goto(args.url)
            page.wait_for_load_state('networkidle')
            page.screenshot(path=str(output / 'initial.png'))
            run(page, worker, context.new_cdp_session(page), output)
            assert not errors, errors
            print('PASS: no console/page errors; isolated profile removed on exit', flush=True)
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            print('Browser errors:', errors, flush=True)
            raise
        finally:
            context.close()


if __name__ == '__main__':
    main()
