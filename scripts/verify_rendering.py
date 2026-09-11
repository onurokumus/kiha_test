"""Phase 1 browser regression checks, using isolated API fixtures (no backend writes).

Run a Vite dev server, then: python scripts/verify_rendering.py --url http://127.0.0.1:3100
Requires Python Playwright + its Chromium browser. Screenshots go to ignored data/verification.
"""
import argparse
import json
import math
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import sync_playwright

TEST = "Rendering_regression_" + "long_test_name_" * 9
X = "thrust_" + "long_sensor_identifier_" * 6 + "N"
Y = "torque_nm"
COLS = [X, Y, "rpm", "temperature_c", "current_a"]
POINTS = [
    {"id": i, "name": f"Point_{i}_" + "long_point_name_" * 7,
     "label": "Label_" + "unbroken" * 20, "start_s": (i - 1) * 10, "end_s": i * 10}
    for i in range(1, 5)
]
MEANS = {X: [50, 50, 90, 10], Y: [50, 50, 65, 15]}
SOURCE = {'name': TEST, 'id': '5f9a0189-c521-437a-95e1-92f2e90c8b6d', 'revision': 'fixture-v1',
          'status': 'ready', 'columns': COLS,
          'test_points': [{'id': point['id'], 'revision': f"point-{point['id']}"} for point in POINTS]}


def mock_api(route):
    url = urlparse(route.request.url)
    path = unquote(url.path).split("/api", 1)[1]
    query = parse_qs(url.query)
    assert route.request.method == "GET", f"Unexpected mutation: {route.request.method} {path}"
    info = {"name": TEST, "status": "ready", "n_rows": 4000, "fs_hz": 100,
            "duration_s": 40, "n_columns": len(COLS) + 1,
            "source_file": "legacy_source.csv", "created_at": "2026-09-08T08:00:00Z"}
    if path == "/settings/defaults":
        body = {"settings": None}
    elif path == "/analysis-sources":
        body = {'version': 1, 'sources': [SOURCE]}
    elif path.endswith('/annotations'):
        body = {'version': 1, 'test': TEST, 'revision': 0, 'annotations': [], 'data_bounds': [0, 40]}
    elif path == '/components':
        body = {'version': 1, 'components': []}
    elif path == '/trash':
        body = {'entries': [], 'retention_seconds': 3600}
    elif path == "/tests":
        body = [info]
    elif path == f"/tests/{TEST}":
        body = {**info, "columns": ["time_s", *COLS], "time_column": "time_s", "t_start": 0}
    elif path.endswith("/testpoints"):
        body = {"version": 1, "test": TEST, "test_points": POINTS}
    elif path.endswith("/tp_stats"):
        col = query["col"][0]
        values = MEANS.get(col, [20, 20, 30, 10])
        body = [{**point, "mean": values[i], "min": values[i] - 4,
                 "max": values[i] + 6, "n": 1000, "n_valid": 1000}
                for i, point in enumerate(POINTS)]
    elif "/testpoints/" in path and path.endswith("/data"):
        point = POINTS[int(path.split("/")[-2]) - 1]
        times = [i / 100 for i in range(1000)]
        body = {"test": TEST, "test_point": point, "mode": "raw", "level": 1,
                "n_raw": 1000, "i0": 0, "i1": 1000, "point_budget": 1500,
                "time_origin_s": point["start_s"], "duration_s": 10,
                "series": {col: {"t": times, "y": [50 + 10 * math.sin(t) for t in times]}
                           for col in query["cols"][0].split(",")}}
    elif path.endswith("/raw"):
        route.fulfill(status=200, content_type="text/csv", body="time_s,torque_nm\n0,50\n",
                      headers={"Content-Disposition": 'attachment; filename="legacy_source.csv"'})
        return
    else:
        raise AssertionError(f"Unexpected API request: {path}")
    route.fulfill(status=200, json=body)


