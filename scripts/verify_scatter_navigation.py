"""Scatter navigation/grid regression with isolated GET-only browser fixtures.

Uses the export fixture's known point values and reads the actual rendered SVG
axes to recover each displayed domain. No application internals are exposed and
no user datasets are changed. Evidence: %TEMP%/ptt-scatter-navigation.
"""
import argparse
import json
import os
import tempfile
import traceback
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from verify_browser_zoom import capture_browser_view, set_browser_zoom
from verify_scatter_exports import Fixture, KEY, settled

MEASURE = """canvas => {
  const svg=canvas.querySelector('svg.recharts-surface'), sr=svg.getBoundingClientRect();
  const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}};
  const axes=['x','y'].map(axis=>{
    const element=svg.querySelector('.recharts-'+axis+'Axis');
    const line=element.querySelector('.recharts-cartesian-axis-line');
    const points=[...element.querySelectorAll('.recharts-cartesian-axis-tick-value')].map(text=>({
      value:Number(text.textContent.replaceAll(',','')),label:text.textContent,
      pixel:Number(text.getAttribute(axis)),rect:rect(text)}));
    const origin=points[0],other=points.at(-1),scale=(other.pixel-origin.pixel)/(other.value-origin.value);
    const value=pixel=>origin.value+(pixel-origin.pixel)/scale;
    const first=Number(line.getAttribute(axis+'1')),last=Number(line.getAttribute(axis+'2'));
    return {points,first,last,range:[value(first),value(last)],line:rect(line)};
  });
  const [x,y]=axes, view=[Math.min(...x.range),Math.max(...x.range),Math.min(...y.range),Math.max(...y.range)];
  const plot={x:sr.x+Math.min(x.first,x.last),y:sr.y+Math.min(y.first,y.last),w:Math.abs(x.last-x.first),h:Math.abs(y.last-y.first)};
  const grids=[...svg.querySelectorAll('.scatter-grid line')].map(line=>({
    layer:line.parentElement.getAttribute('data-grid-layer'),x1:Number(line.getAttribute('x1')),x2:Number(line.getAttribute('x2')),
    y1:Number(line.getAttribute('y1')),y2:Number(line.getAttribute('y2')),stroke:getComputedStyle(line).stroke,dash:getComputedStyle(line).strokeDasharray}));
  const toolbar=document.querySelector('[aria-label="Scatter navigation"]');
  const buttons=[...toolbar.querySelectorAll('button')].map(button=>({name:button.getAttribute('aria-label')||button.textContent,...rect(button)}));
  const overlaps=buttons.flatMap((a,i)=>buttons.slice(i+1).filter(b=>Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x)>1&&Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y)>1).map(b=>[a.name,b.name]));
  return {view,axes,plot,canvas:rect(canvas),toolbar:rect(toolbar),buttons,overlaps,grids,overflow:document.documentElement.scrollWidth-innerWidth,
    width:innerWidth,dpr:devicePixelRatio,mode:canvas.dataset.dragMode,dragging:canvas.dataset.dragging};
}"""


