"""Read-only real PNG downloads from the local preview, with an isolated browser session."""
import json
import os
import re
import zipfile
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect
from verify_plot_exports import png_inspect
from verify_multi_plot_exports import CANVAS_INSTRUMENT
from verify_plot_grid_fit import COLUMNS

URL = os.environ.get('PTT_PREVIEW_URL', 'http://127.0.0.1:8087/ptt/')
OUT = Path(os.environ.get('TEMP', '/tmp')) / 'ptt-compact-png-exports'
KEY = 'ptt.analysis-session.v1'
PLOTS = '.analyze-plots-pane [role="group"][aria-label$=" plot"]'
OUT.mkdir(exist_ok=True)
with urlopen(URL + 'api/analysis-sources', timeout=20) as response:
    sources = json.load(response)['sources']
session = {
    'version': 1, 'sources': sources, 'currentTest': 'ptt_demo_run_a',
    'xAxis': 'rpm', 'yAxis': 'thrust_n', 'axesUserSet': True,
    'selections': [{'test': 'ptt_demo_run_a', 'tpId': 3, 'hidden': False}],
    'plotConfigs': COLUMNS, 'plotsUserEdited': True, 'plotDensity': 'quad',
    'viewMode': 'tp', 'scatterCollapsed': True, 'xyXCols': ['rpm'] * 9,
    'xySource': 'tp', 'specSource': 'tp', 'specMode': 'fft',
    'specLogY': False, 'fullPlotMode': 'envelope',
}
report = {'checks': [], 'pageErrors': [], 'blockedWrites': []}
forbidden = re.compile(
    r'Displayed axes|Visible traces|Original grid slot|kiha-\w+-v\d|prefilter=|'
    r'rows \[|native bins|complete TP|Windowed FFT of stored samples|trailing samples unused',
    re.I,
)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    page = context.new_page()
    page.set_default_timeout(20000)
    page.on('pageerror', lambda e: report['pageErrors'].append(str(e)))

    def guard(route):
        request = route.request
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not (
            (request.method == 'POST' and request.url.endswith(('/plot-image-export', '/export-progress')))
            or (request.method == 'DELETE' and '/export-progress/' in request.url)
        ):
            report['blockedWrites'].append(request.url)
            route.abort()
        else:
            route.continue_()

    page.route('**/api/**', guard)
    page.add_init_script(CANVAS_INSTRUMENT)
    page.add_init_script(
        f"if(!localStorage.getItem('{KEY}'))localStorage.setItem('{KEY}',"
        f"{json.dumps(json.dumps(session))});"
    )

    def settled(count):
        page.wait_for_load_state('networkidle')
        expect(page.locator(PLOTS)).to_have_count(count)
        for plot in page.locator(PLOTS).all():
            plot.locator('.uplot').first.wait_for()
        page.wait_for_timeout(500)

    def patch(count=4, **changes):
        page.wait_for_timeout(300)
        page.evaluate("""([key,changes])=>{
            const session=JSON.parse(localStorage.getItem(key));
            Object.assign(session,changes);delete session.plotViewports;
            localStorage.setItem(key,JSON.stringify(session));
        }""", [KEY, changes])
        page.reload()
        settled(count)

    def download(tag, layout=None):
        if layout:
            page.get_by_role('button', name='Export selected plots', exact=True).click()
            panel = page.get_by_role('dialog', name='Export selected plots', exact=True)
            panel.get_by_role('button', name='Clear selection', exact=True).click()
            for box in panel.locator('input[type=checkbox][aria-label^="Select plot "]').all():
                box.check()
            panel.get_by_role('radio', name='2 × 2' if layout == 2 else '3 × 3', exact=True).check()
            button = panel.get_by_role('button', name='Download combined PNG', exact=True)
        else:
            plot = page.locator(PLOTS).first
            plot.get_by_role('button', name=re.compile('^Plot actions for ')).click()
            page.get_by_role('menuitem', name='Export CSV / PNG…', exact=True).click()
            panel = page.get_by_role('dialog', name=re.compile('^Export '))
            button = panel.get_by_role('button', name='Download PNG', exact=True)
        panel.get_by_role('checkbox', name='Include analysis metadata (ZIP)', exact=True).check()
        expect(button).to_be_enabled()
        page.evaluate('window.__multiPngCaptures=[]')
        with page.expect_download(timeout=60000) as event:
            button.click()
        path = OUT / (tag + '.zip')
        event.value.save_as(path)
        capture = page.evaluate('window.__multiPngCaptures.at(-1)')
        assert capture, tag
        with zipfile.ZipFile(path) as archive:
            metadata = json.loads(archive.read('analysis.json'))
            entries = [name for name in archive.namelist() if name.endswith('.png')]
            assert len(entries) == 1, entries
            image = OUT / (tag + '.png')
            image.write_bytes(archive.read(entries[0]))
        assert metadata['schema'] == 'kiha-analysis-v1' and metadata['format'] == 'png'
        assert 'ptt_demo_run_a' in json.dumps(metadata)
        records = [source for plot in metadata['plots'] for source in (
            plot['sources'] if plot.get('kind') == 'waterfall' else [plot]
        )]
        for record in records:
            assert record['scope'] and record['details'] and set(record['axes']) == {'x', 'y'}, record
            assert 'Displayed axes' in ' '.join(record['details'])
        if layout:
            assert not capture['texts'], capture['texts']
            assert len(capture['panels']) == layout * layout, capture
            assert capture['panels'][0]['x'] <= 12 and capture['panels'][0]['y'] <= 12
            assert [item['slot'] for item in metadata['plots']] == list(range(1, layout * layout + 1))
        cards = capture['panels'] if capture['panels'] else [capture]
        visible = [' '.join(item['text'] for item in card['texts']) for card in cards]
        assert not any(forbidden.search(text) for text in visible), (tag, visible)
        assert not any(re.search(r'\bPlot [1-9]\b', text) for text in visible), (tag, visible)
        if tag.startswith('full'):
            assert all('ptt_demo_run_a' in text for text in visible), (tag, visible)
            assert any('(min–max)' in text for text in visible), (tag, visible)
        stats = png_inspect(page, image, [])
        (OUT / (tag + '-metadata.json')).write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        (OUT / (tag + '-canvas.json')).write_text(json.dumps(capture, indent=2), encoding='utf-8')
        report['checks'].append({'tag': tag, **stats, 'visible': visible})
        page.keyboard.press('Escape')
        expect(panel).not_to_be_visible()
        print(f'PASS {tag}: {stats["width"]}x{stats["height"]}', flush=True)

    try:
        page.goto(URL)
        settled(4)
        download('time-tp')
        patch(viewMode='spectrum', specMode='fft', specSource='tp')
        download('fft')
        patch(viewMode='spectrum', specMode='welch', specSource='tp')
        download('psd')
        patch(viewMode='xy', xySource='tp')
        download('xy')
        patch(viewMode='spectrum', specMode='waterfall', specSource='full')
        download('waterfall')
        patch(viewMode='spectrum', specMode='waterfall', specSource='full', specLogY=True)
        download('waterfall-log')
        patch(viewMode='full', fullPlotMode='envelope')
        download('full-envelope')
        download('full-2x2', 2)
        patch(count=9, viewMode='full', plotDensity='nine')
        download('full-3x3', 3)
        page.screenshot(path=str(OUT / 'grid-3x3-ui.png'), full_page=True)
        assert not report['pageErrors'], report['pageErrors']
        assert not report['blockedWrites'], report['blockedWrites']
        (OUT / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('PASS all downloads; evidence ' + str(OUT), flush=True)
    except Exception:
        page.screenshot(path=str(OUT / 'failure.png'), full_page=True)
        print(page.locator('body').inner_text()[-5000:], flush=True)
        raise
    finally:
        context.close()
        browser.close()
