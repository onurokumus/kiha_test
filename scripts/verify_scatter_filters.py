"""Scatter filter regression: real input, isolated GET fixtures and browser zoom.
Run Vite separately, then python scripts/verify_scatter_filters.py --url http://127.0.0.1:3361
"""
import argparse
import json
import re
import tempfile
import traceback
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
import verify_rendering as fixture
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout

TEST, X, Y = 'Scatter filter regression', 'thrust_n', 'torque_nm'
COLS = [X, Y, 'rpm', 'temperature_c', 'current_a']
POINTS = [dict(id=i+1, name=n, label=l, start_s=i*10, end_s=(i+1)*10)
          for i, (n, l) in enumerate([('Alpha low', 'Cold'), ('Alpha nominal', 'Hot'),
                                      ('Beta high', 'Hot'), ('Gamma unlabelled', '')])]
fixture.TEST, fixture.X, fixture.Y, fixture.COLS, fixture.POINTS = TEST, X, Y, COLS, POINTS
fixture.MEANS = {X: [10, 20, 30, 40], Y: [15, 25, 35, 45]}
fixture.SOURCE = {**fixture.SOURCE, 'name': TEST, 'columns': COLS,
    'test_points': [{'id': p['id'], 'revision': f"point-{p['id']}"} for p in POINTS]}

def configure(page, missing=False):
    settings = dict(scatterX=X, scatterY=Y, clustering=False, gridColumns=COLS[1:], defaultViewMode='tp')
    session = dict(version=1, sources=[fixture.SOURCE], currentTest=TEST, xAxis=X, yAxis=Y,
        axesUserSet=True, selections=[dict(test=TEST, tpId=1, hidden=False)], plotConfigs=COLS[1:],
        plotsUserEdited=True, plotDensity='quad')
    if missing:
        session['filterState'] = dict(tpKeys=[], labels=[], parameterFilters=[
            dict(id='missing', column='removed_signal', mode='mean', min=1, max=None)])
    page.add_init_script(f"""if(!sessionStorage.getItem('filters-seeded')){{
        sessionStorage.setItem('filters-seeded','1');localStorage.clear();
        localStorage.setItem('ptt.settings.v1',{json.dumps(json.dumps(settings))});
        localStorage.setItem('ptt.analysis-session.v1',{json.dumps(json.dumps(session))});}}""")
    page.route('**/api/**', fixture.mock_api)

def drawer(p): return p.get_by_role('region', name='Scatter filters', exact=True)
def trigger(p): return p.locator('.analyze-scatter-panel').get_by_role('button', name=re.compile('Filters'))
def column(p, i=1): return p.get_by_role('button', name=f'Column for parameter filter {i}', exact=True)
def row(p, i=1): return column(p, i).locator('xpath=ancestor::div[button[starts-with(@aria-label,"Remove parameter filter")]][1]')
def open_drawer(p):
    if not drawer(p).is_visible(): trigger(p).click()
    expect(drawer(p)).to_be_visible()
def section(p, name, opened=True):
    b = drawer(p).get_by_role('button', name=re.compile('^'+re.escape(name)))
    if b.get_attribute('aria-expanded') != str(opened).lower(): b.click()
def add(p):
    open_drawer(p)
    section(p, 'Tests & test points', False)
    drawer(p).get_by_role('button', name='+ Add', exact=True).click()
    expect(column(p)).to_be_visible()
def choose(p, value, i=1):
    column(p, i).click()
    p.get_by_role('option', name=value, exact=True).click()
    expect(drawer(p)).to_be_visible()
    expect(column(p, i)).to_contain_text(value)
def count(p, n):
    expect(p.locator('.analyze-scatter-panel [aria-live="polite"][aria-atomic="true"]')).to_have_text(f'{n} / 4 points')
    expect(p.locator('.recharts-scatter-symbol')).to_have_count(n)
