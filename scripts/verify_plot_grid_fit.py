"""Read-only desktop grid-fit regression checks against the built PTT preview.

Requires Python with Playwright and Chromium. Run with --baseline to collect
pre-fix geometry without failing on fit assertions. API writes are blocked, and
all session edits live only in an isolated browser context.
"""

import argparse
import json
import os
import re
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


KEY = "ptt.analysis-session.v1"
PLOTS = '[role="group"][aria-label$=" plot"]'
COLUMNS = ["rpm", "thrust_n", "torque_nm", "voltage_v", "current_a",
           "shaft_power_w", "electrical_power_w", "motor_temp_c", "vibration_x_g"]
SIZES = [(1600, 1000), (1366, 768), (1280, 720), (1100, 850), (960, 850)]

# Measure the actual transformed glyph bounds, including rotated axis titles.
TEXT_INSTRUMENTATION = r"""(() => {
  const proto = CanvasRenderingContext2D.prototype;
  for (const name of ['fillText', 'strokeText']) {
    const original = proto[name];
    proto[name] = function(text, x, y, ...rest) {
      const m = this.measureText(text), matrix = this.getTransform();
      const points = [
        [x - m.actualBoundingBoxLeft, y - m.actualBoundingBoxAscent],
        [x + m.actualBoundingBoxRight, y - m.actualBoundingBoxAscent],
        [x - m.actualBoundingBoxLeft, y + m.actualBoundingBoxDescent],
        [x + m.actualBoundingBoxRight, y + m.actualBoundingBoxDescent]
      ].map(([a, b]) => matrix.transformPoint({x: a, y: b}));
      const record = {text: String(text), left: Math.min(...points.map(p => p.x)),
        right: Math.max(...points.map(p => p.x)), top: Math.min(...points.map(p => p.y)),
        bottom: Math.max(...points.map(p => p.y))};
      (this.canvas.__gridFitText ??= []).push(record);
      return original.call(this, text, x, y, ...rest);
    };
  }
  const clear = proto.clearRect;
  proto.clearRect = function(x, y, width, height) {
    const m = this.getTransform(), a = m.transformPoint({x, y}),
      b = m.transformPoint({x: x + width, y: y + height});
    if (a.x <= 0 && a.y <= 0 && b.x >= this.canvas.width && b.y >= this.canvas.height)
      this.canvas.__gridFitText = [];
    return clear.call(this, x, y, width, height);
  };
  for (const name of ['width', 'height']) {
    const descriptor = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype, name);
    Object.defineProperty(HTMLCanvasElement.prototype, name, {...descriptor, set(value) {
      this.__gridFitText = [];
      descriptor.set.call(this, value);
    }});
  }
})();"""

MEASURE = r"""plots => {
  const rect = el => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {left:r.left, top:r.top, right:r.right, bottom:r.bottom, width:r.width, height:r.height};
  };
  const grid = plots[0].parentElement.parentElement, style = getComputedStyle(grid);
  return {
    scripts: Array.from(document.scripts, script => script.src).filter(Boolean),
    viewport: {width: innerWidth, height: innerHeight},
    page: {width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight},
    grid: {...rect(grid), clientWidth:grid.clientWidth, clientHeight:grid.clientHeight,
      scrollWidth:grid.scrollWidth, scrollHeight:grid.scrollHeight,
      columns:style.gridTemplateColumns, rows:style.gridTemplateRows,
      overflowX:style.overflowX, overflowY:style.overflowY},
    plots: plots.map(plot => {
      const chart = plot.querySelector('.uplot'), canvas = chart?.querySelector('canvas'),
        over = chart?.querySelector('.u-over');
      const texts = canvas?.__gridFitText ?? [];
      return {label:plot.getAttribute('aria-label'), box:rect(plot), chart:rect(chart), canvas:rect(canvas),
        header:rect(plot.querySelector('[data-plot-header]')), over:rect(over),
        scrollWidth:plot.scrollWidth, clientWidth:plot.clientWidth,
        scrollHeight:plot.scrollHeight, clientHeight:plot.clientHeight,
        texts, textOverflow:texts.filter(t => t.left < -2 || t.top < -2 ||
          t.right > canvas.width + 2 || t.bottom > canvas.height + 2)};
    })
  };
}"""


