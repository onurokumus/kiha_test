"""Read-only theme/PNG checks against existing demo data in an isolated browser.

Only GET requests reach the API. Canvas instrumentation observes paint colors
and can inject one capture failure; it does not modify application source.
"""
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

URL = os.environ.get('PTT_PREVIEW_URL', 'http://127.0.0.1:8087/ptt/')
OUT = Path(os.environ.get('TEMP', '/tmp')) / 'ptt-plot-theme'
KEY = 'ptt.analysis-session.v1'
PLOTS = '.analyze-plots-pane [role="group"][aria-label$=" plot"]'
INSTRUMENT = r'''(() => {
  window.__themeCaptures=[];
  window.__themeSvgExports=[];
  const objectUrl=URL.createObjectURL;
  URL.createObjectURL=function(blob){
    if(blob.type.includes('svg'))blob.text().then(text=>window.__themeSvgExports.push(text));
    return objectUrl.call(this,blob);
  };
  const clear=CanvasRenderingContext2D.prototype.clearRect;
  CanvasRenderingContext2D.prototype.clearRect=function(...args){
    if(args[0]===0&&args[1]===0)this.canvas.__themeText=[];
    return clear.apply(this,args);
  };
  const text=CanvasRenderingContext2D.prototype.fillText;
  CanvasRenderingContext2D.prototype.fillText=function(value,...args){
    (this.canvas.__themeText??=[]).push({text:String(value),color:this.fillStyle});
    return text.call(this,value,...args);
  };
  const draw=CanvasRenderingContext2D.prototype.drawImage;
  CanvasRenderingContext2D.prototype.drawImage=function(source,...args){
    if(source instanceof HTMLCanvasElement&&source.closest('.uplot')){
      window.__themeCaptures.push(source.__themeText??[]);
      if(window.__failThemeCapture){window.__failThemeCapture=false;throw Error('Theme verification capture failure');}
    }
    return draw.call(this,source,...args);
  };
})();'''


