"""Verify desktop browser zoom with an isolated Chromium extension and API fixtures.

Start Vite, then run: python scripts/verify_browser_zoom.py --url http://127.0.0.1:3100
Requires Python Playwright and its full Chromium browser (`playwright install chromium`).
Uses chrome.tabs.setZoom: this changes real browser zoom, including devicePixelRatio
and the CSS layout viewport. No user profile, installed extensions, or backend data
are accessed. Temporary extension/profile files are removed when the run completes.
"""
import argparse
import base64
import json
import re
from pathlib import Path
import tempfile

from playwright.sync_api import sync_playwright

from verify_rendering import configure, check_upload


def capture_browser_view(cdp, path):
    # Capture the surface without a clip: Playwright's layout-sized clip crops screenshots
    # when chrome.tabs.setZoom changes CSS pixels without viewport emulation.
    result = cdp.send('Page.captureScreenshot', {'fromSurface': True})
    path.write_bytes(base64.b64decode(result['data']))


def set_browser_zoom(worker, page, factor):
    result = worker.evaluate("""async ({url, factor}) => {
        const tabs = await chrome.tabs.query({});
        const tab = tabs.find(tab => tab.url === url);
        if (!tab) throw new Error(`App tab not found: ${url}`);
        await chrome.tabs.setZoom(tab.id, factor);
        return chrome.tabs.getZoom(tab.id);
    }""", {"url": page.url, "factor": factor})
    assert abs(result - factor) < 0.001, result
    return result


def check_crosshair(page, label):
    plot = page.locator(".u-over").first
    plot.wait_for(state="visible")
    plot.scroll_into_view_if_needed()
    box = plot.bounding_box()
    assert box and box["width"] > 40 and box["height"] > 40, box
    x = box["x"] + box["width"] * 0.63
    y = box["y"] + box["height"] * 0.41
    page.mouse.move(x, y)
    geometry = plot.evaluate("""over => {
        const x = over.querySelector('.u-cursor-x').getBoundingClientRect();
        const y = over.querySelector('.u-cursor-y').getBoundingClientRect();
        return {x: x.left, y: y.top};
    }""")
    delta = {"x": abs(geometry["x"] - x), "y": abs(geometry["y"] - y)}
    assert delta["x"] <= 2 and delta["y"] <= 2, (label, delta)
    print(f"{label}: cursor offset {delta}")


def wait_for_chart_layout(page):
    # Two consecutive frames with the same canvas and parent bounds allow
    # React's ResizeObserver-driven uPlot rebuild to settle after browser zoom.
    page.evaluate("""() => new Promise((resolve, reject) => {
        const samples = [];
        const timer = setTimeout(() => reject(new Error('Chart layout did not settle: ' +
            JSON.stringify(samples.slice(-4)))), 10000);
        let previous = '', stable = 0;
        const frame = () => {
            const elements = [...document.querySelectorAll('.u-over')];
            const current = JSON.stringify(elements.map(el => {
                const r = el.getBoundingClientRect();
                return [r.x, r.y, r.width, r.height];
            }));
            samples.push(current);
            stable = elements.length && current === previous ? stable + 1 : 0;
            previous = current;
            if (stable >= 2) { clearTimeout(timer); resolve(); }
            else requestAnimationFrame(frame);
        };
        requestAnimationFrame(frame);
    })""")


