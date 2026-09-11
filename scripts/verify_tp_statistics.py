"""Live Phase 4b verification using isolated API data, servers and Chromium.

Global Python runs only stdlib/Playwright. Backend/native work is performed by
the project's Python 3.13 child, through real upload, split and stats endpoints.
"""
import argparse
import json
import math
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright
from verify_data_quality import servers, upload
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout, check_crosshair

ROOT = Path(__file__).resolve().parents[1]
LOAD, TINY, EMPTY, CONST = 'load_N', 'tiny_A', 'missing_N', 'constant_N'
LONG = 'force_' + 'long_sensor_identifier_' * 7 + 'N'
COLS = [LOAD, TINY, EMPTY, CONST, LONG]
POINT_NAME = 'Operating point ' + 'long detail ' * 10


def make_data(request):
    source = [1000.0 if i == 1000 else (i % 10 + (20 if i >= 2010 else 0)) for i in range(5000)]
    source[15] = None
    lines = ['time,' + ','.join(COLS)]
    for i, value in enumerate(source):
        lines.append(f'{i/100:.2f},{"" if value is None else value},{(i%5+1)*1e-10},{99.0 if i == 0 else ""},11.0,{i%10}.0')
    assert upload(request, 'alpha', '\n'.join(lines))['status'] == 'ready'
    for name, constant in [('beta', 22), ('unselected', 33)]:
        small = 'time,load_N,constant_N\n' + '\n'.join(
            f'{i/100:.2f},{i%10 + (20 if i >= 2010 else 0)}.0,{constant}.0' for i in range(5000))
        assert upload(request, name, small)['status'] == 'ready'
    for name in ['alpha', 'beta', 'unselected']:
        points = [{'id': 1, 'name': POINT_NAME, 'label': 'nominal', 'start_s': .1, 'end_s': 20.1},
                  {'id': 2, 'name': 'High load', 'label': 'high', 'start_s': 20.1, 'end_s': 40.1}]
        response = request.put(f'tests/{name}/testpoints', data={'version': 1, 'test': name, 'test_points': points})
        assert response.ok, response.text()
    valid = [value for value in source[10:2010] if value is not None]
    mean = math.fsum(valid) / len(valid)
    std = math.sqrt(math.fsum((value-mean)**2 for value in valid) / len(valid))
    stats = request.get('tests/alpha/tp_stats?col=load_N').json()[0]
    assert math.isclose(stats['summary']['mean'], mean, rel_tol=1e-12)
    assert math.isclose(stats['summary']['std_population'], std, rel_tol=1e-12)
    assert (stats['n'], stats['n_valid']) == (2000, 1999)
    assert (stats['summary']['i0'], stats['summary']['i1']) == (10, 2010)
    trace = request.get('tests/alpha/testpoints/1/data?cols=load_N&max_points=100').json()
    trace_values = [v for v in trace['series'][LOAD]['y'] if v is not None]
    assert trace['mode'] == 'envelope' and not math.isclose(sum(trace_values)/len(trace_values), mean, rel_tol=.01)
    return stats


def configure(page):
    settings = {'scatterX': LOAD, 'scatterY': CONST, 'clustering': False,
                'gridColumns': COLS[:4], 'defaultViewMode': 'tp'}
    session = {'version': 1, 'currentTest': 'alpha', 'xAxis': LOAD, 'yAxis': CONST,
               'axesUserSet': True, 'selections': [{'test': 'alpha', 'tpId': 1, 'hidden': False}],
               'plotConfigs': COLS[:4], 'plotsUserEdited': True, 'plotDensity': 'quad'}
    page.add_init_script(f'''if (!sessionStorage.getItem('stats-fixture')) {{
        localStorage.clear(); sessionStorage.setItem('stats-fixture','true');
        localStorage.setItem('ptt.settings.v1', {json.dumps(json.dumps(settings))});
        localStorage.setItem('ptt.analysis-session.v1', {json.dumps(json.dumps(session))});
    }}''')


def plot(page, col=LOAD):
    return page.get_by_role('group', name=f'{col} time plot', exact=True).first


def summary_button(page, col=LOAD):
    return plot(page, col).get_by_role('button', name=re.compile(f'^Statistics for {re.escape(col)}:'))


def details(page, col=LOAD):
    button = summary_button(page, col)
    button.focus()
    page.keyboard.press('Enter')
    panel = page.get_by_role('dialog', name=f'Statistics for {col}', exact=True)
    expect(panel).to_be_visible()
    expect(panel.get_by_role('button', name='Close statistics')).to_be_focused()
    return panel


def close_details(page, panel, col=LOAD):
    page.keyboard.press('Escape')
    expect(panel).not_to_be_visible()
    expect(summary_button(page, col)).to_be_focused()


def set_y(page, col, lower, upper):
    plot(page, col).get_by_role('button', name=f'Y axis for {col}', exact=True).click()
    panel = page.get_by_role('dialog', name=f'Y axis for {col}', exact=True)
    panel.get_by_label('Minimum', exact=True).fill(str(lower))
    panel.get_by_label('Maximum', exact=True).fill(str(upper))
    panel.get_by_role('button', name='Apply range').click()