def bounds(p, lo, hi, i=1):
    row(p, i).get_by_role('spinbutton', name='Minimum', exact=True).fill('' if lo is None else str(lo))
    row(p, i).get_by_role('spinbutton', name='Maximum', exact=True).fill('' if hi is None else str(hi))
    row(p, i).get_by_role('combobox', name='Aggregation', exact=True).focus()
def mode(p, value):
    s = row(p).get_by_role('combobox', name='Aggregation', exact=True)
    s.focus()
    s.press('Home')
    for _ in range(['mean', 'min', 'max', 'any'].index(value)): s.press('ArrowDown')
    s.press('Enter')
    expect(s).to_have_value(value)
def clear(p):
    p.get_by_role('button', name='Clear all scatter filters', exact=True).click()
    count(p, 4)
def wait_saved(p, expr):
    p.wait_for_function("() => {const s=JSON.parse(localStorage.getItem('ptt.analysis-session.v1'));return s&&s.filterState&&("+expr+")(s.filterState);}")

def mouse_and_parameters(p, **_):
    add(p)
    clear(p)
    expect(column(p)).to_have_count(0)
    add(p)
    choose(p, Y)
    column(p).click()
    search = p.get_by_role('combobox', name='Search filter columns...', exact=True)
    search.click()
    expect(drawer(p)).to_be_visible()
    search.fill('not present')
    expect(p.get_by_text('No matching options', exact=True)).to_be_visible()
    p.get_by_role('button', name='Clear search', exact=True).click()
    expect(search).to_have_value('')
    search.fill('RPM')
    p.get_by_role('option', name='rpm', exact=True).click()
    expect(column(p)).to_contain_text('rpm')
    choose(p, X)
    bounds(p, 16, 26)
    for agg, n in [('mean', 1), ('min', 2), ('max', 2), ('any', 3)]:
        mode(p, agg)
        count(p, n)
    bounds(p, 17, 19)
    count(p, 1)
    mode(p, 'mean')
    count(p, 0)
    bounds(p, 30, 10)
    count(p, 0)
    expect(drawer(p).get_by_text('Minimum must not exceed maximum.', exact=True)).to_be_visible()
    mode(p, 'any')
    count(p, 0)
    mode(p, 'mean')
    bounds(p, None, 20)
    count(p, 2)
    bounds(p, 16, 35)
    count(p, 2)
    add(p)
    choose(p, Y, 2)
    bounds(p, 30, None, 2)
    count(p, 1)
    p.get_by_role('button', name='Remove parameter filter 1', exact=True).click()
    expect(column(p)).to_contain_text(Y)
    expect(column(p, 2)).to_have_count(0)
    count(p, 2)
    clear(p)
    expect(column(p)).to_have_count(0)

def tree_and_labels(p, **_):
    open_drawer(p)
    drawer(p).get_by_role('button', name='Expand', exact=True).click()
    parent = drawer(p).get_by_role('checkbox', name=f'{TEST} (4)', exact=True)
    parent.check()
    expect(parent).to_be_checked()
    count(p, 4)
    first = drawer(p).get_by_role('checkbox', name='Alpha low — Cold', exact=True)
    first.uncheck()
    count(p, 3)
    assert parent.evaluate('e=>e.indeterminate')
    clear(p)
    search = drawer(p).get_by_role('searchbox', name='Search tests and test points', exact=True)
    search.fill('alpha')
    subset = drawer(p).get_by_role('checkbox', name=f'{TEST} (2)', exact=True)
    subset.check()
    count(p, 2)
    expect(subset).to_be_checked()
    subset.uncheck()
    count(p, 4)
    search.fill('not present')
    expect(drawer(p).get_by_text('No matching tests or points.', exact=True)).to_be_visible()
    search.fill('')
    drawer(p).get_by_role('button', name='Collapse', exact=True).click()
    expect(first).to_have_count(0)
    drawer(p).get_by_role('button', name='Expand', exact=True).click()
    first.check()
    section(p, 'Labels')
    ls = drawer(p).get_by_role('searchbox', name='Search labels', exact=True)
    ls.fill('HOT')
    expect(drawer(p).get_by_role('checkbox', name='Cold', exact=True)).to_have_count(0)
    drawer(p).get_by_role('checkbox', name='Hot', exact=True).check()
    count(p, 0)
    first.uncheck()
    count(p, 2)
    ls.fill('')
    drawer(p).get_by_role('checkbox', name='Cold', exact=True).check()
    count(p, 3)
    ls.fill('not present')
    expect(drawer(p).get_by_text('No matching labels.', exact=True)).to_be_visible()
    clear(p)

