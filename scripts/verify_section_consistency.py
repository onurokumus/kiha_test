"""Read-only production integration checks for the five companion sections.

Uses a disposable Chromium profile, existing test metadata and GET-only API
access. Browser zoom is real Chromium zoom through a temporary extension.
Run after npm run build, with the local /ptt/ preview running.
"""
import argparse
import json
import os
import tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
from verify_browser_zoom import capture_browser_view, set_browser_zoom


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8087/ptt/')
    args = parser.parse_args()
    output = Path(os.environ.get('TEMP', '/tmp')) / 'ptt-section-consistency'
    output.mkdir(exist_ok=True)
    errors, writes, results = [], [], []
    with tempfile.TemporaryDirectory(prefix='ptt-sections-') as temporary, sync_playwright() as p:
        extension = Path(temporary) / 'extension'
        extension.mkdir()
        (extension / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'Section zoom checks',
            'version': '1.0', 'permissions': ['tabs'], 'background': {'service_worker': 'background.js'}}))
        (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(()=>{});')
        context = p.chromium.launch_persistent_context(str(Path(temporary) / 'profile'), channel='chromium',
            headless=True, no_viewport=True, args=[f'--disable-extensions-except={extension}',
            f'--load-extension={extension}', '--window-size=1600,1000'])
        page = context.pages[0]
        page.set_default_timeout(15000)
        page.on('pageerror', lambda error: errors.append(str(error)))
        def api_guard(route):
            if route.request.method not in ('GET', 'HEAD', 'OPTIONS'):
                writes.append([route.request.method, route.request.url]); route.abort()
            else: route.continue_()
        page.route('**/api/**', api_guard)
        try:
            page.goto(args.url)
            page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='Uploads', exact=True).click()
            analyze = page.get_by_role('button', name='Analyze ptt_demo_run_a', exact=True)
            if analyze.count(): analyze.click()
            else: page.get_by_role('button', name='Analyze', exact=True).click()
            page.wait_for_load_state('networkidle')
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            cdp = context.new_cdp_session(page)
            window = cdp.send('Browser.getWindowForTarget')['windowId']
            for width, height, factor in [(1600,1000,1), (1280,900,1), (1100,800,1), (960,700,1),
                                           (800,700,1), (1600,1000,1.25), (1600,1000,1.5), (1100,900,1.5), (960,500,1), (960,400,1.5)]:
                cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'width': width, 'height': height}})
                set_browser_zoom(worker, page, factor)
                for label in ['Split', 'Edit', 'Uploads', 'Components', 'Settings']:
                    page.get_by_role('button', name=label, exact=True).click()
                    page.wait_for_load_state('networkidle')
                    if label == 'Split':
                        expect(page.get_by_role('button', name='Add test point', exact=True)).to_be_enabled()
                        expect(page.locator('.feature-split .u-over').first).to_be_visible()
                    elif label == 'Edit':
                        expect(page.get_by_role('textbox', name='Description', exact=True)).to_be_visible()
                    elif label == 'Components':
                        expect(page.get_by_role('button', name='Refresh statistics', exact=True)).to_be_enabled()
                    page.evaluate('''async () => { await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))); }''')
                    geometry = page.evaluate('''() => ({viewport:innerWidth, page:document.documentElement.scrollWidth,
                        nav:[...document.querySelectorAll('.app-nav-button')].every(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1;})})''')
                    assert geometry['page'] <= geometry['viewport'] + 1, (label,width,factor,geometry)
                    assert geometry['nav'], (label,width,factor,'nav clipped')
                    primary = {'Split':'Save test points','Edit':'Save notes and metadata','Components':'Refresh statistics','Settings':'Save'}
                    if label in primary:
                        control = page.get_by_role('button', name=primary[label], exact=True)
                        control.scroll_into_view_if_needed()
                        assert control.evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1;}'), (label,width,factor,'primary clipped')
                    if label in ('Split','Settings'):
                        trigger = page.get_by_role('button', name='Test-point files', exact=True) if label=='Split' else page.locator('summary[aria-label="More settings actions"]')
                        trigger.focus(); page.keyboard.press('Enter')
                        action = page.get_by_role('button', name='Export JSON', exact=True)
                        expect(action).to_be_visible()
                        panel = page.get_by_role('region', name='Test-point files', exact=True) if label == 'Split' else page.locator('summary[aria-label="More settings actions"] + div')
                        for menu_action in panel.get_by_role('button').all():
                            menu_action.scroll_into_view_if_needed()
                            assert menu_action.evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1;}'), (label,width,factor,menu_action.inner_text(),'menu action clipped')
                        action.scroll_into_view_if_needed()
                        assert action.evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1;}'), (label,width,factor,'menu clipped')
                        page.keyboard.press('Escape'); expect(action).not_to_be_visible(); expect(trigger).to_be_focused()
                    capture_browser_view(cdp, output / f'{label.lower()}-{width}-{height}-{factor}.png')
                    results.append({'section':label,'width':width,'height':height,'zoom':factor,**geometry})
                print(f'PASS: five sections at {width}x{height}, zoom {factor}',flush=True)
            # Direct links must open the exact component editor, without a second search.
            set_browser_zoom(worker,page,1)
            cdp.send('Browser.setWindowBounds', {'windowId':window,'bounds':{'width':1600,'height':1000}})
            for origin, prefix in [('Components','Edit component settings for '),('Uploads','Edit components for ')]:
                page.get_by_role('button',name=origin,exact=True).click(); page.wait_for_load_state('networkidle')
                if origin == 'Components':
                    expect(page.get_by_role('button', name='Refresh statistics', exact=True)).to_be_enabled()
                buttons=page.get_by_role('button',name=prefix+'ptt_demo_run_a',exact=True)
                expect(buttons).to_be_visible()
                buttons.click()
                expect(page.get_by_role('group',name='Component set 1',exact=True)).to_be_visible()
                expect(page.get_by_role('combobox',name='RPM column',exact=True)).to_be_visible()
                results.append({'direct_component_edit':origin})
            # Search reset leaves the toolbar in place and restores the unfiltered view.
            page.get_by_role('button',name='Components',exact=True).click()
            search=page.get_by_role('searchbox',name='Find component',exact=True)
            before=search.bounding_box(); search.fill('No matching component')
            clear=page.get_by_role('button',name='Clear filters',exact=True)
            expect(clear).to_be_enabled(); clear.click(); expect(search).to_have_value(''); expect(clear).to_be_disabled()
            after=search.bounding_box(); assert abs(before['y']-after['y'])<1 and abs(before['height']-after['height'])<1
            assert not errors,errors
            assert not writes,writes
            (output/'report.json').write_text(json.dumps({'checks':results,'page_errors':errors,'blocked_writes':writes},indent=2))
            print(f'PASS: {len(results)} integration checks; no page errors or API writes; evidence {output}',flush=True)
        except Exception:
            page.screenshot(path=str(output/'failure.png'))
            (output/'failure.txt').write_text(page.locator('body').aria_snapshot(),encoding='utf-8')
            raise
        finally: context.close()

if __name__=='__main__': run()
