"""Read-only dark-mode integration checks using existing demos and isolated Chromium.

Requires Python/Playwright and the built preview. No user profile or API mutations
are used. Actual browser zoom uses an extension in a temporary browser profile.
Screenshots and a JSON report are saved under %TEMP%/ptt-dark-mode.
"""
import argparse
import json
import os
import re
import tempfile
import traceback
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

from verify_browser_zoom import capture_browser_view, set_browser_zoom

SESSION_KEY = "ptt.analysis-session.v1"
THEME_KEY = "ptt.theme.v1"

CONTRAST = """root => {
  const rgb = value => {
    const numbers = value.match(/[\\d.]+/g)?.map(Number) ?? [0,0,0,0];
    return value.startsWith('color(srgb ') ? numbers.map((v,i)=>i<3?v*255:v) : numbers;
  };
  const blend = (front, back) => {
    const alpha = front[3] ?? 1;
    return front.slice(0,3).map((c,i) => c * alpha + back[i] * (1-alpha));
  };
  const background = element => {
    const parent = element.parentElement ? background(element.parentElement) : [255,255,255];
    return blend(rgb(getComputedStyle(element).backgroundColor), parent);
  };
  const luminance = color => color.map(c => c/255).map(c => c<=.04045 ? c/12.92 : ((c+.055)/1.055)**2.4)
    .reduce((sum,c,i) => sum+c*[.2126,.7152,.0722][i],0);
  const result = [];
  for (const element of root.querySelectorAll('h1,h2,h3,p,summary,label,button,input,select,th,td,[role="option"]')) {
    if (!(element instanceof HTMLElement) || element.matches(':disabled,[aria-disabled="true"]')) continue;
    const r=element.getBoundingClientRect(), style=getComputedStyle(element);
    if (r.width<2 || r.height<2 || r.bottom<0 || r.top>innerHeight || style.visibility==='hidden') continue;
    if (!element.innerText?.trim() && !element.matches('input,select')) continue;
    let transparent = false;
    for(let p=element; p; p=p.parentElement) if(Number(getComputedStyle(p).opacity)<.95) transparent=true;
    if (transparent) continue;
    const bg=background(element), fg=blend(rgb(style.color),bg);
    const a=luminance(bg), b=luminance(fg), ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    result.push({text:(element.getAttribute('aria-label')||element.innerText||element.tagName).trim().slice(0,80),
      color:style.color, background:bg, ratio, fontSize:style.fontSize});
    if (element.matches('input[placeholder],textarea[placeholder]') && !element.value) {
      const placeholder=getComputedStyle(element,'::placeholder');
      const foreground=blend(rgb(placeholder.color),bg), p=luminance(foreground);
      result.push({text:'Placeholder: '+element.getAttribute('placeholder'),color:placeholder.color,
        background:bg,ratio:(Math.max(a,p)+.05)/(Math.min(a,p)+.05),fontSize:style.fontSize});
    }
  }
  return result;
}"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8087/ptt/")
    parser.add_argument("--tooltip-only", action="store_true", help="Recheck switch tooltip and refresh zoom evidence only")
    args = parser.parse_args()
    args.url = args.url.rstrip("/") + "/"
    output = Path(os.environ.get("TEMP", "/tmp")) / "ptt-dark-mode"
    output.mkdir(exist_ok=True)
    report_path = output / ("tooltip-report.json" if args.tooltip_only else "report.json")
    with urlopen(args.url + "api/analysis-sources", timeout=30) as response:
        sources = json.load(response)["sources"]
    demos = [source for source in sources if source["name"] in {"ptt_demo_run_a", "ptt_demo_run_b"}]
    assert len(demos) == 2, "Existing ptt_demo_run_a/b fixtures are required"
    selections = [{"test": source["name"], "tpId": point["id"], "hidden": False}
                  for source in demos for point in source["test_points"][:1]]
    session = dict(version=1, sources=sources, currentTest="ptt_demo_run_a", xAxis="rpm", yAxis="thrust_n",
                   axesUserSet=True, selections=selections, plotDensity="quad", viewMode="tp",
                   plotConfigs=["rpm", "thrust_n", "torque_nm", "voltage_v"], plotsUserEdited=True,
                   scatterCollapsed=False, xyXCols=["rpm"]*4, xyYCols=["thrust_n"]*4,
                   xySource="tp", specSource="tp", specMode="fft", timeZoom=[.2, 1.2])
    report = {"checks": [], "contrast": {}, "geometry": [], "apiWrites": [], "pageErrors": []}

    def passed(name):
        report["checks"].append(name)
        print("PASS:", name, flush=True)

    def guard(route):
        if route.request.method not in {"GET", "HEAD", "OPTIONS"}:
            report["apiWrites"].append({"url": route.request.url, "method": route.request.method})
            route.abort()
        else:
            route.continue_()

    def configure(context, blocked=False):
        context.route("**/api/**", guard)
        context.add_init_script(f"""try {{
          if (!localStorage.getItem('{SESSION_KEY}')) localStorage.setItem('{SESSION_KEY}', {json.dumps(json.dumps(session))});
        }} catch {{}}""")
        if blocked:
            context.add_init_script("Object.defineProperty(window, 'localStorage', {get() {throw new DOMException('Storage unavailable','SecurityError')}});")
        context.on("page", lambda page: page.on("pageerror", lambda error: report["pageErrors"].append(str(error))))

    def ready(page):
        expect(page.get_by_role("switch", name="Dark mode", exact=True)).to_be_visible()
        page.wait_for_load_state("networkidle")

    def theme(page, value):
        expect(page.locator("html")).to_have_attribute("data-theme", value)
        expect(page.get_by_role("switch", name="Dark mode", exact=True)).to_have_attribute("aria-checked", str(value == "dark").lower())
        assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == value

    def readable(page, label, selector="body"):
        results = page.locator(selector).evaluate(CONTRAST)
        assert results, (label, "No readable elements sampled")
        report["contrast"][label] = results
        low = [result for result in results if result["ratio"] < 4.5]
        assert not low, (label, "Enabled UI text contrast below 4.5:1", low)

    def geometry(page, label):
        result = page.locator(".app-header").evaluate("""header => {
          const all=[...header.querySelectorAll('button, .app-brand, nav')].filter(e=>e.getClientRects().length);
          const rect=e=>{const r=e.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}};
          const controls=all.filter(e=>e.tagName==='BUTTON').map(e=>({name:e.getAttribute('aria-label')||e.innerText,...rect(e)}));
          const overlaps=controls.flatMap((a,i)=>controls.slice(i+1).filter(b=>Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x)>1&&Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y)>1).map(b=>[a.name,b.name]));
          const toggle=header.querySelector('[role="switch"]');
          return {width:innerWidth,dpr:devicePixelRatio,header:rect(header),toggle:rect(toggle),controls,overlaps,overflow:document.documentElement.scrollWidth-innerWidth};
        }""")
        report["geometry"].append({"label": label, **result})
        assert not result["overlaps"] and result["overflow"] <= 1, (label, result)
        assert result["toggle"]["x"] >= 0 and result["toggle"]["x"] + result["toggle"]["w"] <= result["width"] + 1, result

    with tempfile.TemporaryDirectory(prefix="ptt-dark-mode-") as temporary, sync_playwright() as playwright:
        temporary = Path(temporary)
        extension = temporary / "extension"
        extension.mkdir()
        (extension / "manifest.json").write_text(json.dumps({"manifest_version": 3, "name": "PTT isolated theme zoom",
            "version": "1.0", "permissions": ["tabs"], "background": {"service_worker": "background.js"}}), encoding="utf-8")
        (extension / "background.js").write_text("chrome.runtime.onInstalled.addListener(()=>{});", encoding="utf-8")
        context = playwright.chromium.launch_persistent_context(str(temporary / "profile"), channel="chromium", headless=True,
            no_viewport=True, color_scheme="light", reduced_motion="reduce", args=[f"--disable-extensions-except={extension}",
            f"--load-extension={extension}", "--window-size=1600,1000"])
        configure(context)
        page = context.pages[0]
        page.on("pageerror", lambda error: report["pageErrors"].append(str(error)))
        page.set_default_timeout(15000)
        cdp = context.new_cdp_session(page)
        window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
        try:
            page.goto(args.url)
            ready(page)
            theme(page, "light")
            expect(page.get_by_role("list", name="Selected test points").locator("li")).to_have_count(2)
            page.wait_for_timeout(500)
            before = page.evaluate("key=>JSON.parse(localStorage.getItem(key))", SESSION_KEY)
            assert before["timeZoom"] == [.2, 1.2], before.get("timeZoom")
            switch = page.get_by_role("switch", name="Dark mode", exact=True)
            switch.focus()
            switch.press("Space")
            theme(page, "dark")
            expect(switch).to_be_focused()
            expect(page.get_by_role("tooltip")).to_have_count(0)
            page.wait_for_timeout(500)
            after = page.evaluate("key=>JSON.parse(localStorage.getItem(key))", SESSION_KEY)
            assert before == after, "Theme toggle changed analysis session state"
            passed("Keyboard switch preserves selections, plot configuration and saved time zoom")
            passed("Theme tooltip dismisses when the focused switch is activated")
            readable(page, "Analyze")
            capture_browser_view(cdp, output / "analyze-dark.png")

            if args.tooltip_only:
                for gesture in ["click", "Space", "Enter"]:
                    switch.hover()
                    expect(page.get_by_role("tooltip")).to_have_text("Toggle dark mode")
                    previous = switch.get_attribute("aria-checked")
                    switch.click() if gesture == "click" else switch.press(gesture)
                    theme(page, "light" if previous == "true" else "dark")
                    expect(page.get_by_role("tooltip")).to_have_count(0)
                    switch.press("Escape")
                    expect(page.get_by_role("tooltip")).to_have_count(0)
                    switch.evaluate("element=>element.blur()")
                    page.mouse.move(0, 0)
                    switch.focus()
                    switch.press("Shift+Tab")
                    page.keyboard.press("Tab")
                    expect(page.get_by_role("tooltip")).to_have_text("Toggle dark mode")
                    passed(f"Theme tooltip dismisses after {gesture} and returns on keyboard focus")
                if switch.get_attribute("aria-checked") == "false":
                    switch.click()
                theme(page, "dark")
                chrome_width = page.evaluate("outerWidth-innerWidth")
                cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"width": 1600 + chrome_width, "height": 1000}})
                set_browser_zoom(worker, page, 1.5)
                page.wait_for_timeout(500)
                switch.hover()
                expect(page.get_by_role("tooltip")).to_have_text("Toggle dark mode")
                geometry(page, "corrected-tooltip-at-150-percent")
                capture_browser_view(cdp, output / "zoom-150-dark.png")
                assert not report["apiWrites"] and not report["pageErrors"], report
                passed("Corrected theme tooltip stays accurate at actual 150% browser zoom")
                return

            page.reload()
            ready(page)
            theme(page, "dark")
            page.emulate_media(color_scheme="light")
            theme(page, "dark")
            assert page.evaluate("key=>localStorage.getItem(key)", THEME_KEY) == "dark"
            passed("Explicit choice persists across reload and overrides OS color scheme")

            peer = context.new_page()
            peer.goto(args.url)
            ready(peer)
            theme(peer, "dark")
            peer.get_by_role("switch", name="Dark mode", exact=True).click()
            theme(peer, "light")
            theme(page, "light")
            page.get_by_role("switch", name="Dark mode", exact=True).click()
            theme(peer, "dark")
            peer.close()
            passed("Theme preference synchronizes between browser tabs")

            nav = page.get_by_role("navigation", name="Main navigation")
            for section in ["Split", "Edit", "Uploads", "Components", "Settings"]:
                nav.get_by_role("button", name=section, exact=True).click()
                page.wait_for_load_state("networkidle")
                theme(page, "dark")
                readable(page, section)
                geometry(page, section)
                capture_browser_view(cdp, output / f"{section.lower()}-dark.png")
            passed("All six sections use readable dark surfaces and controls")

            page.locator('summary[aria-label="More settings actions"]').click()
            page.get_by_role("button", name="Make default for everyone", exact=True).click()
            confirm = page.get_by_role("alertdialog")
            expect(confirm).to_be_visible()
            readable(page, "Confirmation dialog", '[role="alertdialog"]')
            capture_browser_view(cdp, output / "confirmation-dark.png")
            confirm.get_by_role("button", name="Cancel", exact=True).click()
            expect(confirm).to_have_count(0)
            passed("Confirmation dialog is readable; cancelling sends no write")

            nav.get_by_role("button", name="Analyze", exact=True).click()
            ready(page)
            panel = page.get_by_role("region", name="Analysis controls", exact=True)
            panel.get_by_role("button", name="Full test", exact=True).click()
            panel.get_by_role("button", name="Active test", exact=True).click()
            expect(page.get_by_role("listbox")).to_be_visible()
            readable(page, "Test picker", '[role="listbox"]')
            capture_browser_view(cdp, output / "picker-dark.png")
            page.keyboard.press("Escape")
            panel.get_by_role("button", name="Spectrum", exact=True).click()
            panel.get_by_role("button", name="Options", exact=True).click()
            expect(page.get_by_role("region", name="Analysis options", exact=True)).to_be_visible()
            readable(page, "Analysis options", '[aria-label="Analysis options"]')
            capture_browser_view(cdp, output / "options-dark.png")
            page.keyboard.press("Escape")
            panel.get_by_role("button", name="Selected points", exact=True).click()
            panel.get_by_role("button", name="Time", exact=True).click()
            page.wait_for_load_state("networkidle")
            page.locator(".u-over").first.click(button="right", position={"x": 50, "y": 30})
            page.get_by_role("menuitem", name="Filter settings…", exact=True).click()
            expect(page.get_by_role("dialog", name=re.compile("^Filter settings for"))).to_be_visible()
            readable(page, "Plot filter dialog", 'dialog[open]')
            capture_browser_view(cdp, output / "plot-filter-dark.png")
            page.keyboard.press("Escape")
            panel.get_by_role("button", name="Export selected plots", exact=True).click()
            expect(page.get_by_role("dialog", name="Export selected plots", exact=True)).to_be_visible()
            readable(page, "Export dialog", 'dialog[open]')
            capture_browser_view(cdp, output / "export-dialog-dark.png")
            page.keyboard.press("Escape")
            expect(page.get_by_role("button", name="Sessions", exact=True)).to_have_count(0)
            passed("Portaled pickers, Options, filters and export dialogs follow dark mode")

            for width in [960, 1100, 1241, 1300, 1301, 1600]:
                chrome_width = page.evaluate("outerWidth-innerWidth")
                cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"width": width + chrome_width, "height": 1000}})
                page.wait_for_function("width=>innerWidth===width", arg=width)
                page.wait_for_timeout(350)
                geometry(page, f"window-{width}")
                capture_browser_view(cdp, output / f"header-{width}-dark.png")
            set_browser_zoom(worker, page, 1.5)
            page.wait_for_timeout(500)
            geometry(page, "actual-browser-zoom-150-percent")
            capture_browser_view(cdp, output / "zoom-150-dark.png")
            assert page.evaluate("devicePixelRatio") >= 1.5
            passed("Header fits 960/1100/1241/1300/1301px viewports and actual 150% browser zoom without overlap")
        except Exception:
            report["failure"] = traceback.format_exc()
            capture_browser_view(cdp, output / "failure.png")
            raise
        finally:
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            context.close()

        browser = playwright.chromium.launch(channel="chromium", headless=True)
        try:
            bootstrap = browser.new_context(color_scheme="dark", viewport={"width": 1440, "height": 1000})
            # Prevent React and the API from helping: the small public script must
            # apply the correct page palette on its own before app startup.
            bootstrap.route("**/assets/index-*.js", lambda route: route.abort())
            bootstrap.route("**/api/**", lambda route: route.abort())
            p = bootstrap.new_page()
            p.goto(args.url, wait_until="domcontentloaded")
            expect(p.locator("html")).to_have_attribute("data-theme", "dark")
            assert p.evaluate("getComputedStyle(document.documentElement).backgroundColor") == "rgb(16, 24, 36)"
            assert p.evaluate("getComputedStyle(document.documentElement).colorScheme") == "dark"
            assert p.locator("#root").inner_html() == ""
            p.screenshot(path=str(output / "before-app-dark.png"))
            bootstrap.close()
            passed("Dark palette applies before app startup with app bundle and APIs blocked")

            fresh = browser.new_context(color_scheme="dark", viewport={"width": 1440, "height": 1000})
            configure(fresh)
            p = fresh.new_page()
            p.goto(args.url)
            ready(p)
            theme(p, "dark")
            assert p.evaluate("key=>localStorage.getItem(key)", THEME_KEY) is None
            p.emulate_media(color_scheme="light")
            theme(p, "light")
            p.emulate_media(color_scheme="dark")
            theme(p, "dark")
            p.get_by_role("switch", name="Dark mode", exact=True).click()
            theme(p, "light")
            p.reload()
            ready(p)
            theme(p, "light")
            fresh.close()
            passed("Fresh profile follows OS changes until explicit light choice overrides dark OS")

            blocked = browser.new_context(color_scheme="light", viewport={"width": 1440, "height": 1000})
            configure(blocked, blocked=True)
            p = blocked.new_page()
            p.goto(args.url)
            ready(p)
            theme(p, "light")
            p.get_by_role("switch", name="Dark mode", exact=True).click()
            theme(p, "dark")
            p.emulate_media(color_scheme="dark")
            p.emulate_media(color_scheme="light")
            theme(p, "dark")
            p.get_by_role("switch", name="Dark mode", exact=True).press("Space")
            theme(p, "light")
            blocked.close()
            passed("Theme toggle remains usable when localStorage access throws")
            assert not report["apiWrites"], report["apiWrites"]
            assert not report["pageErrors"], report["pageErrors"]
            passed("No application errors or API writes")
        except Exception:
            report["failure"] = traceback.format_exc()
            raise
        finally:
            browser.close()
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"{len(report['checks'])} dark-mode groups passed. Evidence: {output}", flush=True)


if __name__ == "__main__":
    main()