def numeric_close(p, **_):
    for closing in ['escape', 'outside', 'close', 'trigger', 'section']:
        add(p)
        choose(p, X)
        row(p).get_by_role('spinbutton', name='Minimum', exact=True).fill('30')
        if closing == 'escape': p.keyboard.press('Escape')
        elif closing == 'outside':
            p.get_by_role('button', name='Edit plots', exact=True).click()
            p.keyboard.press('Escape')
        elif closing == 'close': drawer(p).get_by_role('button', name='Close filters', exact=True).click()
        elif closing == 'trigger': trigger(p).click()
        else: section(p, 'Parameters', False)
        count(p, 2)
        open_drawer(p)
        section(p, 'Parameters')
        expect(row(p).get_by_role('spinbutton', name='Minimum', exact=True)).to_have_value('30')
        clear(p)
    add(p)
    choose(p, X)
    low = row(p).get_by_role('spinbutton', name='Minimum', exact=True)
    low.press_sequentially('1e1')
    low.press('Tab')
    assert low.evaluate('e=>e.valueAsNumber') == 10
    count(p, 4)
    low.fill('-2.5')
    p.keyboard.press('Escape')
    open_drawer(p)
    expect(row(p).get_by_role('spinbutton', name='Minimum', exact=True)).to_have_value('-2.5')
    clear(p)

def keyboard_persistence(p, **_):
    add(p)
    column(p).focus()
    p.keyboard.press('ArrowDown')
    expect(p.get_by_role('combobox', name='Search filter columns...', exact=True)).to_be_focused()
    p.keyboard.press('Escape')
    expect(drawer(p)).to_be_visible()
    expect(column(p)).to_be_focused()
    expect(p.get_by_role('listbox', name='Column for parameter filter 1', exact=True)).to_have_count(0)
    p.keyboard.press('ArrowDown')
    p.keyboard.press('Tab')
    expect(row(p).get_by_role('combobox', name='Aggregation', exact=True)).to_be_focused()
    column(p).focus()
    p.keyboard.press('r')
    p.keyboard.press('p')
    p.keyboard.press('m')
    p.keyboard.press('Enter')
    expect(column(p)).to_contain_text('rpm')
    bounds(p, 15, 25)
    count(p, 2)
    p.keyboard.press('Escape')
    expect(drawer(p)).to_have_count(0)
    expect(trigger(p)).to_be_focused()
    wait_saved(p, "f=>f.parameterFilters.length===1&&f.parameterFilters[0].column==='rpm'&&f.parameterFilters[0].min===15&&f.parameterFilters[0].max===25")
    before = p.evaluate("JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).filterState")
    p.reload()
    p.wait_for_load_state('networkidle')
    count(p, 2)
    open_drawer(p)
    expect(column(p)).to_contain_text('rpm')
    expect(row(p).get_by_role('spinbutton', name='Minimum', exact=True)).to_have_value('15')
    assert p.evaluate("JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).filterState") == before
    clear(p)
    wait_saved(p, 'f=>!f.parameterFilters.length&&!f.tpKeys.length&&!f.labels.length')
    p.reload()
    p.wait_for_load_state('networkidle')
    count(p, 4)

def missing_column(p, **_):
    count(p, 0)
    open_drawer(p)
    expect(column(p)).to_contain_text('removed_signal (unavailable)')
    column(p).click()
    expect(p.get_by_role('option', name='removed_signal (unavailable)', exact=True)).to_be_disabled()
    p.get_by_role('option', name=X, exact=True).click()
    count(p, 4)
    clear(p)

