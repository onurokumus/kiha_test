"""Native multi-flight time/filter/envelope browser regression checks.

Uses the disposable recording and hidden-server helpers from
verify_full_flight_comparison.py. Run with global Python/Playwright; all native
fixture/backend work uses the supplied Python 3.13 interpreter. Dataset writes
are blocked, generated samples are hashed before/after, and owned servers stop.
"""
import argparse
import sys, json, tempfile, re
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlparse, parse_qs
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from verify_full_flight_comparison import servers, A, B, C, COLS, PLOTS, KEY, hashes, instrument
from playwright.sync_api import sync_playwright, expect

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', '--backend-python', dest='backend_python', type=Path, default=Path('D:/okumus/kiha_test/backend/.venv/Scripts/python.exe'), help='Python 3.13 interpreter for native fixtures and the backend')
    parser.add_argument('--output', type=Path, default=Path(tempfile.gettempdir()) / 'ptt-flight-time-filters')
    parser.add_argument('--web-port', type=int, default=3127)
    parser.add_argument('--api-port', type=int, default=8027)
    args = parser.parse_args()
    out = args.output
    out.mkdir(exist_ok=True, parents=True)
    report = {'checks': [], 'errors': [], 'writes': []}

    def passed(s):
        report['checks'].append(s)
        print('PASS:', s, flush=True)
    with tempfile.TemporaryDirectory(prefix='ptt-flight-time-') as td:
        with servers(Path(td), out, args.backend_python, args.web_port, args.api_port) as (web, api, dataset):
            before = hashes(dataset, True)
            sources = json.load(urlopen(api + 'analysis-sources'))['sources']
            compare = {'flights': [{'test': n, 'color': c, 'hidden': False, 'offset': o} for n, c, o in [(A, '#d55e00', 0), (B, '#cc79a7', 1.5)]], 'timeBasis': 'elapsed'}
            base = {'version': 1, 'sources': sources, 'currentTest': A, 'xAxis': 'rpm', 'yAxis': 'thrust_n', 'axesUserSet': True, 'selections': [], 'plotConfigs': COLS, 'plotsUserEdited': True, 'plotDensity': 'single', 'expandedPlot': None, 'scatterCollapsed': True, 'viewMode': 'full', 'fullPlotMode': 'line', 'fullPlotExtraColumns': [['torque_nm']] + [[] for _ in range(8)], 'plotFilters': [{'kind': 'moving_avg', 'winS': '0.15'}], 'plotShowOriginal': [True], 'fullFlightComparison': compare}
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='chromium', headless=True)
                context = browser.new_context(viewport={'width': 1500, 'height': 1000})
                context.route('**/src/utils/uplotSync.ts*', instrument)
                requests = []

                def guard(route):
                    req = route.request
                    if '/data?' in req.url or '/filter?' in req.url:
                        requests.append({'url': req.url, 'q': parse_qs(urlparse(req.url).query)})
                    if req.method not in {'GET', 'HEAD', 'OPTIONS'}:
                        report['writes'].append(req.url)
                        route.abort()
                    else:
                        route.continue_()
                context.route('**/api/**', guard)
                context.add_init_script("if(!sessionStorage.getItem('qa-seeded')){sessionStorage.setItem('qa-seeded','1');localStorage.setItem(" + json.dumps(KEY) + ',' + json.dumps(json.dumps(base)) + ');}')
                page = context.new_page()
                page.set_default_timeout(20000)
                page.on('pageerror', lambda e: report['errors'].append(str(e)))

                def plot():
                    return page.locator(PLOTS).first

                def ready(n=None):
                    page.wait_for_load_state('networkidle')
                    expect(plot().locator('.uplot')).to_be_visible()
                    if n is not None:
                        page.wait_for_function('a=>{const u=document.querySelector(a.s)?.querySelector(".uplot")?.__verificationPlot;return u&&u.series.length===a.n+1}', arg={'s': PLOTS, 'n': n})
                    expect(plot()).not_to_contain_text('Applying flight filters')

                def state():
                    return plot().locator('.uplot').evaluate('el=>{const u=el.__verificationPlot;return {x:[u.scales.x.min,u.scales.x.max],y:[u.scales.y.min,u.scales.y.max],axes:u.axes.map(a=>a.label),bands:u.bands.map(b=>b.series),traces:u.series.slice(1).map((s,i)=>({label:s.label,dash:s.dash,width:s.width,stroke:typeof s.stroke==="function"?s.stroke(u,i+1):s.stroke,t:u.data[i+1][0],y:u.data[i+1][1]}))}}')

                def seed(patch):
                    page.evaluate('(s)=>localStorage.setItem("ptt.analysis-session.v1",JSON.stringify(s))', {**base, **patch})
                    page.reload()
                try:
                    page.goto(web)
                    ready(8)
                    s = state()
                    assert s['axes'][0] == 'Elapsed time (s)', s['axes']
                    assert {len(t['t']) for t in s['traces']} == {1200, 512}
                    assert all(('original' in t['label'] for t in s['traces'][:4]))
                    assert all(('filtered' in t['label'] for t in s['traces'][4:]))
                    for i in range(4):
                        assert s['traces'][i]['dash'] == s['traces'][i + 4]['dash']
                        assert s['traces'][i]['width'] < s['traces'][i + 4]['width']
                        assert s['traces'][i]['t'] == s['traces'][i + 4]['t']
                    passed('100/64Hz original/filtered native-grid overlays and variable styles')
                    plot().get_by_role('button', name=re.compile('^Variable legend')).click()
                    legend = page.get_by_role('dialog', name='Plot variables and line styles')
                    expect(legend).to_contain_text('Original')
                    expect(legend).to_contain_text('Filtered')
                    assert legend.locator('svg path').count() >= 6
                    legend.get_by_role('button', name='Hide torque_nm for all flights').click()
                    ready(4)
                    legend.get_by_role('button', name='Show torque_nm for all flights').click()
                    ready(8)
                    page.keyboard.press('Escape')
                    passed('Visual processing-pattern legend and all-flight variable toggles')
                    over = plot().locator('.u-over').bounding_box()
                    page.mouse.move(over['x'] + over['width'] * 0.5, over['y'] + over['height'] * 0.5)
                    page.keyboard.down('Alt')
                    page.mouse.wheel(0, -120)
                    page.keyboard.up('Alt')
                    page.wait_for_timeout(300)
                    manual_y = state()['y']
                    page.get_by_role('button', name=f'Hide flight {B}').click()
                    ready(4)
                    assert state()['y'] == manual_y, (state()['y'], manual_y)
                    page.get_by_role('button', name=f'Show flight {B}').click()
                    ready(8)
                    assert state()['y'] == manual_y
                    passed('Manual Y zoom preserved through flight hide/show')
                    requests.clear()
                    seed({'fullFlightRange': [2, 4]})
                    ready(8)
                    relevant = [r for r in requests if '/filter?' in r['url']]
                    assert len(relevant) >= 2, relevant
                    for name, want in [(A, (102, 104)), (B, (300.5, 302.5))]:
                        q = next((r['q'] for r in reversed(relevant) if name in r['url']))
                        assert float(q['t0'][0]) == want[0] and float(q['t1'][0]) == want[1], (name, q)
                    assert state()['x'] == [2, 4]
                    passed('Displayed range translated into each native filter window')
                    seed({'fullPlotMode': 'envelope'})
                    ready(16)
                    s = state()
                    assert len(s['bands']) == 8, s['bands']
                    for high, low in s['bands']:
                        a, b = (s['traces'][high - 1], s['traces'][low - 1])
                        assert a['label'].replace(' max', '') == b['label'].replace(' min', '')
                        assert a['t'] == b['t']
                    page.screenshot(path=str(out / 'envelope.png'), full_page=True)
                    passed('Separate native original/filtered envelope bands')
                    missing = {**compare, 'flights': compare['flights'] + [{'test': C, 'color': '#009e73', 'hidden': False, 'offset': 0}]}
                    seed({'fullFlightComparison': missing})
                    ready(10)
                    expect(plot()).to_contain_text('2 of 3 flights have all variables')
                    expect(plot().locator('[title*="missing torque_nm"]')).to_be_attached()
                    passed('Missing-variable partial data and detailed coverage')

                    def fail(route):
                        route.fulfill(status=503, json={'detail': 'isolated filter failure'})
                    context.route(f'**/tests/{B}/filter?*', fail)
                    seed({})
                    ready(6)
                    expect(plot()).to_contain_text('1 flight filter failed; originals shown')
                    b = [t for t in state()['traces'] if B in t['label']]
                    assert len(b) == 2 and all(('original' in t['label'] for t in b))
                    context.unroute(f'**/tests/{B}/filter?*', fail)
                    plot().get_by_role('button', name='Retry', exact=True).click()
                    ready(8)
                    passed('Per-flight filter failure original fallback and Retry')
                    seed({'plotConfigs': ['unavailable_signal'], 'fullPlotExtraColumns': [[]]})
                    page.wait_for_load_state('networkidle')
                    expect(plot().get_by_role('button', name='Plot variable', exact=True)).to_contain_text('unavailable_signal')
                    plot().get_by_role('button', name='Plot variable', exact=True).click()
                    expect(page.get_by_role('option', name=re.compile('unavailable_signal'))).to_have_attribute('aria-disabled', 'true')
                    passed('Unavailable primary retains its name as disabled option')
                    assert not report['errors'], report['errors']
                    assert not report['writes'], report['writes']
                    assert hashes(dataset, True) == before
                    passed('No page errors, writes or sample changes')
                except Exception:
                    page.screenshot(path=str(out / 'failure.png'), full_page=True)
                    (out / 'failure.html').write_text(page.content(), encoding='utf-8')
                    raise
                finally:
                    (out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                    browser.close()
    print('Owned servers stopped; report:', out, flush=True)
if __name__ == '__main__':
    main()