def issues_for(data, tracks):
    problems = []
    grid = data["grid"]
    if len(grid["columns"].split()) != tracks or len(grid["rows"].split()) != tracks:
        problems.append(f"Expected {tracks}x{tracks} tracks; got {grid['columns']} / {grid['rows']}")
    if grid["scrollWidth"] > grid["clientWidth"] + 1 or grid["scrollHeight"] > grid["clientHeight"] + 1:
        problems.append(f"Grid scroll size {grid['scrollWidth']}x{grid['scrollHeight']} exceeds "
                        f"{grid['clientWidth']}x{grid['clientHeight']}")
    if data["page"]["width"] > data["viewport"]["width"] + 1 or data["page"]["height"] > data["viewport"]["height"] + 1:
        problems.append("Document overflows viewport")
    if grid["bottom"] > data["viewport"]["height"] + 1:
        problems.append("Grid extends below viewport")
    for plot in data["plots"]:
        label, box, chart = plot["label"], plot["box"], plot["chart"]
        if box["bottom"] > grid["bottom"] + 1 or box["right"] > grid["right"] + 1:
            problems.append(f"{label}: slot outside grid")
        if not chart or not plot["over"]:
            problems.append(f"{label}: no rendered chart")
            continue
        if chart["bottom"] > box["bottom"] + 1 or chart["right"] > box["right"] + 1:
            problems.append(f"{label}: chart outside slot")
        if plot["scrollWidth"] > plot["clientWidth"] + 1 or plot["scrollHeight"] > plot["clientHeight"] + 1:
            problems.append(f"{label}: slot contents overflow")
        if plot["over"]["width"] < 20 or plot["over"]["height"] < 20:
            problems.append(f"{label}: data area is too small")
        if len(plot["texts"]) < 3:
            problems.append(f"{label}: axis labels/ticks were not drawn")
        drawn = {item["text"] for item in plot["texts"]}
        if label.endswith((" time plot", " full test plot")) and "Time (s)" not in drawn:
            problems.append(f"{label}: time-axis title is missing")
        if label.endswith(" spectrum plot") and ("Frequency (Hz)" not in drawn or
                not any(text.startswith(("Magnitude", "PSD")) for text in drawn)):
            problems.append(f"{label}: spectrum-axis title is missing")
        if label.endswith(" XY plot"):
            expected = label.removesuffix(" XY plot").split(" versus ")
            joined = "".join(item["text"] for item in plot["texts"])
            if any(title not in drawn and title not in joined for title in expected):
                problems.append(f"{label}: XY-axis title is missing")
        if plot["textOverflow"]:
            problems.append(f"{label}: clipped canvas text {plot['textOverflow']}")
        ticks = [item for item in plot["texts"] if re.match(r"^[+\-\N{MINUS SIGN}]?[\d.]", item["text"])]
        titles = [item for item in plot["texts"] if item["text"] and item not in ticks]
        for title in titles:
            for tick in ticks:
                overlap_x = min(title["right"], tick["right"]) - max(title["left"], tick["left"])
                overlap_y = min(title["bottom"], tick["bottom"]) - max(title["top"], tick["top"])
                if overlap_x > 1 and overlap_y > 1:
                    problems.append(f"{label}: title {title['text']!r} overlaps tick {tick['text']!r}")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8087/ptt/")
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    out = Path(os.environ.get("TEMP", "/tmp")) / "ptt-plot-grid-fit"
    out.mkdir(exist_ok=True)
    with urlopen(args.url + "api/analysis-sources", timeout=20) as response:
        sources = json.load(response)["sources"]
    assert any(source["name"] == "ptt_demo_run_a" for source in sources), "Demo fixture is unavailable"
    session = {"version": 1, "sources": sources, "currentTest": "ptt_demo_run_a",
               "xAxis": "rpm", "yAxis": "thrust_n", "axesUserSet": True,
               "selections": [{"test": "ptt_demo_run_a", "tpId": 3, "hidden": False}],
               "plotConfigs": COLUMNS, "plotsUserEdited": True, "plotDensity": "nine",
               "viewMode": "full", "fullPlotMode": "envelope", "scatterCollapsed": True,
               "xyXCols": ["rpm"] * 9, "xySource": "tp", "specSource": "tp", "specMode": "fft"}
    report = {"baseline": args.baseline, "checks": [], "pageErrors": [], "apiWrites": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda error: report["pageErrors"].append(error.stack or str(error)))

        def guard(route):
            if route.request.method not in {"GET", "HEAD", "OPTIONS"}:
                report["apiWrites"].append(route.request.url)
                route.abort()
            else:
                route.continue_()

        page.route("**/api/**", guard)
        page.add_init_script(TEXT_INSTRUMENTATION)
        page.add_init_script(f"if (!localStorage.getItem('{KEY}')) "
                             f"localStorage.setItem('{KEY}', {json.dumps(json.dumps(session))})")
        page.goto(args.url)

        def settled(count):
            page.wait_for_load_state("networkidle")
            if page.locator(PLOTS).count() == 0:
                page.screenshot(path=str(out / "load-state.png"))
                print("Load state:", page.locator("body").inner_text()[:1600], report["pageErrors"], flush=True)
            expect(page.locator(PLOTS)).to_have_count(count)
            expect(page.locator(PLOTS).locator(".uplot")).to_have_count(count, timeout=20000)
            page.wait_for_timeout(450)

        def patch(**changes):
            page.wait_for_timeout(350)
            page.evaluate("""([key, changes]) => {
              const session = JSON.parse(localStorage.getItem(key));
              Object.assign(session, changes);
              localStorage.setItem(key, JSON.stringify(session));
            }""", [KEY, changes])
            page.reload()

        def check(name, tracks, screenshot=False):
            settled(tracks * tracks)
            data = page.locator(PLOTS).evaluate_all(MEASURE)
            problems = issues_for(data, tracks)
            report["checks"].append({"name": name, "issues": problems, **data})
            if screenshot or problems:
                page.screenshot(path=str(out / f"{'baseline-' if args.baseline else ''}{name}.png"))
            print(f"{'FAIL' if problems else 'PASS'}: {name}: " +
                  ("; ".join(problems[:3]) if problems else
                   f"{tracks}x{tracks}, grid {data['grid']['width']:.0f}x{data['grid']['height']:.0f}"), flush=True)

        settled(9)
        if args.baseline:
            for mode in ["full", "tp"]:
                patch(viewMode=mode, scatterCollapsed=False)
                for width, height in [(1366, 768), (1280, 720), (960, 850)]:
                    page.set_viewport_size({"width": width, "height": height})
                    check(f"{mode}-nine-{width}x{height}", 3, True)
        else:
            for mode in ["tp", "full"]:
                for density, tracks in [("single", 1), ("quad", 2), ("nine", 3)]:
                    patch(viewMode=mode, plotDensity=density, scatterCollapsed=True)
                    for width, height in SIZES:
                        page.set_viewport_size({"width": width, "height": height})
                        check(f"{mode}-{density}-{width}x{height}", tracks,
                              screenshot=density == "nine" and width in {1600, 1280, 960})
            for mode in ["spectrum", "xy"]:
                patch(viewMode=mode, plotDensity="nine")
                for width, height in [(1600, 1000), (1280, 720), (960, 850)]:
                    page.set_viewport_size({"width": width, "height": height})
                    check(f"{mode}-nine-{width}x{height}", 3, True)
            for mode in ["tp", "full"]:
                patch(viewMode=mode, plotDensity="nine", scatterCollapsed=False)
                for width, height in [(1600, 1000), (1366, 768), (1280, 720), (960, 850)]:
                    page.set_viewport_size({"width": width, "height": height})
                    check(f"{mode}-nine-scatter-open-{width}x{height}", 3, True)
                page.locator(PLOTS).first.get_by_role("button", name=re.compile("^Expand ")).click()
                check(f"{mode}-maximized", 1, True)
                page.set_viewport_size({"width": 1100, "height": 850})
                check(f"{mode}-maximized-resized", 1)
                page.locator(PLOTS).first.get_by_role("button", name=re.compile("^Minimize ")).click()
                check(f"{mode}-restored-resized", 3, True)
            patch(viewMode="spectrum", specMode="welch", specLogY=True, scatterCollapsed=True)
            page.set_viewport_size({"width": 1280, "height": 720})
            check("spectrum-welch-log-nine-1280x720", 3, True)
            patch(viewMode="xy", xyXCols=["electrical_power_w"] * 9, scatterCollapsed=False)
            for width, height in [(1100, 850), (1280, 720), (960, 850)]:
                page.set_viewport_size({"width": width, "height": height})
                check(f"xy-long-x-scatter-open-{width}x{height}", 3, True)
            patch(viewMode="tp", scatterCollapsed=False)
            page.set_viewport_size({"width": 960, "height": 850})
            settled(9)
            divider = page.get_by_role("separator", name="Resize scatter and plot panels")
            ratio = int(divider.get_attribute("aria-valuenow"))
            divider.press("ArrowLeft")
            expect(divider).to_have_attribute("aria-valuenow", str(ratio - 2))
            check("divider-narrower", 3)
            divider.press("ArrowRight")
            expect(divider).to_have_attribute("aria-valuenow", str(ratio))
            check("divider-restored", 3)
            page.get_by_role("button", name="Hide scatter panel", exact=True).click()
            check("scatter-collapsed", 3)
            page.get_by_role("button", name="Show scatter panel", exact=True).click()
            check("scatter-restored", 3)
            grid = page.locator(PLOTS).first.locator("..").locator("..")
            first = page.locator(PLOTS).first.bounding_box()
            page.mouse.move(first["x"] + first["width"] - 8, first["y"] + 8)
            page.mouse.wheel(0, 700)
            page.wait_for_timeout(300)
            assert grid.evaluate("el => el.scrollTop === 0 && el.scrollLeft === 0"), "Wheel scrolls plot grid"
            check("wheel-does-not-scroll-grid", 3)
            over = page.locator(PLOTS).first.locator(".u-over")
            area = over.bounding_box()

            def colored_pixels():
                return page.locator(PLOTS).first.locator("canvas").first.evaluate("""canvas => {
                  const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
                  let colored = 0;
                  for (let i = 0; i < pixels.length; i += 4) {
                    if (pixels[i + 3] > 40 && Math.max(...pixels.slice(i, i + 3)) -
                        Math.min(...pixels.slice(i, i + 3)) > 40) colored++;
                  }
                  return colored;
                }""")

            initial_ticks = page.locator(PLOTS).first.locator("canvas").first.evaluate(
                "canvas => (canvas.__gridFitText ?? []).map(item => item.text)")
            page.mouse.move(area["x"] + area["width"] * .15, area["y"] + area["height"] * .5)
            page.mouse.down()
            page.mouse.move(area["x"] + area["width"] * .75, area["y"] + area["height"] * .5, steps=10)
            page.mouse.up()
            check("tp-drag-zoom", 3, True)
            zoomed_ticks = page.locator(PLOTS).first.locator("canvas").first.evaluate(
                "canvas => (canvas.__gridFitText ?? []).map(item => item.text)")
            assert initial_ticks != zoomed_ticks, "Drag did not change plotted axes"
            assert colored_pixels() > 30, "Drag zoom blanked the plotted trace"
            over.hover()
            page.mouse.wheel(0, -180)
            check("tp-wheel-zoom", 3, True)
            assert colored_pixels() > 30, "Wheel zoom blanked the plotted trace"
            over.dblclick()
            check("tp-zoom-reset", 3, True)
            assert colored_pixels() > 30, "Reset blanked the plotted trace"
        browser.close()
    report_path = out / ("baseline-report.json" if args.baseline else "report.json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Evidence:", out, flush=True)
    assert not report["pageErrors"], report["pageErrors"]
    assert not report["apiWrites"], report["apiWrites"]
    if not args.baseline:
        failures = [check["name"] for check in report["checks"] if check["issues"]]
        assert not failures, failures


if __name__ == "__main__":
    main()