def resize_zoom(p, worker, cdp, output):
    window = cdp.send('Browser.getWindowForTarget')['windowId']
    baseline_dpr = p.evaluate('devicePixelRatio')
    for width, height, zoom in [(1100, 800, 1), (1440, 1000, 1.25), (1440, 1000, 1.5)]:
        cdp.send('Browser.setWindowBounds', dict(windowId=window, bounds=dict(width=width, height=height)))
        set_browser_zoom(worker, p, zoom)
        p.wait_for_function('(factor)=>Math.abs(devicePixelRatio-factor)<.01', arg=baseline_dpr*zoom)
        wait_for_chart_layout(p)
        add(p)
        choose(p, X)
        bounds(p, 16, 26)
        count(p, 1)
        column(p).click()
        search = p.get_by_role('combobox', name='Search filter columns...', exact=True)
        search.click()
        b = search.locator('xpath=../..').bounding_box()
        v = p.evaluate('({width:innerWidth,height:innerHeight})')
        assert b['x']>=0 and b['y']>=0 and b['x']+b['width']<=v['width']+1 and b['y']+b['height']<=v['height']+1, (b, v)
        p.get_by_role('option', name=Y, exact=True).click()
        expect(column(p)).to_contain_text(Y)
        count(p, 1)
        capture_browser_view(cdp, output/f'filters-{width}-{round(zoom*100)}pct.png')
        clear(p)
        p.keyboard.press('Escape')
    set_browser_zoom(worker, p, 1)

CHECKS = [('mouse and parameter ranges', mouse_and_parameters), ('test tree and labels', tree_and_labels),
          ('immediate numeric close', numeric_close), ('keyboard and persistence', keyboard_persistence),
          ('missing saved column', missing_column), ('desktop resize and real browser zoom', resize_zoom)]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:3361')
    parser.add_argument('--only', help='Regular expression matching check names')
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1]/'data'/'verification'/'scatter-filters'
    output.mkdir(parents=True, exist_ok=True)
    passed, failures = [], []
    with tempfile.TemporaryDirectory(prefix='scatter-filters-') as temp, sync_playwright() as pw:
        tmp = Path(temp)
        extension = tmp/'extension'
        extension.mkdir()
        (extension/'manifest.json').write_text(json.dumps(dict(manifest_version=3, name='Scatter filter zoom checks',
            version='1.0', permissions=['tabs'], background=dict(service_worker='background.js'))))
        (extension/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
        context = pw.chromium.launch_persistent_context(str(tmp/'profile'), channel='chromium', headless=True, no_viewport=True,
            args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1440,1000'])
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        try:
            for name, check in CHECKS:
                if args.only and not re.search(args.only, name): continue
                p = context.new_page()
                p.set_default_timeout(7000)
                errors = []
                p.on('pageerror', lambda e: errors.append(str(e)))
                p.on('console', lambda m: errors.append(m.text) if m.type=='error' else None)
                configure(p, missing=name=='missing saved column')
                cdp = context.new_cdp_session(p)
                try:
                    p.goto(args.url)
                    p.wait_for_load_state('networkidle')
                    if name!='missing saved column': count(p, 4)
                    check(p, worker=worker, cdp=cdp, output=output)
                    assert not errors, errors
                    passed.append(name)
                    print('PASS:', name, flush=True)
                except Exception:
                    error = traceback.format_exc()
                    failures.append(dict(check=name, error=error, browser_errors=errors))
                    print('FAIL:', name, '\n'+error, flush=True)
                    capture_browser_view(cdp, output/('failure-'+re.sub(r'\W+', '-', name)+'.png'))
                finally:
                    p.close()
        finally:
            context.close()
    (output/'results.json').write_text(json.dumps(dict(passed=passed, failures=failures), indent=2))
    print(f'{len(passed)} groups passed; {len(failures)} failed; API fixture permits GET only.', flush=True)
    if failures: raise SystemExit(1)

if __name__=='__main__': main()
