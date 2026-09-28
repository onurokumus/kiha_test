"""Regression for More > Reload data with staggered, disjoint test schemas.

Run: python -X utf8 scripts/verify_scatter_reload.py --url http://127.0.0.1:8087/ptt/
Uses an isolated Chromium context and read-only mocked API fixtures. No backend
or saved user browser profile is used. Evidence goes to ignored data/verification.
"""
import argparse
import json
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import expect, sync_playwright


KEY = 'ptt.analysis-session.v1'
FAST = 'Fast_schema'
SLOW = 'Slow_schema'
COLS = {FAST: ['unusable_a', 'unusable_b'], SLOW: ['thrust_n', 'torque_nm']}
X, Y = COLS[SLOW]
POINTS = [
    {'id': i, 'name': f'Point {i}', 'label': 'steady', 'start_s': (i - 1) * 2, 'end_s': i * 2}
    for i in range(1, 4)
]
SOURCES = [
    {'name': name, 'id': f'reload-fixture-{name}', 'revision': 'fixture-v1',
     'status': 'ready', 'columns': columns,
     'test_points': [{'id': point['id'], 'revision': f"point-{point['id']}"}
                     for point in (POINTS if name == SLOW else [])]}
    for name, columns in COLS.items()
]


class Fixture:
    def __init__(self):
        self.stagger = False
        self.held_catalog = []
        self.held_meta = []
        self.hold_stats = False
        self.held_stats = []
        self.fail_next_catalog = False
        self.requests = []

    @staticmethod
    def info(name):
        return {'name': name, 'status': 'ready', 'n_rows': 600, 'fs_hz': 100,
                'duration_s': 6, 'n_columns': 3, 'source_file': f'{name}.csv',
                'created_at': '2026-09-28T08:00:00Z'}

    def route(self, route):
        url = urlparse(route.request.url)
        path = unquote(url.path).split('/api', 1)[1]
        query = parse_qs(url.query)
        assert route.request.method == 'GET', f'Unexpected API write: {route.request.method} {path}'
        self.requests.append(path)
        if path == '/settings/defaults':
            body = {'settings': None}
        elif path == '/analysis-sources':
            if self.fail_next_catalog:
                self.fail_next_catalog = False
                route.fulfill(status=503, json={'detail': 'Injected reload identity outage'})
                return
            body = {'version': 1, 'sources': SOURCES}
            if self.stagger:
                self.held_catalog.append((route, body))
                return
        elif path == '/tests':
            body = [self.info(name) for name in COLS]
        elif path == '/components':
            body = {'version': 1, 'components': []}
        elif path == '/trash':
            body = {'entries': [], 'retention_seconds': 3600}
        elif path.startswith('/tests/'):
            parts = path.split('/')
            name = parts[2]
            assert name in COLS, path
            if len(parts) == 3:
                body = {**self.info(name), 'columns': ['time_s', *COLS[name]], 'time_column': 'time_s', 't_start': 0}
                if self.stagger and name == SLOW:
                    self.held_meta.append((route, body))
                    return
            elif path.endswith('/testpoints'):
                body = {'version': 1, 'test': name, 'test_points': POINTS if name == SLOW else []}
            elif path.endswith('/tp_stats'):
                column = query['col'][0]
                assert column in COLS[name], (name, column)
                # The fast schema legitimately has no test points. Falling
                # back to its columns recreates the reported empty scatter.
                body = [{**point, 'mean': point['id'] * (10 if column == X else 3) if name == SLOW else None,
                         'min': 0 if name == SLOW else None, 'max': 50 if name == SLOW else None,
                         'n': 200, 'n_valid': 200 if name == SLOW else 0}
                        for point in (POINTS if name == SLOW else [])]
                if self.hold_stats:
                    # Recognizably stale values must not replace fresh values
                    # when an older HTTP response arrives after the next reload.
                    body = [{**point, 'mean': point['mean'] + 9000} for point in body]
                    self.held_stats.append((route, body))
                    return
            elif '/testpoints/' in path and path.endswith('/data'):
                point = POINTS[int(parts[-2]) - 1]
                times = [i / 100 for i in range(200)]
                body = {'test': name, 'test_point': point, 'mode': 'raw', 'level': 1,
                        'n_raw': 200, 'i0': 0, 'i1': 200, 'point_budget': 1500,
                        'time_origin_s': point['start_s'], 'duration_s': 2,
                        'series': {col: {'t': times, 'y': [10 + t for t in times]}
                                   for col in query['cols'][0].split(',')}}
            else:
                raise AssertionError(f'Unexpected API request: {path}')
        else:
            raise AssertionError(f'Unexpected API request: {path}')
        route.fulfill(status=200, json=body)

    def configure(self, page):
        settings = {'scatterX': X, 'scatterY': Y, 'clustering': False, 'gridColumns': [Y], 'defaultViewMode': 'tp'}
        session = {'version': 1, 'sources': SOURCES, 'currentTest': SLOW,
                   'xAxis': X, 'yAxis': Y, 'axesUserSet': True,
                   'selections': [{'test': SLOW, 'tpId': 1, 'hidden': False}],
                   'plotConfigs': [Y], 'plotsUserEdited': True, 'plotDensity': 'single'}
        page.add_init_script(f"""localStorage.clear();
            localStorage.setItem('ptt.settings.v1', {json.dumps(json.dumps(settings))});
            localStorage.setItem({json.dumps(KEY)}, {json.dumps(json.dumps(session))});""")
        page.route('**/api/**', self.route)

    @staticmethod
    def release(held):
        pending = held[:]
        held.clear()
        for route, body in pending:
            route.fulfill(status=200, json=body)


