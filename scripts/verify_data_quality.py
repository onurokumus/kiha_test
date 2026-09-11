"""Live Phase 3 checks using temporary backend data and an isolated Chromium profile.

Run with global Python + Playwright; the backend subprocess always uses its
Python 3.13 venv and run.py. Owns only its two child servers (ports must be free).
No user datasets/settings/profiles are read or changed. Screenshots and logs
remain in ignored data/verification/data-quality; all fixtures are temporary.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from verify_browser_zoom import capture_browser_view, set_browser_zoom

ROOT = Path(__file__).resolve().parents[1]
LONG_COL = "force_" + "long_sensor_identifier_" * 7 + "N"


def wait_until(callback, label, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = callback()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError(f"Timed out: {label}")


def responding(url):
    try:
        with urlopen(url, timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


@contextmanager
def servers(temporary, output, frontend_port, backend_port):
    # Refuse to reuse/stop an unrelated listener. Direct Popen children avoid
    # shell wrapper processes that could outlive cleanup on Windows.
    for port in (frontend_port, backend_port):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    web = f"http://127.0.0.1:{frontend_port}"
    api = f"http://127.0.0.1:{backend_port}/api"
    dataset = temporary / "backend-data"
    backend_env = {**os.environ, "KIHA_DATA_DIR": str(dataset),
                   "KIHA_PORT": str(backend_port), "KIHA_CORS_ORIGINS": web}
    frontend_env = {**os.environ, "VITE_API_BASE": api, "VITE_BASE_PATH": "/", "BROWSER": "none"}
    children = []
    logs = []
    try:
        for name, args, cwd, env in [
            ("backend", [str(ROOT / "backend/.venv/Scripts/python.exe"), "run.py"],
             ROOT / "backend", backend_env),
            ("frontend", [shutil.which("node"), "node_modules/vite/bin/vite.js",
                          "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort"],
             ROOT / "frontend", frontend_env),
        ]:
            log = (output / f"{name}.log").open("w", encoding="utf-8")
            logs.append(log)
            children.append(subprocess.Popen(args, cwd=cwd, env=env, stdout=log,
                                            stderr=subprocess.STDOUT,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        wait_until(lambda: responding(api + "/tests"), "isolated backend startup")
        wait_until(lambda: responding(web), "isolated frontend startup")
        yield web, api, dataset
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        for log in logs:
            log.close()


def upload(request, name, content, **settings):
    body = content.encode()
    response = request.post("uploads", data={
        "name": name, "source_file": f"{name}.csv", "size_bytes": len(body),
        "last_modified_ms": 1, "uploader_name": "Isolated quality verification", **settings})
    assert response.status == 201, response.text()
    upload_id = response.json()["upload_id"]
    response = request.put(f"uploads/{upload_id}/chunks/0?name={name}",
                           multipart={"file": {"name": "chunk", "mimeType": "application/octet-stream", "buffer": body}},
                           headers={"X-Chunk-SHA256": hashlib.sha256(body).hexdigest()})
    assert response.ok, response.text()
    assert request.post(f"uploads/{upload_id}/complete?name={name}").ok
    return wait_until(lambda: next((test for test in request.get("tests").json()
                                   if test["name"] == name and test["status"] in {"ready", "error"}), None), name)


def open_quality(page, name):
    page.get_by_role("searchbox", name="Search upload history").fill(name)
    button = page.get_by_role("button", name=re.compile(f"^Data quality for {re.escape(name)}:"))
    button.wait_for()
    if button.get_attribute("aria-expanded") != "true":
        button.focus()
        page.keyboard.press("Enter")
    details = page.get_by_role("region", name=f"Data quality details for {name}", exact=True)
    details.wait_for()
    return button, details


def check_layout(details):
    assert details.evaluate("""el => {
        const tables = [...el.querySelectorAll('table')];
        return el.scrollWidth <= el.clientWidth + 1 && tables.every(table =>
            table.getBoundingClientRect().width <= table.parentElement.clientWidth + 1);
    }"""), "Quality details or nested counts overflow their container"


def run_checks(web, api, dataset, temporary, output):
    extension = temporary / "zoom-extension"
    extension.mkdir()
    (extension / "manifest.json").write_text(json.dumps({
        "manifest_version": 3, "name": "Isolated data quality zoom checks", "version": "1.0",
        "permissions": ["tabs"], "background": {"service_worker": "background.js"}}))
    (extension / "background.js").write_text("chrome.runtime.onInstalled.addListener(() => {});")
    with sync_playwright() as playwright:
        request = playwright.request.new_context(base_url=api + "/")
        clean = "time,signal\n" + "\n".join(f"{i / 10:.1f},{i}.0" for i in range(40))
        assert upload(request, "clean", clean)["status"] == "ready"
        assert upload(request, "legacy", clean)["status"] == "ready"
        assert upload(request, "pending", clean)["status"] == "ready"
        assert upload(request, "generated", clean, time_mode="generated", time_column="time", fs_hz=10)["status"] == "ready"
        gap_csv = f"time,{LONG_COL},infinite_signal,note\n" + "\n".join(
            f"{i / 10:.1f},{'' if i == 5 else i},inf,rig note" for i in range(80) if not 30 <= i < 40)
        assert upload(request, "gap", gap_csv)["status"] == "ready"
        assert upload(request, "failed", "time,signal\n0,1\n")["status"] == "error"
        assert upload(request, "sparse", "time,signal\n0,1\n" + "\n".join(f"{i}," for i in range(1, 20)))["status"] == "ready"
        assert upload(request, "epoch", "time,signal\n1760000000.125,1\n1760000000.126,2\n1760000000.124,3\n")["status"] == "ready"
        legacy_path = dataset / "tests/legacy/meta.json"
        legacy = json.loads(legacy_path.read_text())
        for key in ("source_time_quality", "inf_counts", "time_gap_count", "time_gap_ranges"):
            legacy.pop(key, None)
        legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
        pending_path = dataset / "tests/pending/status.json"
        pending_path.write_text(json.dumps({"status": "rebuilding"}), encoding="utf-8")
        context = playwright.chromium.launch_persistent_context(
            str(temporary / "browser-profile"), channel="chromium", headless=True, no_viewport=True,
            args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}",
                  "--window-size=1440,1000"])
        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
            page = context.pages[0]
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.goto(web)
            page.wait_for_load_state("networkidle")
            print("Rendered navigation:", page.get_by_role("button").all_text_contents()[:12], flush=True)
            page.get_by_role("button", name="Uploads", exact=True).click()
            page.screenshot(path=str(output / "history.png"))

            # Real UI upload exercises staged options, transfer, ingest and live polling.
            with page.expect_file_chooser() as chosen:
                page.get_by_role("button", name=re.compile("Import test data")).click()
            chosen.value.set_files({"name": "clock.csv", "mimeType": "text/csv",
                                   "buffer": b"time,signal\n10,1\n11,2\n11,3\n9,4\n,5\n12,6\n"})
            page.get_by_label("Uploaded by", exact=True).fill("Quality browser check")
            page.get_by_label("Fallback rate (Hz)", exact=True).fill("50")
            page.get_by_role("button", name="Upload 1 file", exact=True).click()
            button, details = open_quality(page, "clock")
            expect(details).to_contain_text("Source time was unusable", timeout=15000)
            expect(details).to_contain_text("Duplicate steps: 1")
            expect(details).to_contain_text("Backward steps: 1")
            expect(details).to_contain_text("Missing / invalid timestamps: 1")
            details.get_by_text("Backward steps: 1 · show 1 example", exact=True).click()
            expect(details).to_contain_text("Rows 3 → 4: 11 → 9 s")
            expect(page.get_by_role("button", name="Analyze clock", exact=True)).to_be_enabled()
            close = details.get_by_role("button", name="Close data quality for clock")
            close.focus()
            page.keyboard.press("Escape")
            expect(button).to_be_focused()
            expect(button).to_have_attribute("aria-expanded", "false")
            print("PASS: real UI upload, source fallback details, keyboard disclosure/Escape", flush=True)

            _, details = open_quality(page, "gap")
            expect(details).to_contain_text("Missing cells: 21")
            expect(details).to_contain_text("Infinite cells: 70")
            expect(details).to_contain_text("Spectrum cannot cross a known time gap")
            expect(details).to_contain_text("No finite samples")
            details.get_by_text("Show current gap intervals (1 of 1)", exact=True).click()
            expect(details).to_contain_text("[30, 40) · 3–4 s")
            details.get_by_text("Source gaps: 1 · show 1 example", exact=True).click()
            expect(details).to_contain_text("Rows 30 → 31: 2.9 → 4 s")
            cdp = context.new_cdp_session(page)
            window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
            initial_dpr = page.evaluate("devicePixelRatio")
            for width, factor in [(1440, 1), (1100, 1), (1440, 1.25), (1440, 1.5)]:
                cdp.send("Browser.setWindowBounds", {"windowId": window_id,
                         "bounds": {"width": width, "height": 1000}})
                set_browser_zoom(worker, page, factor)
                page.wait_for_function("expected => Math.abs(devicePixelRatio - expected) < .001", arg=initial_dpr * factor)
                details.scroll_into_view_if_needed()
                check_layout(details)
                counts = details.get_by_role("region", name="Affected columns", exact=True)
                counts.focus()
                expect(counts).to_be_focused()
                capture_browser_view(cdp, output / f"details-{width}-{factor}.png")
            print("PASS: actual 100/125/150% browser zoom, window resize, long columns, keyboard access", flush=True)
            set_browser_zoom(worker, page, 1)

            source_before = request.get("tests/gap").json()["source_time_quality"]
            response = request.post("tests/gap/edit", data={"nan_policy": "zero_fill", "drop": ["infinite_signal"]})
            assert response.ok, response.text()
            expect(details).to_contain_text("Missing cells: 0", timeout=15000)
            expect(details).to_contain_text("Infinite cells: 0")
            expect(details).to_contain_text("Current time gaps: 0")
            expect(details).not_to_contain_text("Spectrum cannot cross a known time gap")
            assert request.get("tests/gap").json()["source_time_quality"] == source_before
            expect(details).to_contain_text("Source time gaps: 1")
            print("PASS: live edit refresh, repaired current counts, preserved source gaps", flush=True)

            # Freeze the human-readable edit date to reproduce two edits in one
            # second. Only the opaque metadata revision can trigger this refresh.
            previous_edit = request.get("tests/gap").json()["edited_at"]
            response = request.post("tests/gap/edit", data={"formulas": [
                {"name": "new_missing", "expression": "{" + LONG_COL + "}/0"}]})
            assert response.ok, response.text()
            latest = request.get("tests/gap").json()
            latest["edited_at"] = previous_edit
            gap_meta = dataset / "tests/gap/meta.json"
            staged = gap_meta.with_suffix(".verification.tmp")
            staged.write_text(json.dumps(latest), encoding="utf-8")
            staged.replace(gap_meta)
            expect(details).to_contain_text("Missing cells: 80", timeout=15000)
            expect(details).to_contain_text("new_missing")
            print("PASS: quality refresh when successive edits share edited_at", flush=True)

            button, details = open_quality(page, "clean")
            expect(button).to_contain_text("No issues found")
            expect(details).to_contain_text("Missing cells: 0")
            _, details = open_quality(page, "generated")
            expect(details).to_contain_text("source timestamps were not checked")
            expect(details).to_contain_text("cannot reveal acquisition gaps")
            _, details = open_quality(page, "sparse")
            expect(details).to_contain_text("Only 1 finite sample")
            expect(details).to_contain_text("fewer than two finite samples cannot produce a spectrum")
            _, details = open_quality(page, "epoch")
            details.get_by_text("Backward steps: 1 · show 1 example", exact=True).click()
            expect(details).to_contain_text("1,760,000,000.126 → 1,760,000,000.124 s")
            button, details = open_quality(page, "pending")
            expect(button).to_contain_text("Checks pending")
            expect(details).to_contain_text("Quality checks are pending")
            pending_path.write_text(json.dumps({"status": "ready"}), encoding="utf-8")
            expect(button).to_contain_text("No issues found", timeout=15000)
            expect(details).to_contain_text("Missing cells: 0")
            button, details = open_quality(page, "failed")
            expect(button).to_contain_text("Analysis unavailable")
            expect(details).to_contain_text("at least 2 samples")
            expect(page.get_by_role("button", name="Analyze failed", exact=True)).to_have_count(0)
            print("PASS: clean, generated, processing-to-ready and failed states", flush=True)

            # Inject only a read failure, then verify recovery on the live endpoint.
            page.route(api + "/tests/legacy", lambda route: route.fulfill(
                status=503, headers={"Access-Control-Allow-Origin": web},
                json={"detail": "Isolated metadata failure"}))
            button, details = open_quality(page, "legacy")
            expect(button).to_contain_text("Checks incomplete")
            expect(details).to_contain_text("Could not load data-quality details")
            page.unroute(api + "/tests/legacy")
            details.get_by_role("button", name="Retry quality details").click()
            expect(details).to_contain_text("Source timestamp checks were not recorded")
            expect(details).to_contain_text("Infinite cells: not recorded")
            # Search and polling must not manufacture missing legacy provenance.
            assert json.loads(legacy_path.read_text()) == legacy
            errors = [error for error in errors if "503" not in error]
            assert not errors, errors
            print("PASS: legacy coverage, metadata failure/retry; no unexpected console/page errors", flush=True)
        except Exception:
            page.screenshot(path=str(output / "failure.png"), full_page=True)
            raise
        finally:
            context.close()
            request.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-port", type=int, default=3100)
    parser.add_argument("--backend-port", type=int, default=8100)
    args = parser.parse_args()
    output = ROOT / "data/verification/data-quality"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ptt-quality-") as directory:
        temporary = Path(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, dataset):
            run_checks(web, api, dataset, temporary, output)
    print("PASS: temporary datasets/profile removed and both owned servers stopped", flush=True)


if __name__ == "__main__":
    main()