def configure(page):
    settings = {"scatterX": X, "scatterY": Y, "clustering": False,
                "gridColumns": COLS[1:], "defaultViewMode": "tp"}
    session = {"version": 1, "sources": [SOURCE], "currentTest": TEST, "xAxis": X, "yAxis": Y,
               "axesUserSet": True, "selections": [{"test": TEST, "tpId": 1, "hidden": False}],
               "plotConfigs": COLS[1:], "plotsUserEdited": True, "plotDensity": "quad"}
    page.add_init_script(f"""localStorage.clear();
        localStorage.setItem('ptt.settings.v1', {json.dumps(json.dumps(settings))});
        localStorage.setItem('ptt.analysis-session.v1', {json.dumps(json.dumps(session))});""")
    page.context.route("**/api/**", mock_api)


def check_upload(page, output):
    page.get_by_role("button", name="Uploads", exact=True).click()
    link = page.get_by_role("link", name=f"Download original CSV for {TEST}")
    link.wait_for()
    geometry = link.evaluate("""el => {
        const range = document.createRange(); range.selectNodeContents(el);
        const text = range.getBoundingClientRect(), box = el.getBoundingClientRect();
        return {offsetX: text.x + text.width/2 - box.x - box.width/2,
                offsetY: text.y + text.height/2 - box.y - box.height/2};
    }""")
    print("CSV text center offset:", geometry)
    page.screenshot(path=str(output / "uploads.png"))
    assert abs(geometry["offsetX"]) <= 2 and abs(geometry["offsetY"]) <= 2, geometry
    link.focus()
    page.keyboard.press("Tab")
    page.keyboard.press("Shift+Tab")
    assert link.evaluate("el => el.matches(':focus-visible')")
    # Chromium bypasses route mocks for download links. Verify the native
    # keyboard activation and original endpoint without contacting real data.
    assert link.get_attribute("href").endswith("/raw")
    link.evaluate("""el => el.addEventListener('click', event => {
        event.preventDefault(); el.dataset.keyboardActivated = 'true';
    }, {once: true})""")
    page.keyboard.press("Enter")
    assert link.get_attribute("data-keyboard-activated") == "true"
    page.get_by_role("button", name="Analyze", exact=True).first.click()


def check_cursor(page, label="cursor", synchronized=False):
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    over = page.locator(".u-over").first
    over.wait_for()
    box = over.bounding_box()
    x, y = round(box["x"] + box["width"] * .42), round(box["y"] + box["height"] * .4)
    page.mouse.move(x, y)
    # Keep the pointer INSIDE during the old 300ms scale animation; re-entering
    # would refresh uPlot's cached rectangle and hide the original regression.
    page.wait_for_timeout(350)
    page.mouse.move(x + 2, y + 2)
    page.wait_for_timeout(30)
    cursor_x = page.locator(".u-cursor-x").first.bounding_box()
    cursor_y = page.locator(".u-cursor-y").first.bounding_box()
    assert abs(cursor_x["x"] - (x + 2)) <= 1.5, (label, "x", cursor_x, x + 2)
    assert abs(cursor_y["y"] - (y + 2)) <= 1.5, (label, "y", cursor_y, y + 2)
    if synchronized:
        fractions = page.locator(".uplot").evaluate_all("""plots => plots.map(plot => {
            const over = plot.querySelector('.u-over').getBoundingClientRect();
            const cursor = plot.querySelector('.u-cursor-x').getBoundingClientRect();
            return (cursor.left - over.left) / over.width;
        })""")
        assert max(fractions) - min(fractions) < .006, (label, fractions)
    print(label, "aligned")


