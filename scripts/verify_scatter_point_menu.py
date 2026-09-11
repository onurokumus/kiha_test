"""Check overlapping-point menus with isolated read-only API fixtures.

Start Vite, then run: python scripts/verify_scatter_point_menu.py --url http://127.0.0.1:3151
Requires Python Playwright and its full Chromium browser for real 125%/150% zoom.
No backend or user browser profile is used; screenshots are ignored verification output.
"""
import argparse
import json
from pathlib import Path
import tempfile

from playwright.sync_api import expect, sync_playwright

import verify_rendering as fixtures
from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout


def configure(page):
    # Five coincident points near the right/bottom of the scatter force the
    # picker to cross the resize rail and exercise long-name wrapping/scrolling.
    fixtures.POINTS = [
        {"id": i, "name": f"Point_{i}_" + "long_point_name_" * 4,
         "label": "Label_" + "unbroken" * 14, "start_s": (i - 1) * 5, "end_s": i * 5}
        for i in range(1, 8)
    ]
    fixtures.SOURCE["test_points"] = [
        {"id": point["id"], "revision": f"point-{point['id']}"} for point in fixtures.POINTS
    ]
    fixtures.MEANS = {column: [90, 90, 90, 90, 90, 10, 50] for column in fixtures.COLS}
    fixtures.MEANS[fixtures.Y] = [20, 20, 20, 20, 20, 80, 50]
    fixtures.configure(page)
    page.add_init_script("""const settings = JSON.parse(localStorage.getItem('ptt.settings.v1'));
        settings.clustering = true;
        localStorage.setItem('ptt.settings.v1', JSON.stringify(settings));
        const session = JSON.parse(localStorage.getItem('ptt.analysis-session.v1'));
        session.selections[0].tpId = 6;
        localStorage.setItem('ptt.analysis-session.v1', JSON.stringify(session));""")


def open_menu(page):
    cluster = page.locator('.recharts-scatter-symbol').filter(has=page.get_by_text('5', exact=True))
    expect(cluster).to_have_count(1)
    cluster.locator('circle').first.click()
    menu = page.get_by_role('dialog', name='Overlapping test points')
    expect(menu).to_be_visible()
    expect(menu.get_by_role('textbox', name='Search overlapping test points')).to_be_focused()
    return menu


def check_geometry(page, menu, label, cross_rail=False):
    geometry = menu.evaluate("""menu => {
        const r = menu.getBoundingClientRect();
        const rail = document.querySelector('.analyze-resize-rail').getBoundingClientRect();
        const railPoint = rail.width < rail.height
          ? [rail.left + rail.width / 2, Math.max(r.top + 20, rail.top + 4)]
          : [Math.max(r.left + 20, rail.left + 4), rail.top + rail.height / 2];
        const probes = [[r.left + 12, r.top + 12], [r.right - 12, r.top + 12],
          [r.right - 12, r.bottom - 12]];
        const railInside = railPoint[0] > r.left && railPoint[0] < r.right &&
          railPoint[1] > r.top && railPoint[1] < r.bottom;
        if (railInside) probes.push(railPoint);
        return {bodyPortal: menu.parentElement === document.body,
          inside: r.left >= 9 && r.top >= 9 && r.right <= innerWidth - 9 && r.bottom <= innerHeight - 9,
          width: r.width, height: r.height, railInside,
          noHorizontalOverflow: [...menu.querySelectorAll('*'), menu].every(el => el.scrollWidth <= el.clientWidth + 1),
          above: probes.every(([x, y]) => menu.contains(document.elementFromPoint(x, y)))};
    }""")
    assert geometry['bodyPortal'] and geometry['inside'] and geometry['above'], (label, geometry)
    assert geometry['noHorizontalOverflow'], (label, geometry)
    if cross_rail:
        assert geometry['railInside'], (label, geometry)
    print(label, geometry, flush=True)


