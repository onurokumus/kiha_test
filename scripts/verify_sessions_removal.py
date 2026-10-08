"""Quiet browser restoration after removing the Sessions feature.

Run against the built frontend: python -X utf8 scripts/verify_sessions_removal.py
All API traffic uses the read-only in-memory scatter-reload fixture. Browser
profiles/storage and downloads are isolated; no backend or user data is touched.
"""
import argparse
import copy
import csv
import json
from pathlib import Path
import tempfile

from playwright.sync_api import expect, sync_playwright
from verify_browser_zoom import capture_browser_view, set_browser_zoom
from verify_scatter_reload import Fixture, KEY, FAST, SLOW, SOURCES, X, Y, reload_data, stored


def baseline():
    return dict(version=1, sources=copy.deepcopy(SOURCES), currentTest=SLOW,
                xAxis=X, yAxis=Y, axesUserSet=True,
                selections=[dict(test=SLOW, tpId=1, hidden=False, color='#c45520')],
                plotConfigs=[Y], plotsUserEdited=True, plotDensity='single',
                filterState=dict(tpKeys=[], labels=[], parameterFilters=[]))


def configure(page, state):
    settings = dict(scatterX=X, scatterY=Y, clustering=False, gridColumns=[Y], defaultViewMode='tp')
    page.add_init_script(f"""if (!sessionStorage.getItem('removal-fixture-seeded')) {{
        sessionStorage.setItem('removal-fixture-seeded', '1'); localStorage.clear();
        localStorage.setItem('ptt.settings.v1', {json.dumps(json.dumps(settings))});
        {f'localStorage.setItem({json.dumps(KEY)}, {json.dumps(json.dumps(state))});' if state is not None else ''}
    }}""")
    fixture = Fixture()
    page.route('**/api/**', fixture.route)
    return fixture


def quiet(page):
    expect(page.get_by_role('button', name='Sessions', exact=True)).to_have_count(0)
    expect(page.get_by_role('dialog', name='Analysis sessions', exact=True)).to_have_count(0)
    expect(page.get_by_label('Session recovery', exact=True)).to_have_count(0)
    assert 'session recovery' not in page.locator('body').inner_text().lower()
    expect(page.get_by_role('button', name='Import CSV', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='More', exact=True)).to_be_visible()


def wait_stored(page, active, ids):
    page.wait_for_function("""({key, active, ids}) => {
        const s = JSON.parse(localStorage.getItem(key));
        return s?.currentTest === active && JSON.stringify(s.selections.map(p=>p.tpId)) === JSON.stringify(ids)
            && Array.isArray(s.sources) && s.sources.every(p=>p.id);
    }""", arg=dict(key=KEY, active=active, ids=ids))


def cases():
    yield 'fresh', None, FAST, [], False
    yield 'compatible', baseline(), SLOW, [1], False
    renamed = baseline()
    renamed['currentTest'] = 'Old_schema'
    renamed['selections'][0]['test'] = 'Old_schema'
    renamed['filterState']['tpKeys'] = ['Old_schema:1']
    next(source for source in renamed['sources'] if source['name'] == SLOW)['name'] = 'Old_schema'
    yield 'renamed', renamed, SLOW, [1], False
    changed = baseline()
    next(source for source in changed['sources'] if source['name'] == SLOW)['revision'] = 'old-samples'
    changed.update(mainZoom=[0, 100, 0, 100], timeZoom=[0.1, 0.5], fullRange=[0.1, 0.5])
    yield 'changed-samples', changed, SLOW, [1], True
    points = baseline()
    points['selections'].append(dict(test=SLOW, tpId=2, hidden=False))
    points['filterState']['tpKeys'] = [SLOW + ':1', SLOW + ':2']
    next(source for source in points['sources'] if source['name'] == SLOW)['test_points'][0]['revision'] = 'old-interval'
    points['mainZoom'] = [0, 100, 0, 100]
    yield 'changed-point', points, SLOW, [2], True
    missing = baseline()
    missing['currentTest'] = 'Missing_test'
    missing['selections'].insert(0, dict(test='Missing_test', tpId=9, hidden=False))
    missing['sources'].append(dict(name='Missing_test', id='missing-id', revision='old', test_points=[]))
    yield 'missing-source', missing, FAST, [1], True
    replaced = baseline()
    next(source for source in replaced['sources'] if source['name'] == SLOW)['id'] = 'different-dataset'
    yield 'same-name-replacement', replaced, FAST, [], True
    legacy = baseline()
    del legacy['sources']
    yield 'legacy-name-only', legacy, FAST, [], True
    ambiguous = baseline()
    ambiguous['sources'].append(copy.deepcopy(next(source for source in ambiguous['sources'] if source['name'] == SLOW)))
    yield 'ambiguous-reference', ambiguous, FAST, [], True