def check_table(panel, expected):
    row = panel.locator('tbody tr').first
    assert math.isclose(float(row.locator('td').nth(0).inner_text()), expected['summary']['mean'], rel_tol=1e-10)
    assert math.isclose(float(row.locator('td').nth(1).inner_text()), expected['summary']['std_population'], rel_tol=1e-10)
    expect(row).to_contain_text('1999 / 2000')
    expect(row).to_contain_text('1 excluded')
    expect(row).to_contain_text('Rows [10, 2010)')


def run_checks(web, api, temporary, output):
    extension = temporary / 'extension'
    extension.mkdir()
    (extension/'manifest.json').write_text(json.dumps({'manifest_version':3, 'name':'TP statistics verification', 'version':'1.0', 'permissions':['tabs'], 'background':{'service_worker':'background.js'}}), encoding='utf-8')
    (extension/'background.js').write_text('chrome.runtime.onInstalled.addListener(() => {});', encoding='utf-8')
    with sync_playwright() as p:
        request = p.request.new_context(base_url=api + '/')
        expected = make_data(request)
        context = p.chromium.launch_persistent_context(str(temporary/'profile'), channel='chromium', headless=True, no_viewport=True,
            args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1440,1000'])
        page = context.pages[0]
        errors, stat_requests = [], []
        page.on('pageerror', lambda error: errors.append(error.stack or str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
        page.on('request', lambda req: stat_requests.append(req.url) if '/tp_stats?' in req.url else None)
        failed_url = api + '/tests/alpha/tp_stats?col=tiny_A'
        page.route(failed_url, lambda route: route.fulfill(status=503, json={'detail':'statistics fixture failure'}))
        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            configure(page)
            page.goto(web)
            page.wait_for_load_state('networkidle')
            expect(summary_button(page)).to_have_text('Mean 5')
            panel = details(page)
            check_table(panel, expected)
            region = panel.get_by_role('region', name='Per-test-point statistics')
            region.focus()
            expect(region).to_be_focused()
            close_details(page, panel)
            before = summary_button(page).inner_text()
            print('PASS: real upload/saved TP/full-resolution API mean and population SD, exact rows/counts, keyboard details', flush=True)

            panel = details(page, TINY)
            expect(panel).to_contain_text('Could not load statistics')
            initial_failed_count = sum(url == failed_url for url in stat_requests)
            page.wait_for_timeout(400)
            assert sum(url == failed_url for url in stat_requests) == initial_failed_count
            page.unroute(failed_url)
            panel.get_by_role('button', name='Retry statistics').focus()
            page.keyboard.press('Enter')
            expect(panel).to_contain_text('3e-10')
            expect(panel.get_by_role('button', name='Close statistics')).to_be_focused()
            close_details(page, panel, TINY)
            expect(summary_button(page, TINY)).to_contain_text('Mean 3e-10')
            panel = details(page, EMPTY)
            expect(panel).to_contain_text('No finite samples')
            expect(panel).to_contain_text('2000 excluded')
            close_details(page, panel, EMPTY)
            panel = details(page, CONST)
            assert panel.locator('tbody tr td').nth(1).inner_text() == '0'
            close_details(page, panel, CONST)
            for url in stat_requests:
                parsed = urlparse(url)
                col = parse_qs(parsed.query)['col'][0]
                if '/tests/unselected/' in url or '/tests/beta/' in url:
                    assert col in [LOAD, CONST], url
            print('PASS: failure stops retry loop, keyboard retry recovers; tiny/constant/all-missing data; added columns restricted to selected tests', flush=True)

            set_y(page, LOAD, 0, 12)
            over = plot(page).locator('.u-over')
            over.hover()
            page.mouse.wheel(0, -120)
            page.wait_for_timeout(350)
            assert summary_button(page).inner_text() == before
            plot(page).get_by_role('button', name=f'Expand {LOAD}', exact=True).click()
            plot(page).get_by_title('Filter this plot only', exact=True).select_option('moving_avg')
            expect(plot(page).get_by_text('filtered', exact=True)).to_be_visible()
            expect(summary_button(page)).to_have_text('Original mean 5')
            panel = details(page)
            expect(panel).to_contain_text('The plot is filtered')
            check_table(panel, expected)
            close_details(page, panel)
            plot(page).get_by_role('button', name='Clear', exact=True).click()
            plot(page).get_by_role('button', name=f'Minimize {LOAD}', exact=True).click()
            page.get_by_role('button', name='Reset zoom', exact=True).click()
            assert summary_button(page).inner_text() == before
            print('PASS: X/Y zoom, maximize, real filtering/clear keep full original TP statistics and explicit source label', flush=True)

            # Add alpha's second point from its known scatter coordinates.
            circles = page.locator('.recharts-scatter-symbol')
            circles.nth(1).click()
            expect(summary_button(page)).to_have_text('Means 5…24.5')
            panel = details(page)
            expect(panel).to_contain_text('test points are not pooled')
            expect(panel.locator('tbody tr')).to_have_count(2)
            close_details(page, panel)
            page.get_by_role('button', name=re.compile('2 of .*Expand selection tray')).click()
            page.get_by_role('button', name='Hide High load from alpha', exact=True).click()
            expect(summary_button(page)).to_have_text('Mean 5')
            page.get_by_role('button', name='Show High load from alpha', exact=True).click()
            expect(summary_button(page)).to_have_text('Means 5…24.5')
            page.get_by_role('button', name=re.compile('2 of .*Collapse selection tray')).click()
            # beta has no tiny_A; show that explicitly rather than borrowing alpha's mean.
            circles.nth(2).click()
            panel = details(page, TINY)
            expect(panel).to_contain_text('Variable not in this test')
            expect(summary_button(page, TINY)).to_contain_text('partial')
            close_details(page, panel, TINY)
            print('PASS: multi-TP means are separate, visibility updates, cross-test missing-column partial status', flush=True)

            cdp = context.new_cdp_session(page)
            window_id = cdp.send('Browser.getWindowForTarget')['windowId']
            dpr = page.evaluate('devicePixelRatio')
            for width, factor in [(1100, 1), (1440, 1.25), (1440, 1.5)] * 3:
                cdp.send('Browser.setWindowBounds', {'windowId':window_id,'bounds':{'width':width,'height':1000}})
                set_browser_zoom(worker, page, factor)
                page.wait_for_function('dpr => Math.abs(devicePixelRatio-dpr)<.001', arg=dpr*factor)
                wait_for_chart_layout(page)
                panel = details(page)
                bounds = panel.bounding_box()
                viewport = page.evaluate('({w:innerWidth,h:innerHeight})')
                assert bounds['x'] >= 0 and bounds['y'] >= 0 and bounds['x']+bounds['width'] <= viewport['w']+1 and bounds['y']+bounds['height'] <= viewport['h']+1
                assert panel.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
                # A keyboard-opened detail panel must cover any scatter hover
                # card left under the stationary pointer after resize.
                for tooltip in page.locator('.scatter-tooltip').all():
                    assert panel.evaluate('el => +getComputedStyle(el).zIndex') > tooltip.evaluate('el => +getComputedStyle(el).zIndex')
                capture_browser_view(cdp, output/f'details-{width}-{factor}.png')
                close_details(page, panel)
                check_crosshair(page, f'stats grid {width}/{factor}')
                plot(page).get_by_role('button', name=f'Expand {LOAD}', exact=True).focus()
                page.keyboard.press('Enter')
                check_crosshair(page, f'stats maximized {factor}')
                plot(page).get_by_role('button', name=f'Minimize {LOAD}', exact=True).focus()
                page.keyboard.press('Enter')
            set_browser_zoom(worker, page, 1)
            wait_for_chart_layout(page)
            print('PASS: long TP labels, desktop resize, real 125/150% zoom, keyboard maximize/restore and crosshairs', flush=True)

            # Switch variable while a details view has been used; prior values
            # must not leak into a new column. Then exercise a legacy response.
            page.get_by_role('button', name='Edit plots', exact=True).click()
            plot(page).get_by_role('button', name='Plot variable', exact=True).click()
            page.get_by_role('option', name=LONG, exact=True).click()
            panel = details(page, LONG)
            expect(panel).to_contain_text(LONG)
            expect(panel).to_contain_text('Variable not in this test')
            close_details(page, panel, LONG)
            page.get_by_role('button', name='Editing plots', exact=True).click()
            def legacy(route):
                response = route.fetch()
                body = response.json()
                for row in body:
                    row.pop('summary', None)
                route.fulfill(response=response, json=body)
            page.route(failed_url, legacy)
            page.wait_for_timeout(400)
            page.reload()
            page.wait_for_load_state('networkidle')
            panel = details(page, TINY)
            expect(panel).to_contain_text('Older statistics response')
            expect(panel).to_contain_text('standard deviation and exact row bounds are unavailable')
            close_details(page, panel, TINY)
            print('PASS: variable change and legacy API fallback with explicit precision/SD limits', flush=True)
            unexpected = [error for error in errors if '503' not in error and 'statistics fixture failure' not in error]
            assert not unexpected, unexpected
            (output/'results.json').write_text(json.dumps({'source_stats':expected,'stats_request_count':len(stat_requests),'unexpected_browser_errors':unexpected},indent=2),encoding='utf-8')
        except Exception:
            page.screenshot(path=str(output/'failure.png'), full_page=True)
            print('Browser errors:', errors, flush=True)
            raise
        finally:
            context.close()
            request.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-port', type=int, default=3110)
    parser.add_argument('--backend-port', type=int, default=8110)
    args = parser.parse_args()
    output = ROOT/'data/verification/tp-statistics'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ptt-statistics-') as directory:
        temporary = Path(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, _dataset):
            run_checks(web, api, temporary, output)
    print('PASS: isolated data/profile removed; both owned servers stopped', flush=True)


if __name__ == '__main__':
    main()
