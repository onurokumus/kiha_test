"""Read-only hover-tooltip regression with existing demos in isolated Chromium.

Run with Python/Playwright while the built /ptt/ preview and backend are running.
Only GET/HEAD/OPTIONS reach the API; browser state and temporary DOM probes are
disposable. --reproduce captures the original stuck-focus behavior before a fix.
Reports/screenshots go to %TEMP%/ptt-hover-tooltips. Real browser zoom uses the
existing isolated-extension helper, never CSS zoom or a user's browser profile.
"""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile
import traceback
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

from verify_browser_zoom import capture_browser_view, set_browser_zoom, wait_for_chart_layout

KEY = "ptt.analysis-session.v1"
PLOTS = '.analyze-plots-pane [role="group"][aria-label$=" plot"]'
TIP = "#page-hover-tooltip"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8087/ptt/")
    parser.add_argument("--reproduce", action="store_true")
    args = parser.parse_args()
    args.url = args.url.rstrip("/") + "/"
    output = Path(os.environ.get("TEMP", "/tmp")) / "ptt-hover-tooltips"
    output.mkdir(parents=True, exist_ok=True)
    report = {"checks": [], "apiWrites": [], "pageErrors": [], "geometry": []}
    with urlopen(args.url + "api/analysis-sources", timeout=30) as response:
        sources = json.load(response)["sources"]
    demo = next((source for source in sources if source["name"] == "ptt_demo_run_a"), None)
    assert demo and demo["test_points"], "Existing ptt_demo_run_a fixture is required"
    session = dict(version=1, sources=sources, currentTest=demo["name"],
        xAxis="rpm", yAxis="thrust_n", axesUserSet=True,
        selections=[{"test": demo["name"], "tpId": demo["test_points"][0]["id"], "hidden": False}],
        plotDensity="quad", plotConfigs=["thrust_n", "rpm", "torque_nm", "voltage_v"],
        plotsUserEdited=True, viewMode="xy", scatterCollapsed=False, scatterRatio=40,
        xyXCols=["rpm"] * 9, xyYCols=["thrust_n", "rpm", "torque_nm", "voltage_v"] + ["thrust_n"] * 5,
        xySource="full", specSource="full", specMode="fft", fullPlotMode="auto",
        fullPlotExtraColumns=[["torque_nm"]] + [[] for _ in range(8)])

    def passed(name):
        report["checks"].append(name)
        print("PASS:", name, flush=True)

    def guard(route):
        if route.request.method not in {"GET", "HEAD", "OPTIONS"}:
            report["apiWrites"].append({"url": route.request.url, "method": route.request.method})
            route.abort()
        else:
            route.continue_()

    with tempfile.TemporaryDirectory(prefix="ptt-hover-tooltip-") as temporary, sync_playwright() as playwright:
        temporary = Path(temporary)
        extension = temporary / "extension"
        extension.mkdir()
        (extension / "manifest.json").write_text(json.dumps({"manifest_version": 3,
            "name": "PTT isolated tooltip zoom", "version": "1.0", "permissions": ["tabs"],
            "background": {"service_worker": "background.js"}}), encoding="utf-8")
        (extension / "background.js").write_text("chrome.runtime.onInstalled.addListener(()=>{});", encoding="utf-8")
        context = playwright.chromium.launch_persistent_context(str(temporary / "profile"),
            channel="chromium", headless=True, no_viewport=True, color_scheme="light",
            reduced_motion="reduce", args=[f"--disable-extensions-except={extension}",
                f"--load-extension={extension}", "--window-size=1600,1000"])
        context.route("**/api/**", guard)
        context.add_init_script(f"""if (!localStorage.getItem('{KEY}'))
          localStorage.setItem('{KEY}', {json.dumps(json.dumps(session))});
          localStorage.setItem('ptt.theme.v1','light');""")
        page = context.pages[0]
        page.set_default_timeout(15000)
        page.on("pageerror", lambda error: report["pageErrors"].append(str(error)))
        cdp = context.new_cdp_session(page)
        window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")

        def hidden():
            expect(page.locator(TIP)).to_have_count(0)

        def fresh_hover(target, text=None):
            page.mouse.move(0, 0)
            target.hover()
            expect(page.locator(TIP)).to_be_visible()
            if text is not None:
                expect(page.locator(TIP)).to_have_text(text)

        def switch():
            return page.get_by_role("switch", name="Dark mode", exact=True)

        def ready():
            page.wait_for_load_state("networkidle")
            expect(page.locator(PLOTS).first.locator(".u-over")).to_be_visible()
            wait_for_chart_layout(page)

        def resize(width, height=1000):
            chrome_width = page.evaluate("outerWidth-innerWidth")
            cdp.send("Browser.setWindowBounds", {"windowId": window_id,
                "bounds": {"width": width + chrome_width, "height": height}})
            page.wait_for_function("width=>innerWidth===width", arg=width)
            wait_for_chart_layout(page)

        def bounded(label):
            result = page.locator(TIP).evaluate("""el=>{
              const r=el.getBoundingClientRect();
              return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:innerWidth,height:innerHeight,
                overflow:el.scrollWidth-el.clientWidth,dpr:devicePixelRatio};
            }""")
            report["geometry"].append({"label": label, **result})
            assert result["x"] >= 0 and result["y"] >= 0 and result["right"] <= result["width"] + 1
            assert result["bottom"] <= result["height"] + 1 and result["overflow"] <= 1, result

        try:
            page.goto(args.url)
            ready()
            # Rendered controls are recorded before any interaction assertions.
            report["initialControls"] = page.locator("button").evaluate_all("""els=>els.map(el=>({
              name:el.getAttribute('aria-label')||el.innerText,title:el.getAttribute('title'),
              tooltip:el.getAttribute('data-tooltip')}))""")
            capture_browser_view(cdp, output / ("before.png" if args.reproduce else "initial.png"))
            canvas = page.locator(PLOTS).first.locator('[aria-label^="Plot canvas for"]')

            if args.reproduce:
                fresh_hover(switch(), "Toggle dark mode")
                switch().click()
                page.mouse.move(0, 0)
                page.wait_for_timeout(1500)
                report["stuckAfterClickLeave"] = page.locator(TIP).all_text_contents()
                report["switchStillFocused"] = switch().evaluate("el=>el===document.activeElement")
                assert report["stuckAfterClickLeave"] == ["Toggle dark mode"]
                assert report["switchStillFocused"]
                passed("Original bug reproduced: clicked control retains tooltip after pointer leaves for 1.5s")
                canvas.hover(position={"x": 80, "y": 80})
                expect(page.locator(TIP)).to_contain_text("Drag to zoom")
                canvas.focus()
                page.mouse.move(0, 0)
                page.wait_for_timeout(700)
                expect(page.locator(TIP)).to_contain_text("Drag to zoom")
                report["xyStuckAfterClickLeave"] = page.locator(TIP).inner_text()
                capture_browser_view(cdp, output / "before-stuck-xy.png")
                passed("Original screenshot bug reproduced: XY drag-instruction tooltip sticks after canvas focus and leave")
                return

            page.mouse.move(0, 0)
            switch().hover()
            page.wait_for_timeout(80)
            hidden()
            expect(page.locator(TIP)).to_have_text("Toggle dark mode")
            bounded("delayed-icon-hover")
            page.mouse.move(0, 0)
            hidden()
            passed("Useful icon tooltip is delayed on hover and clears on mouseout")

            fresh_hover(switch(), "Toggle dark mode")
            before = switch().get_attribute("aria-checked")
            switch().click()
            hidden()
            expect(switch()).to_be_focused()
            assert switch().get_attribute("aria-checked") != before
            page.mouse.move(0, 0)
            page.wait_for_timeout(450)
            hidden()
            # A subsequent explicit hover must still work after dismissal.
            fresh_hover(switch(), "Toggle dark mode")
            page.mouse.move(0, 0)
            hidden()
            passed("Click dismisses immediately; pointer-focused controls cannot keep the tooltip after mouseout")

            page.get_by_role("button", name="Sessions", exact=True).focus()
            page.keyboard.press("Tab")
            expect(switch()).to_be_focused()
            expect(page.locator(TIP)).to_have_text("Toggle dark mode")
            description = switch().get_attribute("aria-describedby") or ""
            assert "page-hover-tooltip" in description
            page.keyboard.press("Escape")
            hidden()
            expect(switch()).to_be_focused()
            assert "page-hover-tooltip" not in (switch().get_attribute("aria-describedby") or "")
            passed("Native Tab focus reveals useful help; Escape dismisses while preserving focus and ARIA cleanup")
            for key in ["Space", "Enter"]:
                page.keyboard.press("Shift+Tab")
                page.keyboard.press("Tab")
                expect(switch()).to_be_focused()
                expect(page.locator(TIP)).to_have_text("Toggle dark mode")
                previous = switch().get_attribute("aria-checked")
                page.keyboard.press(key)
                hidden()
                assert switch().get_attribute("aria-checked") != previous
            passed("Space and Enter activation dismiss keyboard help without reopening it")

            canvas.hover(position={"x": 80, "y": 80})
            page.wait_for_timeout(450)
            hidden()
            canvas.click(position={"x": 80, "y": 80})
            page.mouse.move(0, 0)
            hidden()
            assert not canvas.get_attribute("title") and not canvas.get_attribute("data-tooltip")
            plot = page.locator(PLOTS).first
            plot.get_by_role("button", name=re.compile("^Plot actions for ")).click()
            page.get_by_role("menuitem", name="Analysis details…", exact=True).click()
            dialog = page.get_by_role("dialog", name=re.compile("^XY details for "))
            expect(dialog).to_contain_text("Drag to zoom. Shift-drag or middle-drag to pan.")
            page.keyboard.press("Escape")
            hidden()
            passed("XY canvas has no redundant gesture tooltip; Analysis details keeps the interaction guide")

            # Browser-only probes exercise lifecycle events without mutating any
            # application data or relying on a pointer click to hide the bubble.
            page.evaluate("""() => {
              const wrapper=document.createElement('div'); wrapper.id='tooltip-lifecycle-probe';
              wrapper.style.cssText='position:fixed;left:20px;bottom:20px;z-index:1000';
              const button=document.createElement('button');button.textContent='Lifecycle probe';
              button.title='Temporary lifecycle help';button.setAttribute('aria-describedby','existing-help');
              const second=document.createElement('button');second.textContent='Second probe';
              second.title='Second lifecycle help';wrapper.append(button,second);document.body.append(wrapper);
            }""")
            probe = page.locator("#tooltip-lifecycle-probe button").first
            second_probe = page.locator("#tooltip-lifecycle-probe button").nth(1)
            fresh_hover(probe, "Temporary lifecycle help")
            assert probe.get_attribute('title') == '', 'Native title must remain suppressed during custom hover'
            expect(probe).to_have_attribute('aria-describedby', 'existing-help page-hover-tooltip')
            second_probe.hover()
            expect(page.locator(TIP)).to_have_text('Second lifecycle help')
            expect(probe).to_have_attribute('title', 'Temporary lifecycle help')
            expect(probe).to_have_attribute('aria-describedby', 'existing-help')
            assert not probe.get_attribute('data-page-tooltip-title')
            probe.hover()
            expect(page.locator(TIP)).to_have_text('Temporary lifecycle help')
            expect(second_probe).to_have_attribute('title', 'Second lifecycle help')
            passed('Direct hover between titled controls survives old-target observer cleanup and restores original ARIA')
            probe.evaluate("el=>el.title='Updated lifecycle help'")
            hidden()
            expect(probe).to_have_attribute('title', 'Updated lifecycle help')
            fresh_hover(probe, 'Updated lifecycle help')
            assert probe.get_attribute('title') == ''
            page.mouse.move(0, 0)
            hidden()
            expect(probe).to_have_attribute('title', 'Updated lifecycle help')
            fresh_hover(probe, 'Updated lifecycle help')
            probe.evaluate("el=>el.removeAttribute('title')")
            hidden()
            assert probe.get_attribute('title') is None
            assert probe.get_attribute('data-page-tooltip-title') is None
            expect(probe).to_have_attribute('aria-describedby', 'existing-help')
            probe.evaluate("el=>el.title='Temporary lifecycle help'")
            fresh_hover(probe, 'Temporary lifecycle help')
            probe.evaluate("el=>el.title=''")
            hidden()
            expect(probe).to_have_attribute('title', '')
            assert probe.get_attribute('data-page-tooltip-title') is None
            probe.evaluate("el=>el.title='Temporary lifecycle help'")
            passed('Dynamic title update/removal/empty values dismiss stale help, avoid duplicate native bubbles and never restore retired text')
            fresh_hover(probe, "Temporary lifecycle help")
            page.evaluate("""() => {
              const box=document.createElement('div');box.id='tooltip-scroll-probe';
              box.style.cssText='position:fixed;width:30px;height:30px;overflow:auto;bottom:0;right:0';
              box.innerHTML='<div style="height:1000px">Scroll probe</div>';document.body.append(box);box.scrollTop=50;
            }""")
            hidden()
            page.evaluate("document.getElementById('tooltip-scroll-probe').remove()")
            passed("Actual nested scroll event dismisses visible tooltip")
            fresh_hover(probe, "Temporary lifecycle help")
            page.evaluate("window.dispatchEvent(new Event('blur'))")
            hidden()
            passed("Window blur dismisses visible tooltip")
            fresh_hover(probe, "Temporary lifecycle help")
            page.locator("#tooltip-lifecycle-probe").evaluate("el=>el.hidden=true")
            hidden()
            page.locator("#tooltip-lifecycle-probe").evaluate("el=>el.hidden=false")
            fresh_hover(probe, "Temporary lifecycle help")
            page.locator("#tooltip-lifecycle-probe").evaluate("el=>el.remove()")
            hidden()
            passed("Hiding an ancestor or removing a target clears tooltip without another pointer event")

            fresh_hover(switch(), "Toggle dark mode")
            resize(1100)
            hidden()
            fresh_hover(switch(), "Toggle dark mode")
            bounded("desktop-1100")
            resize(960)
            hidden()
            fresh_hover(switch(), "Toggle dark mode")
            bounded("desktop-960")
            resize(1600)
            hidden()
            passed("Resizing dismisses help and subsequent hover fits 960/1100px desktop windows")

            expand = page.locator(PLOTS).first.get_by_role("button", name=re.compile("^Expand "))
            fresh_hover(expand, "Expand this plot")
            expand.click()
            hidden()
            wait_for_chart_layout(page)
            restore = page.get_by_role("button", name=re.compile("^Minimize "))
            fresh_hover(restore, "Minimize this plot")
            restore.click()
            hidden()
            wait_for_chart_layout(page)
            expect(page.locator(PLOTS)).to_have_count(4)
            passed("Maximize/restore dismiss action tooltip and do not restore stale Expand/Minimize text")

            page.mouse.move(0, 0)
            dot = page.locator("[data-scatter-hover-target]").first
            expect(dot).to_be_visible()
            dot.hover()
            expect(page.locator(".scatter-tooltip")).to_be_visible()
            expect(page.locator(".scatter-tooltip")).to_contain_text(re.compile(r"\S"))
            page.mouse.move(0, 0)
            expect(page.locator(".scatter-tooltip")).to_have_count(0)
            passed("Scatter point data tooltip is preserved and still dismisses on mouseout")

            controls = page.get_by_role("region", name="Analysis controls", exact=True)
            controls.get_by_role("button", name="Full test", exact=True).click()
            controls.get_by_role("button", name="Time", exact=True).click()
            ready()
            legend = page.get_by_role("button", name=re.compile("^Variable legend for thrust_n, torque_nm$")).first
            fresh_hover(legend)
            expect(page.locator(TIP + " .page-tooltip__legend-item")).to_have_count(2)
            expect(page.locator(TIP)).to_contain_text("thrust_n")
            expect(page.locator(TIP)).to_contain_text("torque_nm")
            colors = page.locator(TIP + " .page-tooltip__legend-line").evaluate_all(
                "els=>els.map(el=>getComputedStyle(el).borderColor)")
            assert len(set(colors)) == 2, colors
            legend.click()
            hidden()
            expect(page.get_by_role("dialog", name="Plot variables and colors", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            hidden()
            passed("Variable tooltip preserves two colored entries; click opens legend with no overlapping bubble")

            base_dpr = page.evaluate("devicePixelRatio")
            for factor in [1.25, 1.5]:
                set_browser_zoom(worker, page, factor)
                page.wait_for_function("value=>Math.abs(devicePixelRatio-value)<.001", arg=base_dpr * factor)
                wait_for_chart_layout(page)
                fresh_hover(switch(), "Toggle dark mode")
                bounded(f"browser-zoom-{factor}")
                capture_browser_view(cdp, output / f"zoom-{factor}.png")
                switch().click()
                hidden()
                page.mouse.move(0, 0)
                hidden()
                expand = page.locator(PLOTS).first.get_by_role("button", name=re.compile("^Expand "))
                expand.focus()
                page.keyboard.press("Enter")
                hidden()
                wait_for_chart_layout(page)
                restore = page.get_by_role("button", name=re.compile("^Minimize "))
                restore.focus()
                page.keyboard.press("Enter")
                hidden()
                wait_for_chart_layout(page)
                passed(f"Actual {factor*100:.0f}% browser zoom: bounded hover, click cleanup, keyboard maximize/restore")
            assert not report["apiWrites"] and not report["pageErrors"], report
            passed("No JavaScript errors or API mutations")
        except Exception:
            report["failure"] = traceback.format_exc()
            capture_browser_view(cdp, output / ("before-failure.png" if args.reproduce else "failure.png"))
            raise
        finally:
            filename = "before-report.json" if args.reproduce else "report.json"
            (output / filename).write_text(json.dumps(report, indent=2), encoding="utf-8")
            context.close()
    print("Evidence:", output, flush=True)


if __name__ == "__main__":
    main()