def run_cases(browser, url, output, report):
    for name, state, active, ids, reset_ranges in cases():
        context = browser.new_context(viewport=dict(width=1440, height=900), accept_downloads=True)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        configure(page, state)
        try:
            page.goto(url)
            page.wait_for_load_state('networkidle')
            quiet(page)
            wait_stored(page, active, ids)
            recovered = stored(page)
            if reset_ranges:
                assert all(recovered[key] is None for key in ('mainZoom', 'timeZoom', 'fullRange')), recovered
            if name == 'renamed':
                assert recovered['filterState']['tpKeys'] == [SLOW + ':1']
                assert recovered['selections'][0]['color'] == '#c45520'
            if name == 'changed-point':
                assert recovered['filterState']['tpKeys'] == [SLOW + ':2']
            expect(page.locator('.u-over')).to_have_count(1 if ids else 0)
            # A new choice must autosave without any review/dismissal action.
            if not ids:
                page.get_by_role('group', name='Plot layout', exact=True).get_by_role('button', name='1', exact=True).click()
                page.locator('.recharts-scatter-symbol').first.click()
                wait_stored(page, active, [1])
                ids = [1]
            page.reload()
            page.wait_for_load_state('networkidle')
            quiet(page)
            wait_stored(page, active, ids)
            expect(page.locator('.u-over')).to_have_count(1)
            page.screenshot(path=str(output / f'{name}.png'))
            assert not errors, errors
            report.append(name + ': quiet startup, compatible sources, automatic save and reload')
            print('PASS:', report[-1], flush=True)
        except Exception:
            page.screenshot(path=str(output / f'{name}-failure.png'))
            print(page.locator('body').aria_snapshot()[-12000:], flush=True)
            raise
        finally:
            context.close()


def run_desktop(playwright, url, output, report):
    with tempfile.TemporaryDirectory(prefix='ptt-sessions-removal-') as temporary:
        directory = Path(temporary)
        extension = directory / 'extension'
        extension.mkdir()
        (extension / 'manifest.json').write_text(json.dumps(dict(
            manifest_version=3, name='Sessions removal zoom check', version='1.0',
            permissions=['tabs'], background=dict(service_worker='background.js'))))
        (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
        context = playwright.chromium.launch_persistent_context(
            str(directory / 'profile'), channel='chromium', headless=True, no_viewport=True,
            args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}',
                  '--window-size=1440,900'])
        page = context.pages[0]
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        fixture = configure(page, baseline())
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(url)
            page.wait_for_load_state('networkidle')
            quiet(page)
            wait_stored(page, SLOW, [1])
            snapshot = stored(page)
            fixture.fail_next_catalog = True
            reload_data(page)
            expect(page.get_by_role('heading', name='Unable to verify analysis sources', exact=True)).to_be_visible()
            page.wait_for_timeout(700)
            assert stored(page) == snapshot, 'Failed catalog request overwrote the last good browser state'
            page.get_by_role('button', name='Try again', exact=True).click()
            page.wait_for_load_state('networkidle')
            quiet(page)
            wait_stored(page, SLOW, [1])
            report.append('source outage retains saved state; retry restores analysis without review')
            for factor in (1.25, 1.5):
                set_browser_zoom(worker, page, factor)
                quiet(page)
                expand = page.get_by_role('button', name=f'Expand {Y}', exact=True)
                expand.focus()
                page.keyboard.press('Enter')
                minimize = page.get_by_role('button', name=f'Minimize {Y}', exact=True)
                expect(minimize).to_be_visible()
                expect(page.locator('.u-over')).to_be_visible()
                minimize.focus()
                page.keyboard.press('Enter')
                expect(expand).to_be_visible()
                more = page.get_by_role('button', name='More', exact=True)
                more.focus()
                page.keyboard.press('Enter')
                page.get_by_role('button', name='Export scatter plot', exact=True).click()
                dialog = page.get_by_role('dialog', name='Export scatter plot', exact=True)
                expect(dialog).to_be_visible()
                with page.expect_download() as downloading:
                    dialog.get_by_role('button', name='Download CSV', exact=True).click()
                path = output / f'scatter-{factor}.csv'
                downloading.value.save_as(path)
                with path.open(encoding='utf-8-sig', newline='') as handle:
                    assert len(list(csv.DictReader(handle))) == 3
                dialog.get_by_role('button', name='Close export', exact=True).click()
                expect(more).to_be_focused()
                capture_browser_view(context.new_cdp_session(page), output / f'zoom-{factor}.png')
                report.append(f'{factor * 100:g}% actual browser zoom: maximize/restore, keyboard menu, CSV download, focus return')
            set_browser_zoom(worker, page, 1)
            page.set_viewport_size(dict(width=960, height=500))
            quiet(page)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.get_by_role('switch', name='Dark mode', exact=True).click()
            quiet(page)
            page.screenshot(path=str(output / 'narrow-dark.png'))
            report.append('960x500 desktop resize and dark theme retain usable header/actions')
            assert not errors, errors
        finally:
            context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8087/ptt/')
    args = parser.parse_args()
    output = Path(tempfile.gettempdir()) / 'ptt-sessions-removal'
    output.mkdir(exist_ok=True)
    report = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                run_cases(browser, args.url, output, report)
            finally:
                browser.close()
            run_desktop(playwright, args.url, output, report)
    finally:
        (output / 'results.json').write_text(json.dumps(dict(checks=report), indent=2), encoding='utf-8')
    print(f'PASS: {len(report)} browser groups; isolated GET-only fixtures. Evidence: {output}', flush=True)


if __name__ == '__main__':
    main()
