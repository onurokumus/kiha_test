"""Actual wheel regression against a built PTT preview; GET-only existing library.

Uses a disposable Chromium profile and real browser zoom. No server writes.
Run: python -X utf8 scripts/verify_section_scrolling.py --url http://127.0.0.1:8087/ptt/
"""
import argparse
import json
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect
from verify_browser_zoom import set_browser_zoom, capture_browser_view

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', default='http://127.0.0.1:8087/ptt/')
args = parser.parse_args()
out = Path(tempfile.gettempdir()) / 'ptt-section-scrolling'
out.mkdir(exist_ok=True)
report = {'checks': [], 'errors': [], 'blockedWrites': []}

def record(label):
    report['checks'].append(label)
    print(label, flush=True)

with tempfile.TemporaryDirectory(prefix='ptt-scroll-') as temporary, sync_playwright() as p:
    temp = Path(temporary)
    ext = temp / 'extension'
    ext.mkdir()
    (ext / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Scrolling regression',
        'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
    (ext / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
    context = p.chromium.launch_persistent_context(str(temp / 'profile'), channel='chromium',
        headless=True, no_viewport=True, args=[f'--disable-extensions-except={ext}',
        f'--load-extension={ext}', '--window-size=1440,800'])
    page = context.pages[0]
    page.set_default_timeout(12000)
    page.on('pageerror', lambda error: report['errors'].append(str(error)))
    data_requests = []
    page.on('request', lambda request: data_requests.append(request.url)
        if urlparse(request.url).path.endswith('/data') else None)
    def guard(route):
        if route.request.method in ('GET', 'HEAD', 'OPTIONS'):
            route.continue_()
        else:
            report['blockedWrites'].append([route.request.method, route.request.url])
            route.abort()
    context.route('**/api/**', guard)
    def settle():
        page.wait_for_load_state('networkidle')
        page.wait_for_timeout(160)
    def nav(label):
        page.get_by_role('navigation', name='Main navigation').get_by_role('button', name=label, exact=True).click()
        settle()
    def wheel(x, y, delta=300, modifier=None):
        page.mouse.move(x, y)
        if modifier:
            page.keyboard.down(modifier)
        page.mouse.wheel(0, delta)
        if modifier:
            page.keyboard.up(modifier)
        page.wait_for_timeout(320)
    def reset(root):
        root.evaluate('e=>e.scrollTop=0')
        page.wait_for_timeout(100)
    def page_wheel(root, label):
        reset(root)
        assert root.evaluate('e=>e.scrollHeight-e.clientHeight') > 100, label
        box = root.bounding_box()
        wheel(box['x'] + 5, box['y'] + box['height'] * .7)
        assert root.evaluate('e=>e.scrollTop') > 50, label
        record(label)
    def expand(selector):
        for summary in page.locator(selector).all():
            if not summary.locator('..').evaluate('e=>e.open'):
                summary.click()
    def center_visible(locator, root):
        locator.scroll_into_view_if_needed()
        page.wait_for_timeout(150)
        box, bounds = locator.bounding_box(), root.bounding_box()
        top = max(box['y'], bounds['y'] + 100)
        bottom = min(box['y'] + box['height'], bounds['y'] + bounds['height'] - 10)
        assert bottom > top, (box, bounds)
        return box['x'] + box['width'] * .6, (top + bottom) / 2
    try:
        page.goto(args.url)
        settle()
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        cdp = context.new_cdp_session(page)
        window = cdp.send('Browser.getWindowForTarget')['windowId']
        for width, height, factor in [(1440,800,1), (960,400,1), (1440,900,1.25), (1440,900,1.5)]:
            # Target the content viewport at 100%, excluding Chromium's toolbar.
            set_browser_zoom(worker, page, 1)
            cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': height}})
            page.wait_for_timeout(120)
            chrome_height = page.evaluate('outerHeight-innerHeight')
            cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': height + chrome_height}})
            set_browser_zoom(worker, page, factor)
            label = f'{width}x{height}@{factor}'
            nav('Edit')
            expand('.edit-view .feature-inline-disclosure > summary, .edit-view .feature-disclosure > summary')
            root = page.locator('.edit-view')
            page_wheel(root, label + ' Edit left padding')
            reset(root)
            box = root.bounding_box()
            wheel(box['x'] + box['width'] - 6, box['y'] + box['height'] * .7)
            assert root.evaluate('e=>e.scrollTop') > 50
            record(label + ' Edit right padding')
            reset(root)
            header = root.locator('header').bounding_box()
            x, y = header['x'] + 10, header['y'] + header['height'] + 6
            assert page.evaluate('({x,y})=>document.elementFromPoint(x,y).tagName', {'x':x,'y':y}) == 'FIELDSET'
            wheel(x, y)
            assert root.evaluate('e=>e.scrollTop') > 50
            record(label + ' Edit fieldset gap')
            textarea = page.get_by_role('textbox', name='Description', exact=True)
            x,y = center_visible(textarea, root)
            before = root.evaluate('e=>e.scrollTop')
            wheel(x,y)
            assert root.evaluate('e=>e.scrollTop') > before + 10
            record(label + ' Edit input chains to page')
            grid = root.locator('.feature-column-grid')
            x,y = center_visible(grid, root)
            if grid.evaluate('e=>e.scrollHeight-e.clientHeight') > 50:
                grid.evaluate('e=>e.scrollTop=0')
                before = root.evaluate('e=>e.scrollTop')
                wheel(x,y,150)
                assert grid.evaluate('e=>e.scrollTop') > 20
                assert abs(root.evaluate('e=>e.scrollTop') - before) < 2
                grid.evaluate('e=>e.scrollTop=e.scrollHeight')
                root.evaluate('e=>e.scrollTop=Math.max(0,e.scrollTop-70)')
                x,y = center_visible(grid, root)
                before = root.evaluate('e=>e.scrollTop')
                wheel(x,y,200)
                assert root.evaluate('e=>e.scrollTop') > before + 10
                record(label + ' Edit nested columns scroll, then chain at boundary')
            reset(root)
            box = root.bounding_box()
            for _ in range(8):
                wheel(box['x'] + 5, box['y'] + box['height'] * .75,1200)
                if root.evaluate('e=>e.scrollHeight-e.clientHeight-e.scrollTop') < 2:
                    break
            assert root.evaluate('e=>e.scrollHeight-e.clientHeight-e.scrollTop') < 2
            bottom = page.get_by_role('button',name='apply renames / removals',exact=True).bounding_box()
            assert 0 <= bottom['y'] and bottom['y'] + bottom['height'] <= page.evaluate('innerHeight')
            header = root.locator('header').bounding_box()
            assert abs(header['y'] - box['y']) < 2
            record(label + ' Edit bottom reachable by wheel; toolbar stays visible')
            capture_browser_view(cdp, out / f'edit-{width}-{factor}.png')
            nav('Settings')
            expand('.settings-page .feature-disclosure > summary')
            page_wheel(page.locator('.settings-page'), label + ' expanded Settings scrolls')
            nav('Components')
            expand('.app-shell > main details > summary')
            root = page.locator('.app-shell > main')
            if root.evaluate('e=>e.scrollHeight-e.clientHeight') > 100:
                page_wheel(root, label + ' expanded Components scrolls')
            nav('Uploads')
            expand('.upload-history-actions details > summary')
            root = page.locator('.app-shell > div').last
            if root.evaluate('e=>e.scrollHeight-e.clientHeight') > 100:
                page_wheel(root, label + ' Uploads scrolls')
            demo = page.get_by_role('button', name='Analyze ptt_demo_run_a', exact=True)
            if demo.count():
                demo.click()
                settle()
            nav('Split')
            expect(page.get_by_role('button',name='Add test point',exact=True)).to_be_enabled()
            for _ in range(2):
                add = page.get_by_role('button',name='Add line plot',exact=True)
                if add.is_enabled():
                    add.click()
                    settle()
            root = page.locator('.feature-split')
            reset(root)
            over = root.locator('.u-over').first
            x,y = center_visible(over, root)
            before = root.evaluate('e=>e.scrollTop')
            request_count = len(data_requests)
            wheel(x,y,230)
            assert root.evaluate('e=>e.scrollTop') > before + 20
            assert len(data_requests) == request_count, 'plain Split wheel changed plot range'
            record(label + ' Split canvas scrolls without zoom')
            x,y = center_visible(over, root)
            before = root.evaluate('e=>e.scrollTop')
            request_count = len(data_requests)
            wheel(x,y,-120,'Shift')
            settle()
            assert len(data_requests) > request_count, 'Shift wheel did not update time range'
            assert abs(root.evaluate('e=>e.scrollTop') - before) < 2
            record(label + ' Split Shift wheel zooms time without page scrolling')
            x,y = center_visible(over, root)
            before = root.evaluate('e=>e.scrollTop')
            canvas = root.locator('.uplot canvas').first
            canvas_before = canvas.evaluate('e=>e.toDataURL()')
            request_count = len(data_requests)
            wheel(x,y,-120,'Alt')
            assert canvas.evaluate('e=>e.toDataURL()') != canvas_before, 'Alt wheel did not change plot rendering'
            assert len(data_requests) == request_count, 'Alt wheel changed X range'
            assert abs(root.evaluate('e=>e.scrollTop') - before) < 2
            record(label + ' Split Alt wheel zooms Y without changing time or page position')
            nav('Analyze')
            # Keep a usable plot area in the minimum-height viewport; this
            # checks the shared wheel handler, not Analyze's grid sizing.
            hide_scatter = page.get_by_role('button',name='Hide scatter panel',exact=True)
            if hide_scatter.count():
                hide_scatter.click()
            page.get_by_role('button',name='Full test',exact=True).click()
            page.get_by_role('group',name='Plot layout',exact=True).get_by_role('button',name='1',exact=True).click()
            settle()
            unavailable = page.locator('select[aria-label="Variable for plot 1"]')
            if unavailable.count():
                unavailable.select_option(index=1)
                settle()
            over = page.locator('.analyze-plots-pane .u-over').first
            expect(over).to_be_visible()
            box = over.bounding_box()
            request_count = len(data_requests)
            wheel(box['x']+box['width']*.55,box['y']+box['height']*.5,-120)
            settle()
            assert len(data_requests) > request_count, 'Analyze plain wheel no longer zooms'
            record(label + ' Analyze plain wheel still zooms')
        assert not report['errors'], report['errors']
        assert not report['blockedWrites'], report['blockedWrites']
    except Exception:
        capture_browser_view(cdp, out/'failure.png')
        print(page.locator('body').inner_text()[-4000:],flush=True)
        raise
    finally:
        (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        context.close()
print('PASS', len(report['checks']), 'checks;', out, flush=True)