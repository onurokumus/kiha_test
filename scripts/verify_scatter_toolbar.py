"""Read-only compact scatter toolbar, More actions and focus-modality checks.

All API responses use the existing isolated scatter export fixture. Evidence is
saved under %TEMP%/ptt-scatter-toolbar. Browser zoom uses a temporary extension.
"""
import argparse
import json
import os
import re
import tempfile
import traceback
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from verify_browser_zoom import capture_browser_view, set_browser_zoom
from verify_scatter_exports import Fixture, VALID, check_csv, close, download, open_dialog, png, settled

ROW = """panel => {
  const filter=[...panel.querySelectorAll('button')].find(e=>e.textContent.trim().startsWith('Filters'));
  const navigation=panel.querySelector('[aria-label="Scatter navigation"]');
  const count=panel.querySelector('[aria-live="polite"][aria-atomic="true"]');
  const clear=panel.querySelector('[aria-label="Clear all scatter filters"]');
  let row=filter; while(row&&!row.contains(navigation)) row=row.parentElement;
  const rect=e=>{if(!e)return null;const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};};
  const items=[filter,count,...navigation.querySelectorAll('button'),...(clear?[clear]:[])].map(e=>({
    name:e.getAttribute('aria-label')||e.textContent.trim(),...rect(e)}));
  const overlaps=items.flatMap((a,i)=>items.slice(i+1).filter(b=>Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x)>1&&Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y)>1).map(b=>[a.name,b.name]));
  return {width:innerWidth,dpr:devicePixelRatio,row:rect(row),filter:rect(filter),count:rect(count),navigation:rect(navigation),clear:rect(clear),items,overlaps,
    overflow:document.documentElement.scrollWidth-innerWidth, panelOverflow:panel.scrollWidth-panel.clientWidth};
}"""


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url",default="http://127.0.0.1:8087/ptt/")
    args=parser.parse_args()
    output=Path(os.environ.get("TEMP",tempfile.gettempdir()))/"ptt-scatter-toolbar"
    output.mkdir(exist_ok=True)
    report={"checks":[],"geometry":[],"downloads":[],"pageErrors":[]}
    with tempfile.TemporaryDirectory(prefix="scatter-toolbar-") as temporary,sync_playwright() as pw:
        directory=Path(temporary)
        extension=directory/"extension"
        extension.mkdir()
        (extension/"manifest.json").write_text(json.dumps({"manifest_version":3,"name":"Scatter toolbar zoom QA","version":"1.0",
            "permissions":["tabs"],"background":{"service_worker":"background.js"}}),encoding="utf-8")
        (extension/"background.js").write_text("chrome.runtime.onInstalled.addListener(()=>{});",encoding="utf-8")
        context=pw.chromium.launch_persistent_context(str(directory/"profile"),channel="chromium",headless=True,
            no_viewport=True,color_scheme="light",reduced_motion="reduce",args=[f"--disable-extensions-except={extension}",
            f"--load-extension={extension}","--window-size=1600,1000"])
        page=context.pages[0]
        page.set_default_timeout(12000)
        fixture=Fixture()
        fixture.configure(page)
        page.on("pageerror",lambda error:report["pageErrors"].append(str(error)))
        cdp=context.new_cdp_session(page)
        window_id=cdp.send("Browser.getWindowForTarget")["windowId"]
        worker=context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
        panel=page.locator(".analyze-scatter-panel")
        more=page.get_by_role("button",name="More",exact=True)
        canvas=page.get_by_label("Plot canvas for test-point overview",exact=True)
        navigation=page.get_by_role("group",name="Scatter navigation",exact=True)
        filter_button=panel.get_by_role("button",name=re.compile("^Filters"))
        clear=page.get_by_role("button",name="Clear all scatter filters",exact=True)

        def frames():
            page.evaluate("()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")

        def passed(name):
            report["checks"].append(name)
            print("PASS:",name,flush=True)

        def snapshot(name):
            page.keyboard.press("Escape")
            page.mouse.move(0,0)
            frames()
            capture_browser_view(cdp,output/f"{name}.png")

        def row(name):
            frames()
            data=panel.evaluate(ROW)
            report["geometry"].append({"name":name,**data})
            assert data["overflow"]<=1 and data["panelOverflow"]<=1 and not data["overlaps"],(name,data)
            centers=[item["y"]+item["h"]/2 for item in data["items"]]
            assert max(centers)-min(centers)<=1.5,(name,"Controls must share one baseline",data)
            assert max(item["y"]+item["h"] for item in data["items"])-min(item["y"] for item in data["items"])<=34,(name,data)
            expect(page.get_by_role("button",name="Export scatter plot",exact=True)).to_have_count(0)
            expect(panel.get_by_role("button",name="Plot actions for Test-point overview",exact=True)).to_have_count(0)
            return data

        def filter_on():
            filter_button.click()
            drawer=page.get_by_role("region",name="Scatter filters",exact=True)
            labels=drawer.get_by_role("button",name="Labels",exact=True)
            if labels.get_attribute("aria-expanded")!="true":labels.click()
            drawer.get_by_role("checkbox",name="other",exact=True).check()
            drawer.get_by_role("button",name="Close filters",exact=True).click()
            expect(clear).to_be_visible()
            expect(panel.locator('[aria-live="polite"][aria-atomic="true"]')).to_have_text("1 / 4 points")

        def row_pair(name):
            baseline=row(name+"-clear")
            filter_on()
            active=row(name+"-active")
            assert abs(active["row"]["h"]-baseline["row"]["h"])<=1,(name,baseline,active)
            snapshot(name+"-active")
            clear.click()
            expect(clear).to_have_count(0)
            row(name+"-cleared")
            passed(name+" Filters/count/navigation share one row with active filters and Clear")

        def focus_checks(name):
            reset=navigation.get_by_role("button",name="Reset zoom",exact=True)
            reset.focus()
            for _ in range(12):
                page.keyboard.press("Tab")
                if canvas.evaluate("e=>e===document.activeElement"):break
            expect(canvas).to_be_focused()
            keyboard=canvas.evaluate("e=>({style:getComputedStyle(e).outlineStyle,width:parseFloat(getComputedStyle(e).outlineWidth),visible:e.matches(':focus-visible')})")
            assert keyboard["style"]!="none" and keyboard["width"]>=1,(name,"Keyboard focus is not visible",keyboard)
            bounds=canvas.bounding_box()
            x,y=bounds["x"]+bounds["width"]*.55,bounds["y"]+bounds["height"]*.4
            page.mouse.move(x,y)
            page.mouse.down()
            page.mouse.move(x+28,y+17,steps=5)
            page.mouse.up()
            frames()
            pointer=canvas.evaluate("e=>({style:getComputedStyle(e).outlineStyle,width:parseFloat(getComputedStyle(e).outlineWidth)})")
            assert pointer["style"]=="none" or pointer["width"]==0,(name,"Pointer drag left an outline",pointer)
            reset.click()
            passed(name+" keyboard chart focus remains visible; pointer drag leaves no outline")

        try:
            page.goto(args.url)
            settled(page)
            for theme in ["light","dark"]:
                if page.locator("html").get_attribute("data-theme")!=theme:
                    page.get_by_role("switch",name="Dark mode",exact=True).click()
                row_pair(theme+"-desktop")
                focus_checks(theme)
                dialog=open_dialog(page)
                report["downloads"].append(check_csv(download(page,dialog,"CSV",output,theme+"-from-more"),VALID))
                report["downloads"].append(png(page,dialog,output,theme+"-from-more"))
                close(page,dialog)
                expect(more).to_be_focused()
                passed(theme+" More exports CSV/PNG and Escape returns focus to More")
                navigation.get_by_role("button",name="Zoom in scatter plot",exact=True).click()
                more.click()
                options=page.get_by_role("group",name="Scatter plot options",exact=True)
                options.get_by_role("button",name="Reset zoom",exact=True).click()
                expect(navigation.get_by_role("button",name="Reset zoom",exact=True)).to_have_attribute("data-zoomed","false")
                expect(more).to_have_attribute("aria-expanded","false")
                passed(theme+" More Reset zoom shares the visible navigation reset")
                canvas.focus()
                page.keyboard.press("Shift+F10")
                page.get_by_role("menuitem",name="Export CSV / PNG…",exact=True).click()
                dialog=page.get_by_role("dialog",name="Export scatter plot",exact=True)
                expect(dialog).to_be_visible()
                close(page,dialog)
                expect(canvas).to_be_focused()
                bounds=canvas.bounding_box()
                page.mouse.click(bounds["x"]+bounds["width"]*.6,bounds["y"]+bounds["height"]*.5,button="right")
                expect(page.get_by_role("menuitem",name="Export CSV / PNG…",exact=True)).to_be_visible()
                page.keyboard.press("Escape")
                passed(theme+" right-click and Shift+F10 preserve plot actions")
                for width in [1100,960]:
                    extra=page.evaluate("outerWidth-innerWidth")
                    cdp.send("Browser.setWindowBounds",{"windowId":window_id,"bounds":{"width":width+extra,"height":1000}})
                    page.wait_for_function("width=>innerWidth===width",arg=width)
                    row_pair(f"{theme}-{width}")
                extra=page.evaluate("outerWidth-innerWidth")
                cdp.send("Browser.setWindowBounds",{"windowId":window_id,"bounds":{"width":1600+extra,"height":1000}})
                initial=page.evaluate("devicePixelRatio")
                for factor in [1.25,1.5]:
                    set_browser_zoom(worker,page,factor)
                    page.wait_for_function("dpr=>Math.abs(devicePixelRatio-dpr)<.001",arg=initial*factor)
                    row_pair(f"{theme}-zoom-{factor}")
                    dialog=open_dialog(page)
                    close(page,dialog)
                    expect(more).to_be_focused()
                set_browser_zoom(worker,page,1)
            assert not fixture.blocked and not fixture.errors and not report["pageErrors"],(fixture.blocked,fixture.errors,report["pageErrors"])
            passed("GET-only fixture; no API mutations, unexpected routes or application errors")
        except Exception:
            report["failure"]=traceback.format_exc()
            capture_browser_view(cdp,output/"failure.png")
            raise
        finally:
            (output/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
            context.close()
    print(f"{len(report['checks'])} toolbar groups passed. Evidence: {output}",flush=True)


if __name__=="__main__":main()
