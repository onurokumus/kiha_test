"""Phase 7a browser verification with isolated data, servers and Chromium profile.

Run using global Python/Playwright; native work uses backend/.venv Python 3.13.
Reuses the repository server helper for per-process environments, hidden Windows
children and exact child cleanup (the generic shell helper cannot provide these).
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile

from playwright.sync_api import expect, sync_playwright
from verify_data_quality import ROOT, servers, upload
from verify_browser_zoom import capture_browser_view, set_browser_zoom


def source_hashes(dataset):
    return {str(p.relative_to(dataset)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in dataset.rglob("*") if p.is_file() and
            (p.suffix == ".parquet" or p.name in {"raw.csv", "testpoints.json", "manifest.json"})}


def run_checks(web, api, dataset, temporary, output):
    extension = temporary / "zoom-extension"
    extension.mkdir()
    (extension / "manifest.json").write_text(json.dumps({
        "manifest_version": 3, "name": "Isolated test notes zoom checks", "version": "1.0",
        "permissions": ["tabs"], "background": {"service_worker": "background.js"}}))
    (extension / "background.js").write_text("chrome.runtime.onInstalled.addListener(() => {});")
    csv = "time,signal\n" + "".join(f"{i / 10},{i}\n" for i in range(80))
    local_csv = temporary / "temperature.csv"
    local_csv.write_text(csv, encoding="utf-8")
    description = "Temperature test 🌡️ — İzmir / 測定\nSustained load <b>literal</b>"
    findings = "Temperature rose near the end.\n\tInspect the mount 🔧\n<script>literal text</script>"
    with sync_playwright() as playwright:
        request = playwright.request.new_context(base_url=api + "/")
        assert upload(request, "legacy", csv)["status"] == "ready"
        custom = {"description": "Legacy descriptor", "notes": "Legacy findings", "motor": "M1", "__proto__": "keep"}
        assert request.patch("tests/legacy/meta", data={"user_meta": custom}).ok
        assert upload(request, "unrelated", csv)["status"] == "ready"
        unrelated_meta = (dataset / "tests/unrelated/meta.json").read_bytes()
        context = playwright.chromium.launch_persistent_context(
            str(temporary / "browser-profile"), channel="chromium", headless=True,
            no_viewport=True, args=[f"--disable-extensions-except={extension}",
                                   f"--load-extension={extension}", "--window-size=1440,1000"])
        page = context.pages[0]
        page_errors, console_errors, writes = [], [], []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("request", lambda req: writes.append((req.method, req.url, req.post_data))
                if req.url.startswith(api) and req.method in {"PATCH", "POST", "PUT", "DELETE"} and "/chunks/" not in req.url else None)
        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
            page.goto(web)
            page.wait_for_load_state("networkidle")
            print("Rendered navigation:", page.get_by_role("button").all_text_contents()[:15], flush=True)
            page.get_by_role("button", name="Uploads", exact=True).click()

            def stage(files):
                with page.expect_file_chooser() as chooser:
                    page.get_by_role("button", name=re.compile("Import test data")).click()
                chooser.value.set_files(files)
                page.get_by_label("Uploaded by", exact=True).fill("Notes browser check")

            stage(str(local_csv))
            setup = page.get_by_role("region", name="Import setup")
            field = setup.get_by_label("Description (optional)")
            field.fill("x" * 1001)
            expect(setup.get_by_role("button", name="Upload 1 file", exact=True)).to_be_disabled()
            expect(setup.get_by_role("alert")).to_contain_text("1,000")
            field.fill(description)
            # Hold completion only: real chunks are durably committed before Pause.
            held = []
            page.route(api + "/uploads/*/complete?*", lambda route: held.append(route))
            setup.get_by_role("button", name="Upload 1 file", exact=True).click()
            page.wait_for_function("JSON.parse(localStorage.getItem('ptt.uploadSessions.v1') || '[]').length === 1")
            expect(page.get_by_role("button", name="Pause", exact=True).last).to_be_visible()
            # A response callback runs while locator assertions poll the UI.
            page.wait_for_function("document.body.textContent.includes('finalizing')")
            assert held, "Completion must have been held after real chunk upload"
            page.get_by_role("button", name="Pause", exact=True).last.click()
            records = page.evaluate("JSON.parse(localStorage.getItem('ptt.uploadSessions.v1'))")
            assert records[0]["description"] == description
            session_id = records[0]["uploadId"]
            state = request.get(f"uploads/{session_id}?name=temperature").json()
            assert state["received_chunks"] == state["total_chunks"] > 0
            for route in held:
                route.abort()
            page.unroute(api + "/uploads/*/complete?*")
            page.reload()
            page.wait_for_load_state("networkidle")
            page.get_by_role("button", name="Uploads", exact=True).click()
            with page.expect_file_chooser() as chooser:
                page.get_by_role("button", name="Select original CSV to resume temperature").click()
            chooser.value.set_files(str(local_csv))
            expect(page.get_by_role("button", name="Edit notes for temperature")).to_be_visible(timeout=20000)
            assert request.get("tests/temperature").json()["description"] == description
            print("PASS: UI upload validation, committed chunks, Pause/reload/reselect/resume description", flush=True)

            # New batches start blank, support one shared description, and do not retain old notes.
            batch_files = [{"name": f"batch_{i}.csv", "mimeType": "text/csv", "buffer": csv.encode()} for i in (1, 2)]
            stage(batch_files)
            expect(page.get_by_label("Description (optional)")).to_have_value("")
            page.get_by_label("Description (optional)").fill("Batch temperature tests")
            page.get_by_role("button", name="Upload 2 files", exact=True).click()
            expect(page.get_by_role("button", name="Edit notes for batch_2")).to_be_visible(timeout=20000)
            for name in ("batch_1", "batch_2"):
                assert request.get(f"tests/{name}").json()["description"] == "Batch temperature tests"

            # Recover a receiving upload after losing all browser resume records.
            # The original description must come from the server, not blank setup.
            server_csv = temporary / "server_notes.csv"
            server_csv.write_text(csv, encoding="utf-8")
            stage(str(server_csv))
            page.get_by_label("Description (optional)").fill("Server-held description")
            held = []
            page.route(api + "/uploads/*/complete?*", lambda route: held.append(route))
            page.get_by_role("button", name="Upload 1 file", exact=True).click()
            page.wait_for_function("document.body.textContent.includes('finalizing')")
            assert held
            page.get_by_role("button", name="Pause", exact=True).last.click()
            for route in held:
                route.abort()
            page.unroute(api + "/uploads/*/complete?*")
            page.evaluate("localStorage.removeItem('ptt.uploadSessions.v1')")
            page.reload()
            page.wait_for_load_state("networkidle")
            page.get_by_role("button", name="Uploads", exact=True).click()
            with page.expect_file_chooser() as chooser:
                page.get_by_role("button", name="Select original CSV to resume server_notes").click()
            chooser.value.set_files(str(server_csv))
            expect(page.get_by_role("button", name="Edit notes for server_notes")).to_be_visible(timeout=20000)
            assert request.get("tests/server_notes").json()["description"] == "Server-held description"
            print("PASS: batch descriptions and server-only resume after browser records are cleared", flush=True)
            before_sources = source_hashes(dataset)

            edit_button = page.get_by_role("button", name="Edit notes for temperature")
            edit_button.focus()
            page.keyboard.press("Enter")
            expect(page.get_by_role("textbox", name="Description", exact=True)).to_have_value(description)
            expect(page.get_by_role("textbox", name="Findings / notes", exact=True)).to_have_value("")
            page.get_by_role("textbox", name="Findings / notes", exact=True).fill(findings)
            page.get_by_role("button", name="Uploads", exact=True).click()
            dialog = page.get_by_role("alertdialog")
            expect(dialog).to_contain_text("Discard unsaved")
            page.keyboard.press("Escape")
            expect(page.get_by_role("textbox", name="Findings / notes", exact=True)).to_have_value(findings)

            # Failed saves retain the draft and allow retry through the real API.
            def fail_save(route):
                if route.request.method == "PATCH":
                    route.fulfill(status=503, content_type="application/json", body='{"detail":"Simulated note save failure"}')
                else:
                    route.continue_()
            page.route(api + "/tests/temperature/meta", fail_save)
            save = page.get_by_role("button", name="Save notes and metadata", exact=True)
            save.click()
            expect(page.get_by_role("status").filter(has_text="Simulated note save failure")).to_be_visible()
            expect(page.get_by_role("textbox", name="Findings / notes", exact=True)).to_have_value(findings)
            assert request.get("tests/temperature").json().get("notes", "") == ""
            page.unroute(api + "/tests/temperature/meta", fail_save)
            save.focus()
            page.keyboard.press("Enter")
            expect(save).to_be_disabled()
            assert request.get("tests/temperature").json()["notes"] == findings
            page.reload()
            page.wait_for_load_state("networkidle")
            page.get_by_role("button", name="Uploads", exact=True).click()
            page.get_by_role("button", name="Edit notes for temperature").click()
            expect(page.get_by_role("textbox", name="Findings / notes", exact=True)).to_have_value(findings)
            print("PASS: keyboard entry/save, unsaved guard, failed save/retry and reload", flush=True)

            # Clear/re-edit and limits in the UI (no silent truncation).
            page.get_by_role("textbox", name="Findings / notes", exact=True).fill("x" * 20001)
            expect(save).to_be_disabled()
            expect(page.get_by_role("alert").filter(has_text="20,000")).to_be_visible()
            page.get_by_role("button", name="reset drafts", exact=True).click()
            expect(page.get_by_role("textbox", name="Findings / notes", exact=True)).to_have_value(findings)
            page.get_by_role("textbox", name="Description", exact=True).fill("")
            page.get_by_role("textbox", name="Findings / notes", exact=True).fill("")
            save.click()
            expect(save).to_be_disabled()
            cleared = request.get("tests/temperature").json()
            assert cleared["description"] == cleared["notes"] == ""
            page.get_by_role("textbox", name="Description", exact=True).fill("Updated temperature description")
            page.get_by_role("textbox", name="Findings / notes", exact=True).fill(findings)
            save.click()
            expect(save).to_be_disabled()
            page.get_by_role("button", name="Uploads", exact=True).click()
            search = page.get_by_role("searchbox", name="Search upload history")
            search.fill("Updated temperature description")
            expect(page.get_by_role("button", name="Edit notes for temperature")).to_have_text("Updated temperature description")
            expect(page.get_by_role("button", name="Edit notes for legacy")).to_have_count(0)
            search.fill("")
            page.get_by_role("button", name="Edit notes for legacy").click()
            expect(page.get_by_role("textbox", name="Description", exact=True)).to_have_value("")
            expect(page.get_by_role("textbox", name="Findings / notes", exact=True)).to_have_value("")
            expect(page.get_by_placeholder("value", exact=True)).to_have_count(4)
            page.get_by_role("textbox", name="Description", exact=True).fill("Legacy test now described")
            page.get_by_role("textbox", name="Findings / notes", exact=True).fill("New findings")
            save.click()
            expect(save).to_be_disabled()
            assert request.get("tests/legacy").json()["user_meta"] == custom
            # Edit generic metadata too; prototype-like keys must survive the UI serializer.
            page.get_by_placeholder("value", exact=True).nth(2).fill("M2")
            save.click()
            expect(save).to_be_disabled()
            assert request.get("tests/legacy").json()["user_meta"] == {**custom, "motor": "M2"}
            print("PASS: text limits/reset/clear, searchable history and legacy metadata preservation", flush=True)

            cdp = context.new_cdp_session(page)
            window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
            baseline_dpr = page.evaluate("devicePixelRatio")
            for width, factor in ((1440, 1), (1100, 1), (1440, 1.25), (1440, 1.5)):
                cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"width": width, "height": 1000}})
                set_browser_zoom(worker, page, factor)
                page.wait_for_function("dpr => Math.abs(devicePixelRatio - dpr) < .001", arg=baseline_dpr * factor)
                page.get_by_role("textbox", name="Findings / notes", exact=True).scroll_into_view_if_needed()
                for label in ("Description", "Findings / notes"):
                    box = page.get_by_role("textbox", name=label, exact=True)
                    assert box.evaluate("el => {const r=el.getBoundingClientRect();return r.left>=0 && r.right<=innerWidth && el.scrollWidth<=el.clientWidth+1;}")
                save.scroll_into_view_if_needed()
                expect(save).to_be_visible()
                page.get_by_role("textbox", name="Description", exact=True).scroll_into_view_if_needed()
                capture_browser_view(cdp, output / f"edit-{width}-{factor}.png")
                page.get_by_role("button", name="Uploads", exact=True).click()
                button = page.get_by_role("button", name="Edit notes for legacy")
                button.scroll_into_view_if_needed()
                assert button.evaluate("el => {const r=el.getBoundingClientRect();return r.width>50 && r.left>=0 && r.right<=innerWidth;}")
                capture_browser_view(cdp, output / f"history-{width}-{factor}.png")
                button.focus()
                page.keyboard.press("Enter")
                expect(page.get_by_role("textbox", name="Description", exact=True)).to_have_value("Legacy test now described")
            assert source_hashes(dataset) == before_sources, "Notes must not modify source data or upload manifests"
            assert (dataset / "tests/unrelated/meta.json").read_bytes() == unrelated_meta
            allowed_writes = all(
                (url.startswith(api + "/uploads") and method == "POST") or
                (url in {api + "/tests/temperature/meta", api + "/tests/legacy/meta"} and method == "PATCH")
                for method, url, _ in writes)
            assert allowed_writes, writes
            assert not page_errors, page_errors
            unexpected = [error for error in console_errors if "503" not in error and "ERR_FAILED" not in error and "ERR_ABORTED" not in error]
            assert not unexpected, unexpected
            (output / "results.json").write_text(json.dumps({"writes": writes, "page_errors": page_errors,
                "console_errors": console_errors, "unchanged_source_files": len(before_sources),
                "desktop_checks": ["1440px", "1100px", "125%", "150%"]}, indent=2), encoding="utf-8")
            print(f"PASS: desktop resize/125%/150%, {len(before_sources)} source files unchanged; no unexpected writes/errors", flush=True)
        except Exception:
            print("Textarea labels:", page.locator('textarea').evaluate_all("els => els.map(el => ({value:el.value, labels:[...el.labels].map(l=>l.textContent)}))"), flush=True)
            print(page.locator('.edit-view').aria_snapshot()[:3000] if page.locator('.edit-view').count() else "No Edit view", flush=True)
            page.screenshot(path=str(output / "failure.png"), full_page=True)
            raise
        finally:
            context.close()
            request.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-port", type=int, default=3180)
    parser.add_argument("--backend-port", type=int, default=8180)
    args = parser.parse_args()
    output = ROOT / "data/verification/test-notes"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ptt-notes-") as directory:
        temporary = Path(directory)
        with servers(temporary, output, args.frontend_port, args.backend_port) as (web, api, dataset):
            run_checks(web, api, dataset, temporary, output)
    print("PASS: isolated fixtures/profile removed; owned servers stopped", flush=True)


if __name__ == "__main__":
    main()