def check_time_plots(page, output):
    check_cursor(page, "initial grid", synchronized=True)
    for width, height in [(1440, 1000), (1100, 800), (1700, 1050)]:
        page.set_viewport_size({"width": width, "height": height})
        page.get_by_role("button", name="Expand torque_nm", exact=True).evaluate("el => el.click()")
        check_cursor(page, f"maximized {width}")
        assert page.locator(".u-over").count() == 1
        page.get_by_role("button", name="Minimize torque_nm", exact=True).evaluate("el => el.click()")
        check_cursor(page, f"restored {width}", synchronized=True)
    page.set_viewport_size({"width": 1440, "height": 1000})
    expand = page.get_by_role("button", name="Expand torque_nm", exact=True)
    expand.focus()
    page.keyboard.press("Enter")
    check_cursor(page, "keyboard maximize")
    restore = page.get_by_role("button", name="Minimize torque_nm", exact=True)
    restore.focus()
    page.keyboard.press("Enter")
    check_cursor(page, "keyboard restore", synchronized=True)
    page.mouse.wheel(0, -120)
    page.wait_for_function("JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).timeZoom !== null")
    check_cursor(page, "wheel zoom", synchronized=True)
    zoom = page.evaluate("JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).timeZoom")
    box = page.locator(".u-over").first.bounding_box()
    x, y = box["x"] + box["width"] * .5, box["y"] + box["height"] * .5
    page.mouse.move(x, y)
    page.keyboard.down("Shift")
    page.mouse.down()
    page.mouse.move(x + 35, y, steps=6)
    page.mouse.up()
    page.keyboard.up("Shift")
    page.wait_for_timeout(250)
    panned = page.evaluate("JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).timeZoom")
    assert panned != zoom
    check_cursor(page, "shift-drag pan", synchronized=True)
    page.get_by_role("button", name="Reset zoom", exact=True).click()
    page.wait_for_function("JSON.parse(localStorage.getItem('ptt.analysis-session.v1')).timeZoom === null")
    check_cursor(page, "reset", synchronized=True)
    page.emulate_media(reduced_motion="reduce")
    page.get_by_role("button", name="Expand torque_nm", exact=True).evaluate("el => el.click()")
    check_cursor(page, "reduced motion maximize")
    page.screenshot(path=str(output / "time-maximized.png"))
    page.get_by_role("button", name="Minimize torque_nm", exact=True).click()
    page.emulate_media(reduced_motion="no-preference")


def range_geometry(page):
    return page.locator(".scatter-range-series line").evaluate_all("""lines => lines.map(line =>
        ['x1','y1','x2','y2'].map(a => Number(line.getAttribute(a))))""")