def check_interactions(page, output):
    menu = open_menu(page)
    check_geometry(page, menu, 'initial: above resize rail and neighboring plots', cross_rail=True)
    page.screenshot(path=str(output / 'scatter-cluster-menu.png'))
    search = menu.get_by_role('textbox', name='Search overlapping test points')
    search.fill('Point_2_')
    point = menu.locator('button[aria-pressed]')
    expect(point).to_have_count(1)
    expect(point).to_have_attribute('aria-pressed', 'false')
    page.keyboard.press('Tab')
    expect(point).to_be_focused()
    page.keyboard.press('Enter')
    expect(point).to_have_attribute('aria-pressed', 'true')
    expect(menu).to_be_visible()
    page.keyboard.press('Space')
    expect(point).to_have_attribute('aria-pressed', 'false')
    search.fill('')
    expect(menu.locator('button[aria-pressed]')).to_have_count(5)
    point_list = menu.locator('button[aria-pressed]').first.locator('..')
    assert point_list.evaluate('el => el.scrollHeight > el.clientHeight')
    scatter_before = page.locator('.recharts-xAxis').text_content()
    box = point_list.bounding_box()
    page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
    page.mouse.wheel(0, 220)
    page.wait_for_function("document.querySelector('[data-menu-container] button[aria-pressed]').parentElement.scrollTop > 0")
    expect(menu).to_be_visible()
    assert page.locator('.recharts-xAxis').text_content() == scatter_before

    # The first outside click must maximize the plot, rather than disappear into
    # a modal backdrop or focus restoration. Multi-selection used to consume it.
    page.get_by_role('button', name='Expand torque_nm', exact=True).click()
    expect(menu).to_have_count(0)
    expect(page.locator('.u-over')).to_have_count(1)
    page.get_by_role('button', name='Minimize torque_nm', exact=True).click()
    wait_for_chart_layout(page)
    menu = open_menu(page)
    page.keyboard.press('Escape')
    expect(menu).to_have_count(0)
    expect(page.get_by_label('Plot canvas for test-point overview', exact=True)).to_be_focused()

    menu = open_menu(page)
    page.set_viewport_size({'width': 1100, 'height': 650})
    wait_for_chart_layout(page)
    check_geometry(page, menu, 'menu remains usable after desktop resize')
    menu.get_by_role('button', name='Close point selection').click()
    expect(menu).to_have_count(0)
    expect(page.get_by_label('Plot canvas for test-point overview', exact=True)).to_be_focused()
    print('PASS: mouse/keyboard multi-select, search, scrolling, first outside click, Escape/focus and resize', flush=True)


def check_zoom(playwright, url, output):
    with tempfile.TemporaryDirectory(prefix='ptt-scatter-menu-') as temporary:
        temporary = Path(temporary)
        extension = temporary / 'extension'
        extension.mkdir()
        (extension / 'manifest.json').write_text(json.dumps({
            'manifest_version': 3, 'name': 'Isolated scatter menu zoom check', 'version': '1.0',
            'permissions': ['tabs'], 'background': {'service_worker': 'background.js'},
        }), encoding='utf-8')
        (extension / 'background.js').write_text('chrome.runtime.onInstalled.addListener(() => {});', encoding='utf-8')
        context = playwright.chromium.launch_persistent_context(
            str(temporary / 'profile'), channel='chromium', headless=True, no_viewport=True,
            args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--window-size=1440,1000'])
        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            page = context.pages[0]
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
            configure(page)
            page.goto(url)
            page.wait_for_load_state('networkidle')
            cdp = context.new_cdp_session(page)
            baseline_dpr = page.evaluate('devicePixelRatio')
            for factor in [1.25, 1.5]:
                set_browser_zoom(worker, page, factor)
                page.wait_for_function('dpr => Math.abs(devicePixelRatio - dpr) < .001', arg=baseline_dpr * factor)
                wait_for_chart_layout(page)
                menu = open_menu(page)
                check_geometry(page, menu, f'{factor * 100:.0f}% real browser zoom')
                capture_browser_view(cdp, output / f'scatter-cluster-menu-{factor}.png')
                page.keyboard.press('Escape')
                expect(menu).to_have_count(0)
            assert not errors, errors
        finally:
            context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:3151')
    parser.add_argument('--inspect', action='store_true')
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / 'data' / 'verification' / 'scatter-point-menu'
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
            configure(page)
            page.goto(args.url)
            page.wait_for_load_state('networkidle')
            page.locator('.u-over').first.wait_for()
            if args.inspect:
                print(page.locator('.recharts-scatter-symbol').evaluate_all('els => els.map(el => el.outerHTML)'))
                print(json.dumps(page.locator('button').evaluate_all('els => els.map(el => [el.innerText, el.getAttribute("aria-label")])')))
                print(page.evaluate('({settings: localStorage.getItem("ptt.settings.v1"), session: localStorage.getItem("ptt.analysis-session.v1")})'))
                page.screenshot(path=str(output / 'inspect.png'))
                return
            check_interactions(page, output)
            assert not errors, errors
        finally:
            browser.close()
        check_zoom(playwright, args.url, output)
    print('PASS: scatter point menu regression; zero browser errors; all APIs read-only mocked fixtures', flush=True)


if __name__ == '__main__':
    main()
