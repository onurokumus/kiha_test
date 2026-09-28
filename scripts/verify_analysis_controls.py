"""Read-only desktop checks for the stable analysis control panel.

Uses existing demo sources, an isolated browser profile and GET-only API access.
Requires Python with Playwright and its Chromium browser. The temporary extension
changes actual browser zoom; it never accesses the user's browser profile.
"""

import argparse
import base64
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


KEY = "ptt.analysis-session.v1"
MEASURE = """panel => {
  const rect = element => {
    if (!element) return null;
    const r = element.getBoundingClientRect();
    return {x:r.x, y:r.y, width:r.width, height:r.height};
  };
  const options = panel.querySelector('button[aria-label="Options"]');
  const chips = panel.querySelector('[aria-label="Selected test points"]');
  const exportButton = panel.querySelector('[aria-label="Export selected plots"]');
  return {panel:rect(panel), options:rect(options), chips:rect(chips),
    tray:rect(chips?.parentElement), firstChip:rect(chips?.firstElementChild),
    repeatedSourceSummary:/\\b\\d+ selected points?\\b/i.test(panel.innerText),
    visibleTestLabel:[...panel.querySelectorAll('span')].some(span =>
      span.textContent.trim() === 'Test' && span.getClientRects().length &&
      getComputedStyle(span).visibility !== 'hidden'),
    export:rect(exportButton), panelOverflow:panel.scrollWidth-panel.clientWidth,
    pageOverflow:document.documentElement.scrollWidth-innerWidth,
    chipScroll:chips ? {width:chips.clientWidth, scrollWidth:chips.scrollWidth,
      height:chips.clientHeight, scrollHeight:chips.scrollHeight} : null};
}"""