def close_numbers(actual, expected, label, tolerance=1e-7):
    assert len(actual) == len(expected)
    assert all(abs(a-b) <= tolerance * max(1, abs(b)) for a, b in zip(actual, expected)), (label, actual, expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8087/ptt/")
    args = parser.parse_args()
    output = Path(os.environ.get("TEMP", tempfile.gettempdir())) / "ptt-scatter-navigation"
    output.mkdir(exist_ok=True)
    report = {"checks": [], "geometry": [], "pageErrors": []}

    with tempfile.TemporaryDirectory(prefix="ptt-scatter-navigation-") as temporary, sync_playwright() as pw:
        temporary = Path(temporary)
        extension = temporary / "extension"
        extension.mkdir()
        (extension / "manifest.json").write_text(json.dumps({"manifest_version": 3, "name": "Isolated scatter navigation zoom",
            "version": "1.0", "permissions": ["tabs"], "background": {"service_worker": "background.js"}}), encoding="utf-8")
        (extension / "background.js").write_text("chrome.runtime.onInstalled.addListener(()=>{});", encoding="utf-8")
        context = pw.chromium.launch_persistent_context(str(temporary / "profile"), channel="chromium", headless=True,
            no_viewport=True, color_scheme="light", reduced_motion="reduce", args=[f"--disable-extensions-except={extension}",
            f"--load-extension={extension}", "--window-size=1600,1000"])
        page = context.pages[0]
        page.set_default_timeout(12000)
        fixture = Fixture()
        fixture.configure(page)
        page.add_init_script("window.addEventListener('pointerdown',event=>{window.__navigationPointer=event.pointerId},true)")
        page.on("pageerror", lambda error: report["pageErrors"].append(str(error)))
        cdp = context.new_cdp_session(page)
        window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
        canvas = page.get_by_label("Plot canvas for test-point overview", exact=True)
        toolbar = page.get_by_role("group", name="Scatter navigation", exact=True)
        reset_button = toolbar.get_by_role("button", name="Reset zoom", exact=True)

        def frames():
            page.evaluate("() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))")

        def measure():
            frames()
            return canvas.evaluate(MEASURE)

        def passed(name):
            report["checks"].append(name)
            print("PASS:", name, flush=True)

        def picture(name):
            page.keyboard.press("Escape")
            page.mouse.move(0, 0)
            frames()
            capture_browser_view(cdp, output / f"{name}.png")

        def reset():
            reset_button.click()
            expect(reset_button).to_have_attribute("data-zoomed", "false")
            return measure()

        def mode(value):
            toolbar.get_by_role("button", name="Pan scatter plot" if value == "pan" else "Box zoom scatter plot", exact=True).click()
            expect(canvas).to_have_attribute("data-drag-mode", value)

        def position(plot, x, y):
            return {"x": plot["x"]+plot["w"]*x, "y": plot["y"]+plot["h"]*y}

        def check_grid(name):
            data = measure()
            report["geometry"].append({"name": name, **data})
            assert data["overflow"] <= 1 and not data["overlaps"], (name, data)
            assert data["toolbar"]["y"] + data["toolbar"]["h"] <= data["canvas"]["y"] + 1, data
            assert data["grids"], "Missing engineering grid"
            for axis in data["axes"]:
                assert len(axis["points"]) >= 2
                assert len({point["label"] for point in axis["points"]}) == len(axis["points"])
                for point in axis["points"]:
                    box=point["rect"]
                    assert box["x"] >= data["canvas"]["x"]-1 and box["x"]+box["w"] <= data["canvas"]["x"]+data["canvas"]["w"]+1, (name,"clipped tick label",point)
                if abs(axis["range"][1]-axis["range"][0]) > 1:
                    assert all(len(point["label"]) < 12 for point in axis["points"]), (name,"unnecessarily long fixture tick labels",axis)
            for line in data["grids"]:
                assert line["dash"] == "none", line
                vertical = line["x1"] == line["x2"]
                axis = data["axes"][0 if vertical else 1]
                if line["layer"] in {"major", "zero"}:
                    assert min(abs(point["pixel"]-line["x1" if vertical else "y1"]) for point in axis["points"]) < .1, line
            # Measure axes from SVG, allowing ResponsiveContainer's rounding
            # and different gutters without baking layout constants into QA.
            assert data["plot"]["w"] > 0 and data["plot"]["h"] > 0
            assert data["plot"]["x"] >= data["canvas"]["x"] and data["plot"]["y"] >= data["canvas"]["y"]
            assert data["plot"]["x"]+data["plot"]["w"] <= data["canvas"]["x"]+data["canvas"]["w"]+1
            return data

        def wheel_roundtrip(name):
            base = reset()
            plot, view = base["plot"], base["view"]
            anchor = position(plot, .37, .61)
            page.mouse.move(**anchor)
            page.mouse.wheel(0, -120)
            page.wait_for_timeout(70)
            zoomed = measure()["view"]
            assert zoomed[1]-zoomed[0] < view[1]-view[0]
            # Pointer coordinates round to CSS pixels when dispatched by Chromium.
            actual_x, actual_y = round(anchor["x"]), round(anchor["y"])
            rx, ry = (actual_x-plot["x"])/plot["w"], 1-(actual_y-plot["y"])/plot["h"]
            before_anchor = [view[0]+rx*(view[1]-view[0]), view[2]+ry*(view[3]-view[2])]
            after_anchor = [zoomed[0]+rx*(zoomed[1]-zoomed[0]), zoomed[2]+ry*(zoomed[3]-zoomed[2])]
            close_numbers(after_anchor, before_anchor, name+" wheel pointer anchor", tolerance=.005)
            page.mouse.wheel(0, 120)
            page.wait_for_timeout(70)
            close_numbers(measure()["view"], view, name+" inverse wheel")
            page.mouse.move(plot["x"]-30, plot["y"]+plot["h"]*.5)
            page.mouse.wheel(0, -120)
            page.wait_for_timeout(70)
            close_numbers(measure()["view"], view, name+" wheel over axis label ignored")
            passed(name+" wheel cursor anchor, inverse and axis-label guard")

        def pan_checks(name):
            mode("pan")
            base = reset()
            view, plot = base["view"], base["plot"]
            start = position(plot, .35, .42)
            page.mouse.move(**start)
            page.mouse.down()
            # Same event turn: pointerup's final coordinates must win over the
            # earlier move and pending animation frame. Start is a real pointer.
            page.evaluate("""({x,y})=>{
              const common={pointerId:window.__navigationPointer,pointerType:'mouse',bubbles:true};
              window.dispatchEvent(new PointerEvent('pointermove',{...common,clientX:x+7,clientY:y+5,buttons:1}));
              window.dispatchEvent(new PointerEvent('pointerup',{...common,clientX:x+55,clientY:y+23,buttons:0}));
            }""", start)
            page.mouse.up()
            expected = [view[0]-55/plot["w"]*(view[1]-view[0]), view[1]-55/plot["w"]*(view[1]-view[0]),
                        view[2]+23/plot["h"]*(view[3]-view[2]), view[3]+23/plot["h"]*(view[3]-view[2])]
            close_numbers(measure()["view"], expected, name+" final pointerup frame", tolerance=.005)
            base = reset()
            start = position(base["plot"], .8, .5)
            end = {"x": base["plot"]["x"]+base["plot"]["w"]+60, "y": start["y"]}
            page.mouse.move(**start)
            page.mouse.down()
            page.mouse.move(**end, steps=5)
            page.mouse.up()
            after = measure()["view"]
            shift = (end["x"]-start["x"])/base["plot"]["w"]*(view[1]-view[0])
            close_numbers(after, [view[0]-shift,view[1]-shift,view[2],view[3]], name+" outside pan", tolerance=.005)
            base = reset()
            start = position(base["plot"], .4, .4)
            page.mouse.move(**start)
            page.mouse.down()
            page.mouse.move(start["x"]+35,start["y"]+25,steps=4)
            frames()
            page.keyboard.press("Escape")
            page.mouse.up()
            close_numbers(measure()["view"], view, name+" Escape restores pan")
            expect(canvas).to_have_attribute("data-dragging", "false")
            passed(name+" pan final frame, outside capture and Escape restoration")

        def box_checks(name, every_direction=True):
            directions = [(.2,.25,.7,.75),(.7,.75,.2,.25),(.7,.25,.2,.75),(.2,.75,.7,.25)] if every_direction else [(.7,.75,.2,.25)]
            mode("zoom")
            for sx,sy,ex,ey in directions:
                base = reset()
                start, end = position(base["plot"],sx,sy), position(base["plot"],ex,ey)
                page.mouse.move(**start)
                page.mouse.down()
                page.mouse.move(**end, steps=5)
                frames()
                preview = page.locator('[data-scatter-zoom-box="true"]')
                expect(preview).to_be_visible()
                bounds = preview.bounding_box()
                assert abs(bounds["x"]-min(start["x"],end["x"])) <= 1.5 and abs(bounds["y"]-min(start["y"],end["y"])) <= 1.5, bounds
                page.mouse.up()
                after = measure()["view"]
                v=base["view"]
                expected=[v[0]+.2*(v[1]-v[0]),v[0]+.7*(v[1]-v[0]),v[2]+.25*(v[3]-v[2]),v[2]+.75*(v[3]-v[2])]
                close_numbers(after, expected, name+" box direction", tolerance=.01)
                expect(preview).to_have_count(0)
            mode("pan")
            base=reset()
            start,end=position(base["plot"],.3,.3),position(base["plot"],.65,.65)
            page.keyboard.down("Shift")
            page.mouse.move(**start)
            page.mouse.down()
            page.mouse.move(**end,steps=4)
            frames()
            expect(page.locator('[data-scatter-zoom-box="true"]')).to_be_visible()
            page.mouse.up()
            page.keyboard.up("Shift")
            after=measure()["view"]
            assert after[1]-after[0] < .4*(base["view"][1]-base["view"][0])
            expect(canvas).to_have_attribute("data-drag-mode","pan")
            base=reset()
            page.keyboard.down("Shift")
            page.mouse.move(**start)
            page.mouse.down()
            page.mouse.move(**end,steps=4)
            frames()
            page.keyboard.press("Escape")
            page.mouse.up()
            page.keyboard.up("Shift")
            close_numbers(measure()["view"],base["view"],name+" cancelled box")
            expect(page.locator('[data-scatter-zoom-box="true"]')).to_have_count(0)
            passed(name+" box zoom directions, preview placement, Shift shortcut and Escape")

        def keyboard_checks(name):
            base=reset()
            canvas.focus()
            page.keyboard.press("+")
            zoomed=measure()["view"]
            close_numbers([zoomed[1]-zoomed[0],zoomed[3]-zoomed[2]],
                          [(.8)*(base["view"][1]-base["view"][0]),(.8)*(base["view"][3]-base["view"][2])],name+" keyboard zoom")
            page.keyboard.press("-")
            close_numbers(measure()["view"],base["view"],name+" keyboard inverse")
            for key,reverse in [("ArrowRight","ArrowLeft"),("ArrowUp","ArrowDown")]:
                page.keyboard.press(key)
                assert measure()["view"] != base["view"]
                page.keyboard.press(reverse)
                close_numbers(measure()["view"],base["view"],name+" keyboard arrow inverse")
            page.keyboard.press("z")
            expect(canvas).to_have_attribute("data-drag-mode","zoom")
            page.keyboard.press("p")
            expect(canvas).to_have_attribute("data-drag-mode","pan")
            page.keyboard.press("+")
            page.keyboard.press("Home")
            close_numbers(measure()["view"],base["view"],name+" Home reset")
            expect(reset_button).to_have_attribute("data-zoomed","false")
            after=measure()
            close_numbers(list(after["toolbar"].values()),list(base["toolbar"].values()),name+" toolbar remains fixed")
            passed(name+" keyboard +/-/arrows/P/Z/Home and stable reset toolbar")

        def selection_checks(name):
            mode("pan")
            reset()
            chips=page.get_by_role("list",name="Selected test points",exact=True).locator("li")
            expect(chips).to_have_count(1)
            dot=canvas.locator('circle[data-export-stroke]:not([data-export-stroke="transparent"])').first
            location=dot.bounding_box()
            point={"x":location["x"]+location["width"]/2,"y":location["y"]+location["height"]/2}
            page.mouse.click(**point)
            expect(chips).to_have_count(0)
            page.mouse.click(**point)
            expect(chips).to_have_count(1)
            frames()
            page.mouse.move(**point)
            page.mouse.down()
            page.mouse.move(point["x"]+30,point["y"]-22,steps=6)
            page.mouse.up()
            expect(chips).to_have_count(1)
            expect(reset_button).to_have_attribute("data-zoomed","true")
            reset()
            passed(name+" ordinary point clicks select; dragging a point pans without toggling selection")

        try:
            page.goto(args.url)
            settled(page)
            for theme in ["light","dark"]:
                if page.locator("html").get_attribute("data-theme") != theme:
                    page.get_by_role("switch",name="Dark mode",exact=True).click()
                page.mouse.move(0,0)
                check_grid(theme)
                picture(theme+"-grid")
                passed(theme+" solid grid, distinct ticks, toolbar and real-axis alignment")
                wheel_roundtrip(theme)
                pan_checks(theme)
                box_checks(theme)
                keyboard_checks(theme)
                selection_checks(theme)
            for width in [960,1100]:
                chrome_width=page.evaluate("outerWidth-innerWidth")
                cdp.send("Browser.setWindowBounds",{"windowId":window_id,"bounds":{"width":width+chrome_width,"height":1000}})
                page.wait_for_function("width=>innerWidth===width",arg=width)
                check_grid(f"width-{width}")
                wheel_roundtrip(f"width-{width}")
                box_checks(f"width-{width}",False)
                reset()
                picture(f"width-{width}")
            chrome_width=page.evaluate("outerWidth-innerWidth")
            cdp.send("Browser.setWindowBounds",{"windowId":window_id,"bounds":{"width":1600+chrome_width,"height":1000}})
            initial_dpr=page.evaluate("devicePixelRatio")
            for factor in [1.25,1.5]:
                set_browser_zoom(worker,page,factor)
                page.wait_for_function("dpr=>Math.abs(devicePixelRatio-dpr)<.001",arg=initial_dpr*factor)
                check_grid(f"zoom-{factor}")
                wheel_roundtrip(f"zoom-{factor}")
                box_checks(f"zoom-{factor}",False)
                keyboard_checks(f"zoom-{factor}")
                reset()
                picture(f"zoom-{factor}")
            set_browser_zoom(worker,page,1)
            chrome_width=page.evaluate("outerWidth-innerWidth")
            cdp.send("Browser.setWindowBounds",{"windowId":window_id,"bounds":{"width":1100+chrome_width,"height":1000}})
            page.wait_for_function("innerWidth===1100")
            page.wait_for_timeout(400)
            precision_view=[5000,5000.0001,10,10.0001]
            page.evaluate("([key,view])=>{const session=JSON.parse(localStorage.getItem(key));session.mainZoom=view;localStorage.setItem(key,JSON.stringify(session));}",[KEY,precision_view])
            page.reload()
            settled(page)
            precision=check_grid("precision-view-1100")
            close_numbers(precision["view"],precision_view,"deep-zoom displayed axis precision",tolerance=1e-11)
            picture("precision-view-1100")
            passed("Deep zoom at1100px retains distinct labels without edge clipping")
            report["requests"]=len(fixture.requests)
            assert not fixture.errors and not fixture.blocked and not report["pageErrors"],(fixture.errors,fixture.blocked,report["pageErrors"])
            passed("GET-only fixtures; no unexpected requests or application errors")
        except Exception:
            report["failure"]=traceback.format_exc()
            capture_browser_view(cdp,output/"failure.png")
            raise
        finally:
            (output/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
            context.close()
    print(f"{len(report['checks'])} scatter navigation groups passed. Evidence: {output}",flush=True)


if __name__ == "__main__":
    main()
