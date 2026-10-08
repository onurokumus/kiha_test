"""Native-demo regression for distinct Full test variable colors.

Run against Vite and a native Python 3.13 backend using copied ptt_demo_run_a/b
fixtures. --data-dir must name that isolated backend root. Dataset mutations are
blocked; only reads and ephemeral PNG ZIP/progress requests reach the backend.
The Vite uPlot instrumentation only affects this disposable browser context.
"""
import argparse
import hashlib
import io
import json
import math
import re
import tempfile
import traceback
import zipfile
from itertools import combinations
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from PIL import Image

from verify_time_y_zoom import instrument
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout
from verify_filter_overlay import FILTER

KEY = 'ptt.analysis-session.v1'
TEST = 'ptt_demo_run_a'

def lab(color):
    values = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    r,g,b = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
    l = (.4122214708*r + .5363325363*g + .0514459929*b)**(1/3)
    m = (.2119034982*r + .6806995451*g + .1073969566*b)**(1/3)
    s = (.0883024619*r + .2817188376*g + .6299787005*b)**(1/3)
    return (.2104542553*l + .793617785*m - .0040720468*s,
            1.9779984951*l - 2.428592205*m + .4505937099*s,
            .0259040371*l + .7827717662*m - .808675766*s)

def distance(a,b):
    return math.dist(lab(a),lab(b))

def old_color(index):
    palette = ['#d55e00','#21834a','#9333b8','#c72c65','#a17c00','#c73b32']
    if index < 6:
        return palette[index]
    h = ((index-6)*137.508+24)%240
    h = h if h <=165 else h+120
    a = .65*.38
    def channel(n):
        k=(n+h/30)%12
        return math.floor(255*(.38-a*max(-1,min(k-3,9-k,1)))+.5)
    return '#' + ''.join(f'{channel(n):02x}' for n in (0,8,4))