def same_box(before, after, message, keys=("x", "y", "width", "height")):
    assert before is not None and after is not None, (message, before, after)
    assert all(abs(before[key] - after[key]) <= 1.1 for key in keys), (
        message, before, after)


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8087/ptt/")
    args = parser.parse_args()
    out = Path(os.environ.get("TEMP", "/tmp")) / "ptt-analysis-controls"
    out.mkdir(exist_ok=True)
    with urlopen(args.url + "api/analysis-sources", timeout=20) as response:
        sources = json.load(response)["sources"]
    demos = [source for source in sources
             if source["name"] in {"ptt_demo_run_a", "ptt_demo_run_b"}]
    assert len(demos) == 2, "The two existing demo sources are required"
    selections = [{"test": source["name"], "tpId": point["id"], "hidden": False}
                  for source in demos for point in source["test_points"]]
    assert len(selections) >= 8, "At least eight existing demo test points are required"
    session = {"version": 1, "sources": sources, "currentTest": "ptt_demo_run_a",
               "xAxis": "rpm", "yAxis": "thrust_n", "axesUserSet": True,
               "selections": selections, "plotDensity": "quad", "viewMode": "tp",
               "plotConfigs": ["rpm", "thrust_n", "torque_nm", "voltage_v"],
               "plotsUserEdited": True, "scatterCollapsed": True,
               "xyXCols": ["rpm"] * 4, "xyYCols": ["thrust_n"] * 4,
               "xySource": "tp", "specSource": "tp", "specMode": "fft"}
    report = {"checks": [], "geometry": [], "pageErrors": [], "apiWrites": []}

    with tempfile.TemporaryDirectory(prefix="ptt-analysis-controls-") as temporary, \
            sync_playwright() as playwright:
        temporary = Path(temporary)
        extension = temporary / "extension"
        extension.mkdir()
        (extension / "manifest.json").write_text(json.dumps({
            "manifest_version": 3, "name": "Isolated analysis control zoom check",
            "version": "1.0", "permissions": ["tabs"],
            "background": {"service_worker": "background.js"}}), encoding="utf-8")
        (extension / "background.js").write_text(
            "chrome.runtime.onInstalled.addListener(()=>{});", encoding="utf-8")
        context = playwright.chromium.launch_persistent_context(
            str(temporary / "profile"), channel="chromium", headless=True,
            no_viewport=True, reduced_motion="reduce", args=[
                f"--disable-extensions-except={extension}",
                f"--load-extension={extension}", "--window-size=1600,1000"])
        page = context.pages[0]
        page.set_default_timeout(10000)
        page.on("pageerror", lambda error: report["pageErrors"].append(str(error)))

        def guard(route):
            if route.request.method not in {"GET", "HEAD", "OPTIONS"}:
                report["apiWrites"].append(route.request.url)
                route.abort()
            else:
                route.continue_()

        page.route("**/api/**", guard)
        page.add_init_script(f"if (!localStorage.getItem('{KEY}')) "
                             f"localStorage.setItem('{KEY}', {json.dumps(json.dumps(session))})")
        panel = page.get_by_role("region", name="Analysis controls", exact=True)
        options = panel.get_by_role("button", name="Options", exact=True)
        popup = page.get_by_role("region", name="Analysis options", exact=True)
        chips = page.get_by_role("list", name="Selected test points", exact=True)
        export = page.get_by_role("button", name="Export selected plots", exact=True)
        cdp = context.new_cdp_session(page)
        window_id = cdp.send("Browser.getWindowForTarget")["windowId"]

        def capture(name):
            data = cdp.send("Page.captureScreenshot", {"fromSurface": True})
            (out / f"{name}.png").write_bytes(base64.b64decode(data["data"]))

        def capture_panel(name):
            page.keyboard.press("Escape")
            page.mouse.move(0, 0)
            page.wait_for_timeout(180)
            panel.screenshot(path=str(out / f"{name}-panel.png"))

        def settled():
            page.wait_for_load_state("networkidle")
            expect(panel).to_be_visible()
            page.wait_for_timeout(180)

        def passed(name):
            report["checks"].append(name)
            print("PASS:", name, flush=True)

        def measure(name):
            data = panel.evaluate(MEASURE)
            assert data["panelOverflow"] <= 1, (name, data)
            assert data["pageOverflow"] <= 1, (name, data)
            assert not data["repeatedSourceSummary"], (name, data)
            assert not data["visibleTestLabel"], (name, data)
            assert data["tray"]["height"] <= 68, (name, "Selection tray should stay compact", data)
            if data["firstChip"]:
                assert data["firstChip"]["y"] >= data["chips"]["y"], (name, data)
                assert (data["firstChip"]["y"] + data["firstChip"]["height"] <=
                        data["chips"]["y"] + data["chipScroll"]["height"] + 1), (name, data)
            if data["export"]:
                assert data["export"]["y"] >= data["tray"]["y"], (name, data)
                assert (data["export"]["y"] + data["export"]["height"] <=
                        data["tray"]["y"] + data["tray"]["height"] + 1), (name, data)
            report["geometry"].append({"name": name, **data})
            return data

        def close_options():
            if popup.count():
                page.keyboard.press("Escape")
                expect(popup).to_have_count(0)

        def click_control(name):
            panel.get_by_role("button", name=name, exact=True).click()
            settled()

        def patch(**changes):
            close_options()
            page.wait_for_timeout(350)
            # Cleared sessions intentionally prune unused source identities.
            # Restore matching read-only catalog references when reseeding TPs.
            if "selections" in changes:
                changes["sources"] = sources
            page.evaluate("""([key, changes]) => {
              const state = JSON.parse(localStorage.getItem(key));
              Object.assign(state, changes);
              localStorage.setItem(key, JSON.stringify(state));
            }""", [KEY, changes])
            page.reload()
            settled()

        def options_geometry(name, baseline):
            expect(options).to_have_attribute("aria-expanded", "false")
            before = measure(name + "-closed")
            same_box(baseline["panel"], before["panel"], name + " panel")
            same_box(baseline["options"], before["options"], name + " options")
            if not options.is_enabled():
                expect(popup).to_have_count(0)
                return
            trigger_box = options.bounding_box()
            center = {"x": trigger_box["x"] + trigger_box["width"] / 2,
                      "y": trigger_box["y"] + trigger_box["height"] / 2}
            page.mouse.click(**center)
            expect(popup).to_be_visible()
            expect(options).to_have_attribute("aria-expanded", "true")
            opened = measure(name + "-open")
            same_box(before["panel"], opened["panel"], name + " open panel")
            same_box(before["options"], opened["options"], name + " open trigger")
            same_box(before["export"], opened["export"], name + " export")
            assert popup.evaluate("e => {const r=e.getBoundingClientRect(); return "
                                  "r.left>=0 && r.top>=0 && r.right<=innerWidth+1 && "
                                  "r.bottom<=innerHeight+1 && e.scrollWidth<=e.clientWidth+1;}")
            page.mouse.click(**center)
            expect(popup).to_have_count(0)
            same_box(before["panel"], measure(name + "-closed-again")["panel"],
                     name + " close panel")

        def mode_matrix(name):
            click_control("Selected points")
            click_control("Time")
            baseline = measure(name + "-baseline")
            for source in ["Selected points", "Full test"]:
                click_control(source)
                for view in ["Time", "XY", "Spectrum"]:
                    click_control(view)
                    methods = ["FFT", "PSD", "Waterfall"] if view == "Spectrum" else [None]
                    for method in methods:
                        if method:
                            click_control(method)
                        state_name = f"{name}-{source}-{view}-{method or 'default'}"
                        options_geometry(state_name, baseline)
                        expect(export).to_have_count(1)
                        assert export.evaluate("e => !!e.closest('[aria-label=\"Analysis controls\"]')")
            passed(name + ": equal panel/toggle bounds across both sources and all views/methods")

        def single_toolbar_row(name):
            controls = [panel.get_by_role("button", name=label, exact=True) for label in
                        ["Selected points", "Time", "Active test", "FFT", "Options", "9"]]
            bounds = [control.bounding_box() for control in controls]
            centers = [box["y"] + box["height"] / 2 for box in bounds]
            assert max(centers) - min(centers) <= 2, (name, bounds)
            for left, right in zip(bounds, bounds[1:]):
                assert left["x"] + left["width"] <= right["x"] + 1, (name, bounds)
            assert options.inner_text().strip() == "", "Options must remain icon-only"
            expect(options.locator("svg")).to_have_count(1)
            assert 28 <= options.bounding_box()["width"] <= 34
            assert 110 <= controls[2].bounding_box()["width"] <= 220
            methods = panel.get_by_role("group", name="Spectrum type", exact=True).bounding_box()
            one = panel.get_by_role("button", name="1", exact=True).bounding_box()
            tools_box = panel.locator('[aria-label="Plot tools"]').bounding_box()
            options_box = options.bounding_box()
            method_gap = options_box["x"] - methods["x"] - methods["width"]
            tools_gap = tools_box["x"] - options_box["x"] - options_box["width"]
            edge_gap = panel.bounding_box()["x"] + panel.bounding_box()["width"] - bounds[-1]["x"] - bounds[-1]["width"]
            assert 0 <= method_gap <= 12, (name, "Spectrum and Options gap", method_gap)
            assert 0 <= tools_gap <= 20, (name, "Options and plot tools gap", tools_gap)
            assert 0 <= edge_gap <= 16, (name, "Toolbar should align right", edge_gap)
            badge = panel.locator('[data-plot-resolution]')
            if badge.count():
                badge_box = badge.bounding_box()
                badge_gap = one["x"] - badge_box["x"] - badge_box["width"]
                assert 0 <= badge_gap <= 12, (name, "Display detail and layout gap", badge_gap)
                assert abs(badge_box["y"] + badge_box["height"] / 2 -
                           one["y"] - one["height"] / 2) <= 2
            report.setdefault("singleRowGeometry", []).append({"name": name, "controls": bounds,
                "methodOptionsGap": method_gap, "optionsToolsGap": tools_gap, "rightEdgeGap": edge_gap})
            measure(name + "-compact-tray")

        try:
            page.goto(args.url)
            settled()
            worker = context.service_workers[0] if context.service_workers else \
                context.wait_for_event("serviceworker")
            expect(chips.get_by_role("listitem")).to_have_count(len(selections))
            mode_matrix("wide")
            single_toolbar_row("1600-wide")
            capture("wide-waterfall")
            capture_panel("wide-waterfall")
            cdp.send("Browser.setWindowBounds", {"windowId": window_id,
                     "bounds": {"width": 1171, "height": 1000}})
            settled()
            single_toolbar_row("1115-control-pane")
            assert abs(panel.bounding_box()["width"] - 1115) <= 2
            capture_panel("1115-control-pane")
            cdp.send("Browser.setWindowBounds", {"windowId": window_id,
                     "bounds": {"width": 1100, "height": 1000}})
            settled()
            single_toolbar_row("1100-wide")
            capture_panel("compact-wide-waterfall")
            cdp.send("Browser.setWindowBounds", {"windowId": window_id,
                     "bounds": {"width": 1600, "height": 1000}})
            settled()
            passed("Source, view, compact test, spectrum type, icon-only Options and layout share the first row")

            active_test = panel.get_by_role("button", name="Active test", exact=True)
            long_test = max((source["name"] for source in sources if source["status"] == "ready"),
                            key=len)
            before_long_name = measure("compact-test-before-long-name")
            active_test.click()
            page.get_by_role("option", name=re.compile("^" + re.escape(long_test))).click()
            settled()
            same_box(before_long_name["panel"], measure("compact-test-long-name")["panel"],
                     "long test name must not resize the panel")
            assert active_test.evaluate("""e => {
              const value = e.querySelector('[class*="triggerValue"]');
              return value && value.scrollWidth > value.clientWidth &&
                getComputedStyle(value).textOverflow === 'ellipsis';
            }"""), "The existing long test name should truncate inside the compact selector"
            active_test.hover()
            expect(page.get_by_role("tooltip")).to_have_text(long_test)
            page.screenshot(path=str(out / "compact-test-full-name-tooltip.png"))
            page.keyboard.press("Escape")
            active_test.click()
            page.get_by_role("option", name=re.compile("^ptt_demo_run_a")).click()
            settled()
            passed("Long test names truncate in the compact selector and show their full name on hover")

            # The RPM picker is a nested React portal outside the options DOM.
            click_control("Spectrum")
            click_control("FFT")
            options.focus()
            page.keyboard.press("Enter")
            expect(popup).to_be_visible()
            popup.get_by_role("button", name="Per rev", exact=True).click()
            rpm = popup.get_by_role("button", name="RPM variable for per-revolution spectrum", exact=True)
            rpm.click()
            search = page.get_by_role("combobox", name="Search RPM variables...", exact=True)
            expect(search).to_be_focused()
            search.fill("rpm")
            page.get_by_role("option", name="rpm", exact=True).click()
            expect(popup).to_be_visible()
            expect(rpm).to_be_focused()
            expect(rpm).to_contain_text("rpm")
            rpm.click()
            expect(search).to_be_visible()
            page.keyboard.press("Escape")
            expect(search).to_have_count(0)
            expect(popup).to_be_visible()
            expect(rpm).to_be_focused()
            page.keyboard.press("Escape")
            expect(popup).to_have_count(0)
            expect(options).to_be_focused()
            passed("Options keyboard opening, nested RPM search/selection and two-stage Escape/focus return")

            page.keyboard.press("Enter")
            page.keyboard.press("Tab")
            hz = popup.get_by_role("button", name="Hz", exact=True)
            expect(hz).to_be_focused()
            page.keyboard.press("Shift+Tab")
            expect(options).to_be_focused()
            page.keyboard.press("Tab")
            expect(hz).to_be_focused()
            popup.get_by_role("button", name="Log scale", exact=True).focus()
            page.keyboard.press("Tab")
            expect(popup).to_have_count(0)
            expect(panel.get_by_role("button", name="1", exact=True)).to_be_focused()
            passed("Tab enters options, Shift+Tab returns to toggle, and last Tab continues to plot layout")

            options.click()
            popup.get_by_role("button", name="Hz", exact=True).click()
            options.click()
            click_control("Waterfall")
            before = measure("waterfall-closed")
            options.click()
            popup.get_by_label("Bin spacing").select_option("manual")
            expect(popup.get_by_label("Window (samples)")).to_be_visible()
            same_box(before["panel"], measure("manual-window")["panel"], "manual window panel")
            popup.get_by_label("Overlap").select_option("50")
            popup.get_by_role("button", name="Log color", exact=True).click()
            expect(popup).to_be_visible()
            capture("options-waterfall-manual")
            panel.get_by_role("button", name="4", exact=True).click()
            expect(popup).to_have_count(0)
            passed("Waterfall manual-window/settings changes keep panel fixed; outside click dismisses")

            # Check the existing test picker and full-test trace controls too.
            click_control("Full test")
            click_control("Time")
            before = measure("test-change-before")
            panel.get_by_role("button", name="Active test", exact=True).click()
            page.get_by_role("option", name=re.compile("^ptt_demo_run_b")).click()
            settled()
            same_box(before["panel"], measure("test-change-after")["panel"], "test switch")
            options.click()
            popup.get_by_role("button", name="Line", exact=True).click()
            expect(popup).to_be_visible()
            popup.get_by_role("button", name="Min/max", exact=True).click()
            options.click()
            passed("Active test search and full-test trace controls retain stable layout")

            # Export stays owned by the grid and focused after its dialog closes.
            expect(export).to_be_enabled()
            export.focus()
            page.keyboard.press("Enter")
            dialog = page.get_by_role("dialog", name="Export selected plots", exact=True)
            expect(dialog).to_be_visible()
            page.keyboard.press("Escape")
            expect(dialog).to_have_count(0)
            expect(export).to_be_focused()
            page.get_by_role("button", name="Expand rpm", exact=True).click()
            expect(export).to_be_disabled()
            page.get_by_role("button", name="Minimize rpm", exact=True).click()
            expect(export).to_be_enabled()
            passed("Export portal retains existing dialog/focus and maximize/restore behavior")

            cdp.send("Browser.setWindowBounds", {"windowId": window_id,
                     "bounds": {"width": 1100, "height": 1000}})
            settled()
            click_control("Selected points")
            click_control("Time")
            baseline = measure("eight-selections")
            def same_selection_geometry(name):
                current = measure(name)
                same_box(baseline["panel"], current["panel"], name + " panel")
                same_box(baseline["tray"], current["tray"], name + " tray")

            scroll = baseline["chipScroll"]
            assert scroll["scrollWidth"] > scroll["width"], scroll
            last = chips.get_by_role("listitem").last
            last.get_by_role("button").first.focus()
            assert chips.evaluate("e => e.scrollLeft > 0")
            same_selection_geometry("last-chip-focused")
            last.get_by_role("button", name=re.compile("^Remove ")).click()
            expect(chips.get_by_role("listitem")).to_have_count(len(selections) - 1)
            same_selection_geometry("removed-chip")
            panel.get_by_role("button", name="Clear selection", exact=True).click()
            settled()
            same_selection_geometry("empty-selection")
            expect(export).to_have_count(0)
            click_control("Full test")
            expect(export).to_be_enabled()
            same_selection_geometry("empty-full-test")
            patch(selections=selections[:1])
            same_selection_geometry("single-selection")
            patch(selections=selections, scatterCollapsed=False)
            passed("Eight/one/zero chips, horizontal keyboard scrolling and clear/remove keep panel height")

            for width in [1600, 1100, 960]:
                cdp.send("Browser.setWindowBounds", {"windowId": window_id,
                         "bounds": {"width": width, "height": 1000}})
                settled()
                mode_matrix(f"split-{width}")
                capture(f"split-{width}")
                capture_panel(f"split-{width}")

            cdp.send("Browser.setWindowBounds", {"windowId": window_id,
                     "bounds": {"width": 1600, "height": 1000}})
            page.get_by_role("separator", name="Resize scatter and plot panels", exact=True).focus()
            page.keyboard.press("End")
            settled()
            mode_matrix("minimum-plot-pane")
            capture("minimum-plot-pane")

            for factor in [1.25, 1.5]:
                worker.evaluate("""async ({url, factor}) => {
                  const tab = (await chrome.tabs.query({})).find(tab => tab.url === url);
                  await chrome.tabs.setZoom(tab.id, factor);
                }""", {"url": page.url, "factor": factor})
                settled()
                mode_matrix(f"zoom-{factor}")
                capture(f"zoom-{factor}")

            # Short desktop windows must keep every floating setting reachable.
            worker.evaluate("""async url => {
              const tab = (await chrome.tabs.query({})).find(tab => tab.url === url);
              await chrome.tabs.setZoom(tab.id, 1);
            }""", page.url)
            cdp.send("Browser.setWindowBounds", {"windowId": window_id,
                     "bounds": {"width": 960, "height": 400}})
            settled()
            click_control("Full test")
            click_control("Spectrum")
            click_control("Waterfall")
            short = measure("short-window-closed")
            options.click()
            expect(popup).to_be_visible()
            popup.get_by_label("Bin spacing").select_option("manual")
            page.wait_for_timeout(150)
            same_box(short["panel"], measure("short-window-open")["panel"],
                     "short window options preserve panel")
            assert popup.evaluate("e => {const r=e.getBoundingClientRect(); return "
                                  "r.left>=0 && r.top>=0 && r.right<=innerWidth+1 && "
                                  "r.bottom<=innerHeight+1 && e.scrollWidth<=e.clientWidth+1;}")
            for field in popup.locator("select, button:not(:disabled)").all():
                field.focus()
                expect(field).to_be_focused()
                assert field.evaluate("e => {const r=e.getBoundingClientRect(), "
                                      "p=e.closest('[aria-label=\"Analysis options\"]').getBoundingClientRect(); "
                                      "return r.top>=p.top && r.bottom<=p.bottom+1;}")
            popup.get_by_label("Overlap").select_option("75")
            popup.get_by_role("button", name="Log color", exact=True).click()
            expect(popup).to_be_visible()
            page.mouse.move(0, 0)
            capture("short-window-options")
            page.keyboard.press("Escape")
            expect(options).to_be_focused()
            chips.get_by_role("listitem").last.get_by_role("button").first.focus()
            short_chips = chips.evaluate("""e => {
              const rect = node => { const r=node.getBoundingClientRect();
                return {x:r.x, y:r.y, right:r.right, bottom:r.bottom, height:r.height}; };
              return {list:rect(e), item:rect(e.lastElementChild), clientHeight:e.clientHeight,
                scrollHeight:e.scrollHeight, scrollLeft:e.scrollLeft};
            }""")
            report["shortChipGeometry"] = short_chips
            assert short_chips["scrollLeft"] > 0, short_chips
            assert short_chips["item"]["y"] >= short_chips["list"]["y"], short_chips
            assert short_chips["item"]["bottom"] <= short_chips["list"]["bottom"], short_chips
            passed("960x400 desktop popup stays in viewport and scrolls every field into view")
            assert not report["pageErrors"], report["pageErrors"]
            assert not report["apiWrites"], report["apiWrites"]
        except Exception as error:
            report["failure"] = str(error)
            capture("failure")
            raise
        finally:
            (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            context.close()
    print(f"Evidence: {out}", flush=True)


if __name__ == "__main__":
    run()