def check_scatter(page, output):
    page.get_by_role("button", name=re.compile("^More")).click()
    horizontal = page.get_by_role("switch", name="Show horizontal X-axis minimum-to-maximum range bars")
    vertical = page.get_by_role("switch", name="Show vertical Y-axis minimum-to-maximum range bars")
    horizontal.focus()
    page.keyboard.press("Space")
    vertical.click()
    page.keyboard.press("Escape")
    page.wait_for_selector('.scatter-range-series line', state="attached")
    assert len(range_geometry(page)) == 24
    initial = range_geometry(page)
    for cycle in range(4):
        chart = page.locator(".recharts-wrapper").bounding_box()
        x, y = chart["x"] + chart["width"] * .7, chart["y"] + chart["height"] * .55
        page.mouse.move(x, y)
        page.mouse.wheel(0, -120)
        page.wait_for_timeout(150)
        assert len(range_geometry(page)) == 24
        assert range_geometry(page) != initial
        page.mouse.down()
        page.mouse.move(x + 40, y + 20, steps=6)
        page.mouse.up()
        assert len(range_geometry(page)) == 24
        reset = page.get_by_role("button", name="Reset zoom", exact=True)
        reset.focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(100)
        assert range_geometry(page) == initial, f"stale geometry after reset {cycle}"
    # Check the range midlines meet at the current scatter centers and retain
    # the asymmetric 4/6 data distances, including overlapping identities.
    point_geometry = page.locator("[data-range-point-id]").evaluate_all("""groups => groups.map(group => {
        const x = group.querySelector('[data-range-axis="x"] line:nth-child(2)');
        const y = group.querySelector('[data-range-axis="y"] line:nth-child(2)');
        return {cx: +y.getAttribute('x1'), cy: +x.getAttribute('y1'),
            xmin: +x.getAttribute('x1'), xmax: +x.getAttribute('x2'),
            ymin: +y.getAttribute('y1'), ymax: +y.getAttribute('y2')};
    })""")
    assert point_geometry[0] == point_geometry[1]
    for point in point_geometry:
        assert abs((point["cx"] - point["xmin"]) / (point["xmax"] - point["cx"]) - 4/6) < .001
        assert abs((point["ymin"] - point["cy"]) / (point["cy"] - point["ymax"]) - 4/6) < .001
    page.get_by_role("button", name=re.compile("^More")).click()
    horizontal.click()
    assert len(range_geometry(page)) == 12
    vertical.click()
    assert len(range_geometry(page)) == 0
    horizontal.click()
    assert len(range_geometry(page)) == 12
    vertical.click()
    page.keyboard.press("Escape")
    # Hover the rightmost point, whose tooltip must cross the panel boundary.
    dot = page.locator('.recharts-scatter-symbol').nth(2).locator('circle[fill]:not([fill="none"])').first
    dot.hover()
    tooltip = page.get_by_role("tooltip")
    tooltip.wait_for()
    assert tooltip.evaluate("el => el.parentElement === document.body")
    box = tooltip.bounding_box()
    panel = page.locator(".analyze-scatter-panel").bounding_box()
    assert box["x"] + box["width"] > panel["x"] + panel["width"]
    overflow = tooltip.evaluate("""el => {
        const box = el.getBoundingClientRect();
        return [...el.querySelectorAll('*')].some(child => {
            const range = document.createRange(); range.selectNodeContents(child);
            return [...range.getClientRects()].some(r => r.left < box.left || r.right > box.right
                || r.top < box.top || r.bottom > box.bottom);
        });
    }""")
    assert not overflow, "tooltip text overflow"
    # Temporarily enable hit testing to verify the card actually paints above
    # the adjacent canvas; production stays pointer-transparent.
    assert tooltip.evaluate("""el => {
        el.style.pointerEvents = 'auto'; const r = el.getBoundingClientRect();
        const top = document.elementFromPoint(r.right - 20, r.top + 20);
        el.style.removeProperty('pointer-events'); return el.contains(top);
    }""")
    page.screenshot(path=str(output / "scatter-tooltip.png"))
    page.keyboard.press("Escape")
    assert page.get_by_role("tooltip").count() == 0
    page.mouse.move(20, 20)
    dot.hover()
    page.get_by_role("tooltip").wait_for()
    dot.click()
    page.get_by_role("button", name=re.compile("^2 of 20 test points selected")).wait_for()
    dot.click()
    page.get_by_role("button", name=re.compile("^1 of 20 test points selected")).wait_for()
    page.get_by_role("button", name=re.compile("Filters$")).click()
    expand_test = page.get_by_role("button", name=f"Expand {TEST}", exact=True)
    if expand_test.count():
        expand_test.click()
    page.get_by_role("checkbox", name=f'{POINTS[0]["name"]} — {POINTS[0]["label"]}', exact=True).check()
    assert len(range_geometry(page)) == 6
    assert page.locator('[data-range-point-id]').get_attribute('data-range-point-id') == f"{TEST}:1"
    page.keyboard.press("Escape")
    page.get_by_role("button", name="Clear all scatter filters").click()
    assert len(range_geometry(page)) == 24
    assert range_geometry(page) == initial
    print("scatter: 4 zoom/pan/reset cycles, toggles, filtering, selection, long tooltip, stacking, Escape passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3100")
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / "data" / "verification"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        configure(page)
        page.goto(args.url)
        page.wait_for_load_state("networkidle")
        page.locator(".u-over").first.wait_for()
        page.screenshot(path=str(output / "initial.png"))
        if args.inspect:
            print(page.locator("button, a").evaluate_all("els => els.map(e => ({text:e.innerText, label:e.getAttribute('aria-label')}))"))
            print("errors", errors)
        else:
            check_upload(page, output)
            check_time_plots(page, output)
            check_scatter(page, output)
            assert not errors, errors
            print("PASS: rendering regression checks; no browser console/page errors")
        browser.close()


if __name__ == "__main__":
    main()