def hashes(data_dir):
    result = {str(p.relative_to(data_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in data_dir.rglob('*') if p.is_file()}
    assert result, f'Isolated fixture directory has no files: {data_dir}'
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:3016/')
    parser.add_argument('--reproduce', action='store_true')
    parser.add_argument('--data-dir', type=Path, required=True,
                        help='Isolated copied-demo backend data root; every file is hashed before/after')
    parser.add_argument('--output', type=Path, default=Path(tempfile.gettempdir())/'ptt-full-test-colors')
    args=parser.parse_args()
    url=args.url.rstrip('/')+'/'
    out=args.output
    out.mkdir(exist_ok=True,parents=True)
    sources=json.load(urlopen(url+'api/analysis-sources'))['sources']
    meta=json.load(urlopen(url+f'api/tests/{TEST}'))
    cols=[c for c in meta['columns'] if c != meta['time_column']]
    pair=min(combinations(range(len(cols)),2), key=lambda p:distance(old_color(p[0]),old_color(p[1])))
    initial=[cols[i] for i in pair]
    report={'checks':[], 'pageErrors':[], 'apiWrites':[], 'ephemeralExportRequests':[], 'initialVariables':initial,
            'sourceColors':{cols[i]:old_color(i) for i in pair}, 'snapshots':{}}
    original_hashes=hashes(args.data_dir)
    session={'version':1,'sources':sources,'currentTest':TEST,'xAxis':'rpm','yAxis':'thrust_n',
        'axesUserSet':True,'selections':[], 'plotConfigs':[initial[0]],'plotsUserEdited':True,
        'plotDensity':'single','viewMode':'full','fullPlotMode':'line','scatterCollapsed':True,
        'fullPlotExtraColumns':[[initial[1]]]+[[] for _ in range(8)]}

    def passed(label):
        report['checks'].append(label)
        print('PASS:',label,flush=True)

    with tempfile.TemporaryDirectory(prefix='ptt-variable-colors-') as temporary, sync_playwright() as p:
        tmp=Path(temporary)
        ext=tmp/'extension'; ext.mkdir()
        (ext/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'PTT variable color QA',
            'version':'1.0','permissions':['tabs'],'background':{'service_worker':'background.js'}}))
        (ext/'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
        context=p.chromium.launch_persistent_context(str(tmp/'profile'),channel='chromium',headless=True,
            no_viewport=True,accept_downloads=True,args=[f'--disable-extensions-except={ext}',
            f'--load-extension={ext}','--window-size=1600,1100'])
        def guard(route):
            if route.request.method == 'POST' and route.request.url.split('?')[0].endswith(('/api/plot-image-export','/api/export-progress')):
                # This endpoint only packages the captured PNG+metadata into a
                # temporary ZIP with an ephemeral progress token. Inspected
                # backend image_export.py and export_progress.py: no datasets.
                report['ephemeralExportRequests'].append(route.request.url);route.continue_()
            elif route.request.method not in {'GET','HEAD','OPTIONS'}:
                report['apiWrites'].append(route.request.url); route.abort()
            else: route.continue_()
        context.route('**/api/**',guard)
        context.route('**/src/utils/uplotSync.ts*',instrument)
        context.add_init_script(f"if(!sessionStorage.getItem('colors-seeded')){{sessionStorage.setItem('colors-seeded','1');localStorage.setItem('{KEY}',{json.dumps(json.dumps(session))});localStorage.setItem('ptt.theme.v1','light');}}")
        page=context.pages[0]; page.set_default_timeout(20000)
        page.on('pageerror',lambda e:report['pageErrors'].append(str(e)))
        cdp=context.new_cdp_session(page)
        worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        def plot(): return page.locator('[role="group"][aria-label$=" full test plot"]').first
        def ready():
            page.wait_for_load_state('networkidle')
            expect(plot().locator('.uplot')).to_have_count(1)
            expect(plot()).not_to_contain_text('Applying filter')
            wait_for_chart_layout(page)
        def snapshot():
            return plot().locator('.uplot').evaluate('''el=>{const u=el.__verificationPlot;return {
              series:u.series.slice(1).map((s,i)=>({label:s.label,color:typeof s.stroke==='function'?s.stroke(u,i+1):s.stroke,dash:s.dash,width:s.width})),
              bands:u.bands.map(b=>b.series),x:[u.scales.x.min,u.scales.x.max]};}''')
        def active_colors():
            return json.loads(plot().get_by_role('button',name=re.compile('^Variable legend for')).get_attribute('data-tooltip-legend'))
        def colors(): return {e['label']:e['color'] for e in active_colors()}
        def patch(changes):
            page.wait_for_timeout(450)
            page.evaluate(f'''changes=>{{const s=JSON.parse(localStorage.getItem('{KEY}'));Object.assign(s,changes);localStorage.setItem('{KEY}',JSON.stringify(s));}}''',changes)
            page.reload(); ready()
        def open_legend():
            plot().get_by_role('button',name=re.compile('^Variable legend for')).click()
            panel=page.get_by_role('dialog',name='Plot variables and colors',exact=True)
            expect(panel).to_be_visible(); return panel
        def add(column):
            plot().get_by_role('button',name='Add variable to plot',exact=True).click()
            page.get_by_role('combobox',name='Search variables to add...',exact=True).fill(column)
            option=page.get_by_role('option',name=column,exact=True)
            swatch=option.locator('[class*="optionRail"]').first
            expect(swatch).to_be_attached()
            preview=swatch.evaluate('el=>getComputedStyle(el).backgroundColor')
            option.click(); ready()
            if preview:
                expected=page.evaluate('c=>{const e=document.createElement("i");e.style.color=c;document.body.append(e);const r=getComputedStyle(e).color;e.remove();return r;}',colors()[column])
                assert preview==expected,(column,preview,expected)
            return preview
        def remove(column):
            panel=open_legend(); panel.get_by_role('button',name=f'Remove {column} from plot',exact=True).click()
            page.keyboard.press('Escape'); ready()
        def consistency(tag,minimum=None):
            mapping=colors(); actual=snapshot(); report['snapshots'][tag]={'colors':mapping,**actual}
            minimum=minimum if minimum is not None else (.12 if len(mapping)<=2 else .09)
            for a,b in combinations(mapping.values(),2):
                assert distance(a,b)>=minimum-1e-4,(tag,mapping,distance(a,b))
            for s in actual['series']:
                assert s['color'][:7] in mapping.values(),(tag,s,mapping)
            dots=plot().locator('[data-full-test-variables] button[aria-label^="Variable legend"] i').evaluate_all('els=>els.map(el=>el.style.backgroundColor)')
            expected_rgb=page.evaluate('cs=>cs.map(c=>{const e=document.createElement("i");e.style.background=c;return e.style.backgroundColor;})',list(mapping.values())[:3])
            assert dots==expected_rgb,(tag,dots,expected_rgb)
            page.mouse.move(0,0)
            plot().get_by_role('button',name=re.compile('^Variable legend for')).hover()
            expect(page.locator('#page-hover-tooltip')).to_be_visible()
            hover=page.locator('#page-hover-tooltip i').evaluate_all('els=>els.map(el=>el.style.borderColor)')
            expected_all=page.evaluate('cs=>cs.map(c=>{const e=document.createElement("i");e.style.borderColor=c;return e.style.borderColor;})',list(mapping.values()))
            assert hover==expected_all,(tag,hover,expected_all)
            panel=open_legend()
            border=panel.locator('i').evaluate_all('els=>els.map(el=>el.style.borderColor)')
            assert border
            expected_all=page.evaluate('cs=>cs.map(c=>{const e=document.createElement("i");e.style.borderColor=c;return e.style.borderColor;})',list(mapping.values()))
            assert all(c in expected_all for c in border),(tag,border,expected_all)
            page.keyboard.press('Escape'); page.mouse.move(0,0)
            return mapping
        try:
            page.goto(url); ready()
            report['initialControls']=page.locator('button').evaluate_all('els=>els.map(e=>e.getAttribute("aria-label")||e.innerText)')
            initial_colors=colors(); report['baselineColors']=initial_colors
            report['baselineDistance']=distance(*initial_colors.values())
            report['initialCanvas']=snapshot()
            capture_browser_view(cdp,out/'initial.png')
            if args.reproduce:
                assert report['baselineDistance']<.05,report
                passed('Original real-demo generated colors collide perceptually (<0.05 OKLab distance)')
                return
            consistency('initial-pair')
            passed('Previously colliding source columns now distinct; canvas, header and both legends agree')
            survivors=colors()
            added=[c for c in cols if c not in initial][:4]
            for c in added:
                add(c)
                assert all(colors()[k]==v for k,v in survivors.items()),(c,survivors,colors())
                survivors=colors()
                consistency('add-'+c)
            expect(plot().get_by_role('button',name='Add variable to plot',exact=True)).to_be_disabled()
            six=colors(); remove(added[-1]); assert all(colors()[k]==v for k,v in six.items() if k!=added[-1])
            add(added[-1]); assert colors()==six
            passed('Six-variable capacity, contextual add previews and stable colors on append/remove-last/re-add')
            target=next(c for c in cols if c not in six)
            plot().get_by_role('button',name='Plot variable',exact=True).click()
            page.get_by_role('combobox',name='Search plot variables...',exact=True).fill(target)
            option=page.get_by_role('option',name=target,exact=True)
            primary_preview=option.locator('[class*="optionRail"]').evaluate('e=>e.style.backgroundColor')
            option.click(); ready()
            primary_actual=plot().get_by_role('button',name='Plot variable',exact=True).locator('[class*="triggerRail"]').evaluate('e=>e.style.backgroundColor')
            assert primary_preview==primary_actual,(primary_preview,primary_actual)
            consistency('primary-change')
            passed('Changing primary variable recomputes the comparison without collisions')
            before=colors(); page.reload(); ready(); assert colors()==before
            passed('Browser session reload deterministically restores comparison colors')
            saved=page.evaluate("localStorage.getItem('ptt.analysis-session.v1')")
            remove(added[-1])
            page.evaluate("saved=>localStorage.setItem('ptt.analysis-session.v1',saved)",saved)
            page.reload();ready()
            assert colors()==before
            expect(page.get_by_role('button',name='Sessions',exact=True)).to_have_count(0)
            passed('Quiet browser restoration retains selected variables and their resolved colors')
            for mode in ('line','envelope'):
                patch({'fullPlotMode':mode,'plotFilters':[FILTER],'plotShowOriginal':[True]})
                expect(plot()).to_have_attribute('data-filter-display','overlay')
                snap=snapshot(); n=6 if mode=='line' else 12
                assert len(snap['series'])==n*2,(mode,snap)
                for i in range(n):
                    original,filtered=snap['series'][i],snap['series'][i+n]
                    assert original['color'][:7]==filtered['color'][:7]
                    assert original['dash'] and not filtered['dash']
                consistency(mode+'-light')
                page.get_by_role('switch',name='Dark mode',exact=True).click(); ready()
                consistency(mode+'-dark')
                capture_browser_view(cdp,out/(mode+'-dark.png'))
                page.get_by_role('switch',name='Dark mode',exact=True).click(); ready()
            passed('Raw/line and min-max envelopes preserve distinct variables and matching original/filtered hues in both themes')
            plot().get_by_role('button',name=re.compile('^Expand ')).click(); ready()
            expect(plot().locator('.u-legend')).to_be_visible()
            consistency('maximized')
            plot().get_by_role('button',name=re.compile('^Minimize ')).click(); ready()
            for zoom in (1.25,1.5):
                set_browser_zoom(worker,page,zoom); ready(); consistency(f'zoom-{zoom}')
                assert plot().evaluate('e=>e.scrollWidth<=e.clientWidth+1')
                capture_browser_view(cdp,out/f'zoom-{zoom}.png')
            set_browser_zoom(worker,page,1); ready()
            passed('Maximize/restore and actual 125%/150% browser zoom preserve colors and fit plot controls')
            expected=colors()
            page.get_by_role('switch',name='Dark mode',exact=True).click();ready()
            dark_before_export=colors()
            plot().get_by_role('button',name=re.compile('^Plot actions for')).click()
            page.get_by_role('menuitem',name='Export CSV / PNG…',exact=True).click()
            dialog=page.get_by_role('dialog',name=re.compile('^Export .* plot$'))
            dialog.get_by_role('checkbox',name='Include analysis metadata (ZIP)',exact=True).check()
            with page.expect_download(timeout=60000) as download:
                dialog.get_by_role('button',name='Download PNG',exact=True).click()
            target_file=out/'six-variable-colors.zip';download.value.save_as(target_file)
            with zipfile.ZipFile(target_file) as archive:
                metadata=json.loads(archive.read('analysis.json'))
                report['exportMetadata']=metadata
                mappings=[]
                def walk(value):
                    if isinstance(value,dict):
                        if 'variable_colors' in value: mappings.append(value['variable_colors'])
                        for v in value.values(): walk(v)
                    elif isinstance(value,list):
                        for v in value: walk(v)
                walk(metadata)
                assert expected in mappings,(expected,mappings)
                png=archive.read(next(n for n in archive.namelist() if n.endswith('.png')))
                (out/'six-variable-colors.png').write_bytes(png)
                image=Image.open(io.BytesIO(png)).convert('RGB')
                pixels={rgb for _,rgb in image.getcolors(image.width*image.height)}
                for color in expected.values():
                    rgb=tuple(int(color[i:i+2],16) for i in (1,3,5))
                    assert rgb in pixels,(color,'missing from PNG')
            dialog.get_by_role('button',name='Close export',exact=True).click()
            assert colors()==dark_before_export
            passed('Dark-mode PNG contains every resolved light hue; metadata agrees and live dark colors restore')
            assert not report['pageErrors'],report['pageErrors']
            assert not report['apiWrites'],report['apiWrites']
            assert hashes(args.data_dir)==original_hashes,'Isolated source datasets changed'
            passed('No application errors or dataset changes; only reads and ephemeral PNG packaging requests')
        except Exception:
            report['failure']=traceback.format_exc()
            capture_browser_view(cdp,out/'failure.png')
            raise
        finally:
            report['datasetsUnchanged']=hashes(args.data_dir)==original_hashes
            (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            context.close()

if __name__=='__main__': main()