def check_zoomed_tooltip(page):
    dot = page.locator('.recharts-scatter-symbol').nth(2).locator('circle[fill]:not([fill="none"])').first
    dot.hover()
    tooltip = page.get_by_role('tooltip')
    tooltip.wait_for()
    assert tooltip.evaluate("""el => {
        const r = el.getBoundingClientRect();
        return el.scrollWidth <= el.clientWidth && r.left >= 0 && r.top >= 0
            && r.right <= innerWidth && r.bottom <= innerHeight;
    }"""), 'zoomed tooltip must wrap within the visible viewport'
    page.keyboard.press('Escape')
    page.get_by_role('button', name=re.compile('^More')).click()
    page.get_by_role('switch', name='Show horizontal X-axis minimum-to-maximum range bars').click()
    page.get_by_role('switch', name='Show vertical Y-axis minimum-to-maximum range bars').click()
    page.keyboard.press('Escape')
    assert page.locator('.scatter-range-series line').count() == 24


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3100")
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / "data" / "verification" / "browser-zoom"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ptt-browser-zoom-") as temporary:
        temporary = Path(temporary)
        extension = temporary / "extension"
        extension.mkdir()
        (extension / "manifest.json").write_text(json.dumps({
            "manifest_version": 3, "name": "Isolated plot browser zoom verification",
            "version": "1.0", "permissions": ["tabs"],
            "background": {"service_worker": "background.js"},
        }), encoding="utf-8")
        (extension / "background.js").write_text(
            "chrome.runtime.onInstalled.addListener(() => {});", encoding="utf-8")
        with sync_playwright() as playwright:
            print("Launching isolated Chromium with browser zoom extension", flush=True)
            context = playwright.chromium.launch_persistent_context(
                str(temporary / "profile"), channel="chromium", headless=True,
                no_viewport=True, timeout=20000,
                args=[f"--disable-extensions-except={extension}",
                      f"--load-extension={extension}", "--window-size=1440,1000"],
            )
            try:
                worker = (context.service_workers[0] if context.service_workers
                          else context.wait_for_event("serviceworker", timeout=20000))
                print("Browser zoom extension ready", flush=True)
                page = context.pages[0]
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
                configure(page)
                page.goto(args.url)
                page.wait_for_load_state("networkidle")
                page.locator(".u-over").first.wait_for()
                baseline = page.evaluate("({dpr: devicePixelRatio, width: innerWidth})")
                print("100% browser metrics:", baseline)
                cdp = context.new_cdp_session(page)
                window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
                for factor in [1.25, 1.5]:
                    cdp.send("Browser.setWindowBounds", {"windowId": window_id,
                             "bounds": {"width": 1440, "height": 1000}})
                    set_browser_zoom(worker, page, factor)
                    page.wait_for_function("factor => Math.abs(devicePixelRatio - factor) < .001",
                                           arg=baseline["dpr"] * factor)
                    metrics = page.evaluate("({dpr: devicePixelRatio, width: innerWidth})")
                    assert abs(metrics["width"] * factor - baseline["width"]) <= 2, metrics
                    print(f"{factor * 100:.0f}% browser metrics:", metrics)
                    wait_for_chart_layout(page)
                    capture_browser_view(cdp, output / f"grid-{factor}.png")
                    check_zoomed_tooltip(page)
                    check_crosshair(page, f"{factor}: grid")
                    expand = page.get_by_role("button", name="Expand torque_nm", exact=True)
                    expand.focus()
                    page.keyboard.press("Enter")
                    wait_for_chart_layout(page)
                    check_crosshair(page, f"{factor}: maximized")
                    capture_browser_view(cdp, output / f"maximized-{factor}.png")
                    cdp.send("Browser.setWindowBounds", {"windowId": window_id,
                             "bounds": {"width": 1260, "height": 940}})
                    wait_for_chart_layout(page)
                    check_crosshair(page, f"{factor}: maximized after resize")
                    restore = page.get_by_role("button", name="Minimize torque_nm", exact=True)
                    restore.focus()
                    page.keyboard.press("Enter")
                    wait_for_chart_layout(page)
                    check_crosshair(page, f"{factor}: restored")
                    factor_output = output / str(factor)
                    factor_output.mkdir(exist_ok=True)
                    check_upload(page, factor_output)
                    # Analyze opens a fresh test selection. Reload to restore
                    # the isolated, seeded test-point selection for the next case.
                    page.reload()
                    page.wait_for_load_state("networkidle")
                    page.locator(".u-over").first.wait_for()
                assert not errors, errors
                print("PASS: real 125%/150% browser zoom, cursor geometry, keyboard maximize/restore, resize, CSV alignment")
            finally:
                context.close()


if __name__ == "__main__":
    main()
