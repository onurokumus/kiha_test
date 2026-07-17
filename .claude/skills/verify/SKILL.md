---
name: verify
description: Build, launch, and drive PTT (FastAPI backend + Vite/React frontend) to verify changes end-to-end on Windows.
---

# Verifying PTT changes

## Launch

The user usually has the backend running via the auto-restart wrapper
(`backend\run_backend.bat`, parent cmd.exe). Killing just the python on
port 8000 makes the wrapper respawn it — to take the port, kill the
wrapper cmd.exe FIRST, then the python + its venv-launcher parent.
Restore state afterwards: relaunch the bat minimized
(`Start-Process cmd.exe '/c','...\run_backend.bat' -WindowStyle Minimized`).

- Backend with captured logs (cwd must be `backend/`):
  `Start-Process .\.venv\Scripts\python.exe run.py -WindowStyle Hidden
   -RedirectStandardError <log>` — app logs (kiha.api / kiha.ingest) go
  to stderr. Health: `GET http://127.0.0.1:8000/api/health`.
- Frontend dev server: from `frontend/`, `npm run dev -- --open false`
  (without `--open false` it pops a browser on the user's desktop).
  Port 3000, '/api' proxied to :8000.

## Drive (headless browser)

Global playwright + Edge works: `$env:NODE_PATH = (npm root -g)`, then
`require('playwright')` and `chromium.launch({ channel: 'msedge' })`.

- Upload via button: `page.setInputFiles('input[type=file]', csvPath)`
  (the input is hidden; setInputFiles doesn't care).
- Upload via drag-drop: dispatch DragEvent with a DataTransfer built in
  page context — on a node INSIDE `#root` (e.g. `#root > div`), never on
  `document.body`: body is the React root's PARENT, so React's listeners
  never see events dispatched there.
- Watch status transitions via `page.request.get('/api/tests')` polling
  (receiving -> ingesting -> ready).
- Test CSVs: `other_small_project/dummy_data/demo_60s.csv` (98 MB) or
  generate via `generate_dummy_data.py`; tiny ones can be synthesized
  in-page for drop tests (`time,thrust,rpm` header, 0.1 s steps).

## Slow/abort transfer probes (things localhost speed hides)

Raw `http.client` from the backend venv: `putrequest` +
`sock.sendall` in chunks with sleeps to hold a transfer open (observe
'receiving' in /api/tests mid-flight); `sock.shutdown` mid-body to test
disconnect cleanup (test dir must vanish; retry must not 409).

## Cleanup

`DELETE /api/tests/<name>` (soft, goes to data/trash) for every test the
run created; leave the user's demo_* / perf_1h tests alone.