def stored(page):
    return page.evaluate('key => JSON.parse(localStorage.getItem(key))', KEY)


def check_reloaded(page, label, axes=(X, Y)):
    expect(page.get_by_role('button', name='Scatter plot X axis', exact=True)).to_contain_text(axes[0])
    expect(page.get_by_role('button', name='Scatter plot Y axis', exact=True)).to_contain_text(axes[1])
    expect(page.locator('.recharts-scatter-symbol')).to_have_count(3)
    expect(page.get_by_text('No comparable points', exact=True)).to_have_count(0)
    expect(page.locator('.u-over')).to_have_count(1)
    page.wait_for_function('''({key, axes}) => {
        const session = JSON.parse(localStorage.getItem(key));
        return session.xAxis === axes[0] && session.yAxis === axes[1] &&
            session.selections.length === 1 && session.selections[0].test === 'Slow_schema';
    }''', arg={'key': KEY, 'axes': axes})
    print(f'PASS: {label}: chosen axes, three scatter points and selected trace survive', flush=True)


def reload_data(page):
    page.get_by_role('button', name='More', exact=True).click()
    page.get_by_role('button', name=re.compile('Reload data')).click()


def wait_for_held(page, pending, message):
    deadline = time.monotonic() + 10
    while not pending and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert pending, message


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8087/ptt/')
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / 'data/verification/scatter-reload'
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        errors = []
        console_errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: console_errors.append(message.text) if message.type == 'error' else None)
        fixture = Fixture()
        fixture.configure(page)
        try:
            page.goto(args.url)
            page.wait_for_load_state('networkidle')
            check_reloaded(page, 'initial fixture')
            for cycle in range(3):
                fixture.stagger = True
                reload_data(page)
                # Route holds make the race deterministic: the source catalog
                # trails the list, then the chosen schema trails the fast one.
                wait_for_held(page, fixture.held_catalog, 'Reload must refresh source identities')
                # Let a prematurely started fast metadata response render while
                # the catalog is still held; the fixed loader waits instead.
                page.wait_for_timeout(250)
                Fixture.release(fixture.held_catalog)
                wait_for_held(page, fixture.held_meta, 'Chosen schema was not requested again')
                page.wait_for_timeout(250)
                Fixture.release(fixture.held_catalog)
                fixture.stagger = False
                Fixture.release(fixture.held_meta)
                page.wait_for_load_state('networkidle')
                page.screenshot(path=str(output / f'reload-{cycle + 1}.png'))
                check_reloaded(page, f'staggered reload {cycle + 1}')
            fixture.hold_stats = True
            reload_data(page)
            wait_for_held(page, fixture.held_stats, 'Expected pending statistics to overlap reload')
            fixture.hold_stats = False
            reload_data(page)
            check_reloaded(page, 'reload while previous statistics are pending')
            axis_ticks = page.locator('.recharts-xAxis').text_content()
            Fixture.release(fixture.held_stats)
            page.wait_for_load_state('networkidle')
            assert page.locator('.recharts-xAxis').text_content() == axis_ticks, 'Older statistics replaced fresh scatter values'
            check_reloaded(page, 'late obsolete statistics ignored')
            # Change the live workspace after startup, then immediately reload.
            # Capturing the startup session or waiting on old autosave is wrong.
            for axis, value in [('X', Y), ('Y', X)]:
                page.get_by_role('button', name=f'Scatter plot {axis} axis', exact=True).click()
                page.get_by_role('option', name=value, exact=True).click()
            reload_data(page)
            check_reloaded(page, 'new live axis choices preserved', axes=(Y, X))
            page.wait_for_load_state('networkidle')
            snapshot = stored(page)
            fixture.fail_next_catalog = True
            reload_data(page)
            expect(page.get_by_role('heading', name='Unable to verify analysis sources', exact=True)).to_be_visible()
            # Outwait the autosave debounce to prove failure does not overwrite
            # the last usable workspace, rather than merely checking too early.
            page.wait_for_timeout(700)
            assert stored(page) == snapshot, 'Failed reload overwrote the saved workspace'
            page.get_by_role('button', name='Try again', exact=True).click()
            check_reloaded(page, 'failed catalog reload and retry', axes=(Y, X))
            assert not errors, errors
            assert len(console_errors) == 1 and '503' in console_errors[0], console_errors
            (output / 'results.json').write_text(json.dumps({'session': stored(page), 'requests': fixture.requests,
                'page_errors': errors, 'expected_console_errors': console_errors}, indent=2), encoding='utf-8')
        except Exception:
            page.screenshot(path=str(output / 'failure.png'))
            session = stored(page)
            (output / 'failure-session.json').write_text(json.dumps(session, indent=2), encoding='utf-8')
            print(json.dumps({'axes': [session.get('xAxis'), session.get('yAxis')],
                              'selections': session.get('selections'), 'errors': errors,
                              'console_errors': console_errors}, indent=2), flush=True)
            print(page.locator('body').aria_snapshot()[-12000:], flush=True)
            raise
        finally:
            context.close()
            browser.close()
    print('PASS: read-only scatter reload regression; no unexpected browser errors or dataset writes', flush=True)


if __name__ == '__main__':
    main()