def run():
    OUT.mkdir(exist_ok=True)
    sources = json.load(urlopen(URL + 'api/analysis-sources', timeout=20))['sources']
    base = {'version': 1, 'sources': sources, 'currentTest': 'ptt_demo_run_a',
            'xAxis': 'rpm', 'yAxis': 'thrust_n', 'axesUserSet': True,
            'selections': [{'test': 'ptt_demo_run_a', 'tpId': 3, 'hidden': False}],
            'plotConfigs': ['thrust_n'], 'plotsUserEdited': True, 'plotDensity': 'single',
            'viewMode': 'tp', 'scatterCollapsed': False, 'scatterRatio': 40,
            'xyXCols': ['rpm'] * 9, 'xyYCols': ['thrust_n'] * 9,
            'xySource': 'tp', 'specSource': 'tp', 'specMode': 'fft',
            'showHorizontalErrorBars': True, 'showVerticalErrorBars': True,
            'specLogY': False, 'fullPlotMode': 'envelope', 'fullRange': [4, 12],
            'timeZoom': [1, 4]}
    report = {'checks': [], 'errors': [], 'writes': []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for tag, changes in [
            ('time-tp', {}), ('full-time', {'viewMode': 'full', 'expandedPlot': 0}),
            ('fft', {'viewMode': 'spectrum'}),
            ('psd', {'viewMode': 'spectrum', 'specMode': 'welch'}),
            ('xy', {'viewMode': 'xy'}),
            ('waterfall', {'viewMode': 'spectrum', 'specMode': 'waterfall', 'specSource': 'full'}),
        ]:
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
            page = context.new_page()
            page.set_default_timeout(20000)
            page.on('pageerror', lambda error: report['errors'].append(str(error)))
            data_requests = []

            def guard(route):
                if route.request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                    report['writes'].append(route.request.url)
                    route.abort()
                else:
                    if re.search(r'/(data|spectrum|waterfall|xy|filter)(?:\?|$)', route.request.url):
                        data_requests.append(route.request.url)
                    route.continue_()

            page.route('**/api/**', guard)
            page.add_init_script(INSTRUMENT)
            page.add_init_script(f"localStorage.setItem('{KEY}', {json.dumps(json.dumps({**base, **changes}))});"
                                 "localStorage.setItem('ptt.theme.v1','light');")
            if tag == 'time-tp':
                page.add_init_script("localStorage.setItem('ptt.settings.v1',JSON.stringify({"
                                     "datasheetZone:'ptt_demo_run_b',datasheetVisible:true}));")
            page.goto(URL)
            page.wait_for_load_state('networkidle')
            canvas = page.locator(PLOTS).first.locator('.uplot canvas').first
            expect(canvas).to_be_visible()
            page.wait_for_timeout(400)
            page.mouse.move(0, 0)
            if tag == 'time-tp':
                expect(page.locator('.datasheet-scatter-series .recharts-scatter-line path')).to_be_visible()
                before_pan = page.evaluate(f"JSON.parse(localStorage.getItem('{KEY}'))")
                scatter = page.get_by_label('Plot canvas for test-point overview', exact=True)
                bounds = scatter.bounding_box()
                start = (bounds['x'] + bounds['width'] * .5, bounds['y'] + bounds['height'] * .4)
                page.mouse.move(*start)
                page.mouse.down()
                page.mouse.move(start[0] + 64, start[1] + 27, steps=8)
                page.mouse.up()
                page.mouse.move(0, 0)
                page.wait_for_timeout(350)
                panned = page.evaluate(f"JSON.parse(localStorage.getItem('{KEY}'))")
                assert panned['mainZoom'] != before_pan['mainZoom']
                assert panned['selections'] == before_pan['selections']

            def download(name, scatter=False):
                if scatter:
                    page.get_by_role('button', name='More', exact=True).click()
                    page.get_by_role('button', name='Export scatter plot', exact=True).click()
                else:
                    page.locator(PLOTS).first.get_by_role('button', name=re.compile('^Plot actions for ')).click()
                    page.get_by_role('menuitem', name='Export CSV / PNG…', exact=True).click()
                dialog = page.get_by_role('dialog', name=re.compile('^Export '))
                metadata = dialog.get_by_role('checkbox', name='Include analysis metadata (ZIP)', exact=True)
                if metadata.count():
                    metadata.uncheck()
                with page.expect_download() as event:
                    dialog.get_by_role('button', name='Download PNG', exact=True).click()
                path = OUT / (name + '.png')
                event.value.save_as(path)
                page.keyboard.press('Escape')
                page.mouse.move(0, 0)
                if scatter:
                    expect(page.get_by_role('button', name='More', exact=True)).to_be_focused()
                    lines = page.evaluate("""() => {
                      const svg=new DOMParser().parseFromString(window.__themeSvgExports.at(-1),'image/svg+xml');
                      return [...svg.querySelectorAll('[data-grid-layer] line')]
                        .map(line=>[line.getAttribute('data-export-stroke'),line.style.stroke]);
                    }""")
                    assert lines, 'exported scatter grid is missing'
                    for raw, painted in lines:
                        expected = 'rgb(' + ', '.join(str(int(raw[index:index + 2], 16)) for index in [1, 3, 5]) + ')'
                        assert painted == expected, (name, raw, painted)
                return hashlib.sha256(path.read_bytes()).hexdigest()

            light_png = download(tag + '-light')
            scatter_light = download('scatter-light', True) if tag == 'time-tp' else None
            live_light = canvas.evaluate('c=>c.toDataURL()')
            def legend_colors():
                return page.locator(PLOTS).first.locator('.u-series .u-label, .u-series .u-marker').evaluate_all(
                    'els=>els.map(e=>[e.style.color,e.style.borderColor,e.style.background])')
            light_legend = legend_colors()
            page.evaluate("window.__themeCanvas=document.querySelector('.analyze-plots-pane .uplot canvas')")
            saved = page.evaluate(f"localStorage.getItem('{KEY}')")
            request_count = len(data_requests)
            page.get_by_role('switch', name='Dark mode', exact=True).click()
            page.mouse.move(0, 0)
            page.wait_for_timeout(350)
            assert page.evaluate("window.__themeCanvas===document.querySelector('.analyze-plots-pane .uplot canvas')"), tag
            assert saved == page.evaluate(f"localStorage.getItem('{KEY}')"), tag + ' session changed'
            assert len(data_requests) == request_count, tag + ' theme refetched data'
            live_dark = canvas.evaluate('c=>c.toDataURL()')
            dark_legend = legend_colors()
            if tag == 'full-time':
                assert dark_legend and dark_legend != light_legend, 'expanded legend did not repaint'
            assert live_light != live_dark, tag + ' canvas did not repaint'
            colors = canvas.evaluate('c=>[...new Set((c.__themeText??[]).map(t=>t.color))]')
            assert '#a2b1c8' in colors, (tag, colors)
            page.screenshot(path=str(OUT / (tag + '-dark-ui.png')))
            dark_png = download(tag + '-dark')
            assert light_png == dark_png, tag + ' dark theme leaked into PNG'
            assert canvas.evaluate('c=>c.toDataURL()') == live_dark, tag + ' export did not restore canvas'
            assert legend_colors() == dark_legend, tag + ' export did not restore legend'
            assert page.evaluate("window.__themeCaptures.at(-1).some(text=>text.color==='#626f83')"), tag
            if tag == 'time-tp':
                assert scatter_light == download('scatter-dark', True), 'scatter dark theme leaked into PNG'
                scatter = page.get_by_label('Plot canvas for test-point overview', exact=True)
                scatter.focus()
                page.keyboard.press('Shift+F10')
                menu = page.get_by_role('menu', name='Plot actions for test-point overview', exact=True)
                expect(menu).to_be_visible()
                menu.get_by_role('menuitem', name='Export CSV / PNG…', exact=True).click()
                expect(page.get_by_role('dialog', name='Export scatter plot', exact=True)).to_be_visible()
                page.keyboard.press('Escape')
                expect(scatter).to_be_focused()
                page.keyboard.press('Shift+F10')
                expect(menu).to_be_visible()
                page.keyboard.press('Escape')
                expect(scatter).to_be_focused()
            if tag in {'time-tp', 'full-time'}:
                page.locator(PLOTS).first.get_by_role('button', name=re.compile('^Plot actions for ')).click()
                page.get_by_role('menuitem', name='Export CSV / PNG…', exact=True).click()
                dialog = page.get_by_role('dialog', name=re.compile('^Export '))
                page.evaluate('window.__failThemeCapture=true')
                dialog.get_by_role('button', name='Download PNG', exact=True).click()
                expect(dialog.get_by_role('alert')).to_contain_text('Theme verification capture failure')
                page.keyboard.press('Escape')
                assert canvas.evaluate('c=>c.toDataURL()') == live_dark, 'failed export did not restore dark canvas'
                assert legend_colors() == dark_legend, 'failed export did not restore legend'
            page.get_by_role('switch', name='Dark mode', exact=True).click()
            page.mouse.move(0, 0)
            page.wait_for_timeout(250)
            assert canvas.evaluate('c=>c.toDataURL()') == live_light, tag + ' light canvas/range changed'
            assert legend_colors() == light_legend, tag + ' light legend changed'
            assert saved == page.evaluate(f"localStorage.getItem('{KEY}')"), tag + ' saved ranges changed'
            report['checks'].append({'mode': tag, 'pngSha256': light_png, 'darkTextColors': colors})
            print('PASS', tag, 'live repaint / state retained / identical light PNG / restore', flush=True)
            if tag == 'full-time':
                page.get_by_role('button', name='Split', exact=True).click()
                page.wait_for_load_state('networkidle')
                split = page.locator('.feature-split .uplot canvas').first
                expect(split).to_be_visible()
                before = split.evaluate('c=>c.toDataURL()')
                page.evaluate("window.__splitCanvas=document.querySelector('.feature-split .uplot canvas')")
                page.get_by_role('switch', name='Dark mode', exact=True).click()
                page.wait_for_timeout(200)
                assert page.evaluate("window.__splitCanvas===document.querySelector('.feature-split .uplot canvas')")
                assert split.evaluate('c=>c.toDataURL()') != before
                overlays = page.locator('.feature-split [style*="rgba(166, 184, 255"]').evaluate_all(
                    "els=>els.map(e=>getComputedStyle(e).backgroundColor)")
                assert overlays and all(color.endswith((', 0.17)', ', 0.063)')) for color in overlays), overlays
                page.screenshot(path=str(OUT / 'split-dark-ui.png'), full_page=True)
                page.get_by_role('switch', name='Dark mode', exact=True).click()
                page.wait_for_timeout(200)
                assert split.evaluate('c=>c.toDataURL()') == before, 'Split ranges changed'
                report['checks'].append({'mode': 'split', 'overlayColors': overlays})
                print('PASS split canvas identity / translucent overlays / ranges retained', flush=True)
            context.close()
        browser.close()
    assert not report['errors'] and not report['writes'], report
    (OUT / 'results.json').write_text(json.dumps(report, indent=2))
    print('PASS all plot themes; evidence', OUT, flush=True)


if __name__ == '__main__':
    run()
