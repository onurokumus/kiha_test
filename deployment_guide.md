# PTT — Linux Deployment Guide (systemd + nginx, Python 3.11)

This guide targets the current production host and layout:

- host: `heliweb1`
- application URL: `http://heliweb1/ptt/`
- checkout: `/progs2/ptt`
- service account and checkout owner: `ptt:ptt`
- backend service: `ptt-backend.service`

The interactive SSH/admin account may be `t21485srv`, but it must not own or
run the application. Use `sudo` from that account for the privileged commands
below and `sudo -u ptt` for checkout-owned commands.

Production runs **one** uvicorn process (via systemd) on `127.0.0.1:8000`,
with nginx serving the built frontend at `http://heliweb1/ptt/` and proxying
`/ptt/api/` to the backend's `/api/` routes. This is same-origin, so browser
CORS is not involved in normal production traffic. Node is only needed at
**build** time.

> **Never scale to multiple workers** (no `gunicorn -w N`, no `uvicorn --workers`).
> All concurrency safety (per-test RW locks, the native-read gate) is in-process;
> a second worker silently breaks it.

## 1. Install

```bash
# prerequisites: python3.11 + venv, git, nginx, Node 18+ (build only)
sudo apt update && sudo apt install -y git nginx python3.11 python3.11-venv
# Node 18+: fine from apt on Ubuntu 24.04; on older distros use nvm/NodeSource.

# On a clean server, create the unprivileged service account if it does not
# already exist. Skip useradd when `id ptt` already succeeds.
sudo useradd -r -M -d /progs2/ptt -s /usr/sbin/nologin ptt
sudo install -d -o ptt -g ptt /progs2/ptt
sudo -u ptt git clone https://github.com/onurokumus/kiha_test.git /progs2/ptt

# backend venv — python3.11 executable explicitly
cd /progs2/ptt/backend
sudo -u ptt python3.11 -m venv .venv
sudo -u ptt .venv/bin/pip install -r requirements.txt

# one-time sanity check
sudo -u ptt .venv/bin/pip install -r requirements-dev.txt
sudo -u ptt .venv/bin/python -m pytest tests

# Build into an immutable release directory, then publish it with one symlink.
cd /progs2/ptt/frontend
sudo -u ptt npm ci
sudo install -d -o ptt -g ptt /progs2/ptt-releases
PTT_RELEASE=$(date -u +%Y%m%dT%H%M%SZ)-$(sudo -u ptt git -C /progs2/ptt rev-parse --short=12 HEAD)
test ! -e /progs2/ptt-releases/$PTT_RELEASE
sudo -u ptt npm run build -- --outDir /progs2/ptt-releases/$PTT_RELEASE --emptyOutDir
test -f /progs2/ptt-releases/$PTT_RELEASE/index.html

# Expose that build under the existing heliweb1 document root. If heliweb1 uses
# another root, substitute it here and in the nginx `root` directive below.
sudo install -d /var/www/heliweb1
sudo ln -s /progs2/ptt-releases/$PTT_RELEASE /var/www/heliweb1/ptt
```

Test data lands in `/progs2/ptt/data/` by default (`KIHA_DATA_DIR` overrides — put it
on a disk with room; a 1 h test is ~2 GB on disk plus the retained raw.csv).

## 2. Backend service — `/etc/systemd/system/ptt-backend.service`

```ini
[Unit]
Description=PTT backend (FastAPI/uvicorn, single process by design)
After=network.target

[Service]
Type=simple
User=ptt
WorkingDirectory=/progs2/ptt/backend
ExecStart=/progs2/ptt/backend/.venv/bin/python run.py
Restart=always
RestartSec=2
Environment=KIHA_HOST=127.0.0.1
Environment=KIHA_PORT=8000
# native-crash tracebacks in the journal (same as run_backend.bat on Windows)
Environment=PYTHONFAULTHANDLER=1
# Optional: complete comma-separated CORS allowlist for direct backend access.
# /ptt is a URL path, not part of an Origin; change these if the host/scheme does.
Environment=KIHA_CORS_ORIGINS=http://heliweb1
# Environment=KIHA_DATA_DIR=/srv/ptt-data
# Environment=KIHA_MAX_UPLOAD_BYTES=21474836480
# Resumable-upload defaults (values are bytes except STALE_AGE_S):
# Environment=KIHA_UPLOAD_CHUNK_BYTES=16777216
# Valid range: 1..67108864 (64 MiB); a file may use at most 100,000 chunks.
# Update nginx client_max_body_size whenever this value changes.
# Existing sessions retain their original size; do not lower nginx's limit
# below that size plus overhead until those sessions finish or are canceled.
# Environment=KIHA_UPLOAD_MULTIPART_OVERHEAD_BYTES=1048576
# Environment=KIHA_UPLOAD_STALE_AGE_S=604800
# Environment=KIHA_UPLOAD_DISK_RESERVE_BYTES=1073741824

[Install]
WantedBy=multi-user.target
```

`WorkingDirectory` must be `backend/` (run.py imports `app.main:app` relative to it).
`Restart=always` replaces `run_backend.bat`'s restart loop.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ptt-backend
curl http://127.0.0.1:8000/api/health        # -> {"ok":true}
sudo journalctl -u ptt-backend -f            # logs (kiha.* + uvicorn)
```

## 3. nginx — existing `heliweb1` server

Add these three `location` blocks inside the existing `server` block whose
`server_name` is `heliweb1`. Do not create a second `server_name heliweb1` block;
that server may already host other tools at other paths.

```nginx
# Canonical trailing slash: Vite's production base is /ptt/.
location = /ptt {
    return 308 /ptt/;
}

# The trailing /api/ on proxy_pass rewrites:
# /ptt/api/tests -> http://127.0.0.1:8000/api/tests
location ^~ /ptt/api/ {
    proxy_pass http://127.0.0.1:8000/api/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;

    # --- load-bearing: resumable CSV uploads + large exports ---
    # Each request carries one 16 MiB multipart chunk, not the whole CSV.
    # Keep this >= KIHA_UPLOAD_CHUNK_BYTES +
    # KIHA_UPLOAD_MULTIPART_OVERHEAD_BYTES (17 MiB by default).
    client_max_body_size 17m;

    # Optional. Controlled tests on this deployment measured the same upload
    # throughput with nginx request buffering on and off. Keeping it off avoids
    # an additional nginx disk spool for each chunk, but protocol correctness,
    # progress, retry, and resume do not depend on it.
    proxy_request_buffering off;

    proxy_buffering off;            # stream CSV exports back without disk spooling
    # These apply per chunk request. 1 h is deliberately conservative for
    # unusually slow links and for large response exports.
    proxy_read_timeout 1h;
    proxy_send_timeout 1h;
}

# Vite builds asset references with the /ptt/ prefix. The fallback keeps
# direct browser loads under that prefix working if client routes are added.
location ^~ /ptt/ {
    root /var/www/heliweb1;
    try_files $uri $uri/ /ptt/index.html;
}
```

```bash
sudo nginx -t
sudo nginx -s reload
# heliweb1 has no nginx systemd unit. On a different installation that does,
# `sudo systemctl reload nginx` is an equivalent reload command.
# firewall, if enabled:
sudo ufw allow 80/tcp

# Both should succeed through nginx:
curl --fail http://heliweb1/ptt/
curl --fail http://heliweb1/ptt/api/health
```

Open `http://heliweb1/ptt/` — drag a CSV in; the Uploads tab should show
verified progress and the status chain receiving → ingesting → ready.

### Upload protocol and recovery

The production UI uses these API routes:

- `POST /api/uploads` reserves the test name and creates a durable session.
- `GET /api/uploads/{upload_id}?name=...` returns verified chunks and progress.
- `PUT /api/uploads/{upload_id}/chunks/{index}?name=...` uploads one multipart
  field named `file`; `X-Chunk-SHA256` carries its SHA-256 digest.
- `POST /api/uploads/{upload_id}/complete?name=...` atomically completes
  `raw.csv` and schedules ingestion.
- `DELETE /api/uploads/{upload_id}?name=...` removes that matching `receiving`
  or failed session and releases its test name; `ingesting` and `ready` tests
  are protected.

The default chunk size is 16 MiB and the frontend keeps three requests in
flight. A valid `receiving` session survives a backend restart. Its manifest
and verified ranges live below `data/tests/<name>/.upload/`; completion makes
`raw.csv` visible atomically, so ingestion never sees a partial CSV. Sessions
with no activity for seven days are stale by default and may be cleaned during
recovery. `KIHA_UPLOAD_STALE_AGE_S` changes that retention period.

Refreshing a browser cannot preserve its permission to read the local file.
The session metadata remains in local storage and on the server; select the
same file again. PTT hashes the local chunks that the server already has before
skipping them, then uploads only the missing chunks. Use Cancel if the original
file is no longer available.

`KIHA_MAX_UPLOAD_BYTES` caps the complete CSV. The per-request parser also caps
one multipart request at `KIHA_UPLOAD_CHUNK_BYTES` plus
`KIHA_UPLOAD_MULTIPART_OVERHEAD_BYTES`. `KIHA_UPLOAD_DISK_RESERVE_BYTES` keeps
the configured amount free when accepting a new session; an actual disk-full
write is still reported without treating an unverified chunk as committed.
Both server and browser reject a file/configuration combination that would
require more than 100,000 chunks; increase `KIHA_UPLOAD_CHUNK_BYTES` if needed.

The SHA-256 checks protect resumability and detect accidental corruption or a
client/server mismatch; they are not authentication. On plain HTTP, an active
network attacker could replace both a chunk and its checksum, and CORS is a
browser policy rather than access control. Use HTTPS plus appropriate
authentication and authorization if the network or its users are not trusted.

## 4. Deploying this resumable-upload update

The first work-network trial should use the published feature branch. Confirm
that the checkout is clean before switching branches; if `git status --short`
prints anything, stop and preserve those server-side changes first. The
`ls-remote` check prevents starting a deployment before the branch exists on
GitHub.

```bash
set -euo pipefail
cd /progs2/ptt
if [ -n "$(sudo -u ptt git status --porcelain)" ]
then
    echo "STOP: /progs2/ptt has local changes"
    exit 1
fi
sudo -u ptt git ls-remote --exit-code --heads origin feature/resumable-multipart-upload
sudo -u ptt git fetch origin
sudo -u ptt git switch feature/resumable-multipart-upload
sudo -u ptt git pull --ff-only origin feature/resumable-multipart-upload
sudo -u ptt git rev-parse HEAD
```

Install dependencies and build the new frontend into a versioned staging
directory. This does not change the live frontend yet. Keeping release
directories outside the checkout also keeps `git status` clean.

```bash
set -euo pipefail
cd /progs2/ptt
sudo -u ptt backend/.venv/bin/pip install -r backend/requirements.txt
sudo install -d -o ptt -g ptt /progs2/ptt-releases
PTT_RELEASE=$(date -u +%Y%m%dT%H%M%SZ)-$(sudo -u ptt git -C /progs2/ptt rev-parse --short=12 HEAD)
test ! -e /progs2/ptt-releases/$PTT_RELEASE
cd /progs2/ptt/frontend
sudo -u ptt npm ci
sudo -u ptt npm run build -- --outDir /progs2/ptt-releases/$PTT_RELEASE --emptyOutDir
test -f /progs2/ptt-releases/$PTT_RELEASE/index.html
printf '%s\n' "$PTT_RELEASE" | sudo tee /run/ptt-candidate-release >/dev/null
```

If the nginx location has not yet been updated to the configuration in section
3, validate and load it now:

```bash
set -euo pipefail
sudo nginx -t
sudo nginx -s reload
```

Schedule a short maintenance window and have every user close all existing PTT
tabs. Before restarting, confirm in the current UI that no test is `receiving`
or `ingesting`; allow ingestion to finish and allow an in-flight legacy upload
to finish or fail cleanly. Closing a tab alone does not stop server-side
ingestion. An old tab still calls the removed raw-body endpoint. Activate and
verify the new backend first, then atomically move the public symlink to the
already built frontend release:

```bash
set -euo pipefail
PTT_RELEASE=$(sudo cat /run/ptt-candidate-release)
test -f /progs2/ptt-releases/$PTT_RELEASE/index.html
sudo systemctl restart ptt-backend
sudo systemctl status ptt-backend --no-pager
curl --fail http://127.0.0.1:8000/api/health
curl --fail --silent --show-error http://127.0.0.1:8000/api/analysis-sources >/dev/null
curl --fail --silent --show-error http://127.0.0.1:8000/api/trash >/dev/null
curl --fail --silent http://127.0.0.1:8000/openapi.json | grep -q '"/api/uploads"'
sudo ln -sfn /progs2/ptt-releases/$PTT_RELEASE /var/www/heliweb1/ptt.next
sudo mv -Tf /var/www/heliweb1/ptt.next /var/www/heliweb1/ptt
curl --fail http://heliweb1/ptt/
curl --fail http://heliweb1/ptt/api/health
sudo rm -f /run/ptt-candidate-release
```

The symlink rename is the frontend cutover; nginx does not need a reload for
it. Keep the previous release directory until the work-network test passes.
Users must open a fresh tab or hard-refresh after the cutover.

### Post-deployment upload check

Use the browser for this check because the production upload is now a
multi-request protocol. The previous one-command curl probe against
`/api/tests/upload` exercises a removed endpoint and is not a valid speed test.

Create three copies of a representative CSV with distinct filenames/test names,
or delete each completed test before starting the next one. Reusing an existing
ready test name is correctly rejected as a conflict.

1. Open a new incognito window at `http://heliweb1/ptt/`.
2. Open DevTools → Network, enable **Preserve log**, and filter for `/uploads`.
3. Upload the first CSV normally; record its size and elapsed time.
4. Upload the second CSV, click Pause, wait a few seconds, and click Resume.
5. Upload the third CSV, click Pause, and reload the page. In its upload row,
   click **Select original CSV** and choose that exact file. Resume starts
   automatically.
6. In the preserved Network log, confirm the resume status GET is followed by
   PUT requests only for chunk indexes that were missing; verified indexes
   must not be sent again.
7. Confirm all three final states reach `ready` and each test opens normally.
8. Check the backend journal for errors:

```bash
sudo journalctl -u ptt-backend -n 200 --no-pager
```

The home-PC tests prove protocol correctness, recovery, and file integrity.
Only this test on the work PC, server, and network establishes production
throughput.

### Routine updates after merge

After the feature is merged to `main`, follow the same pattern: build a new
release without exposing it, open a short maintenance window, activate and
verify the backend, then atomically publish the frontend.

```bash
set -euo pipefail
cd /progs2/ptt
if [ -n "$(sudo -u ptt git status --porcelain)" ]
then
    echo "STOP: /progs2/ptt has local changes"
    exit 1
fi
sudo -u ptt git switch main
sudo -u ptt git pull --ff-only origin main
sudo -u ptt backend/.venv/bin/pip install -r backend/requirements.txt
sudo install -d -o ptt -g ptt /progs2/ptt-releases
PTT_RELEASE=$(date -u +%Y%m%dT%H%M%SZ)-$(sudo -u ptt git -C /progs2/ptt rev-parse --short=12 HEAD)
test ! -e /progs2/ptt-releases/$PTT_RELEASE
cd /progs2/ptt/frontend
sudo -u ptt npm ci
sudo -u ptt npm run build -- --outDir /progs2/ptt-releases/$PTT_RELEASE --emptyOutDir
test -f /progs2/ptt-releases/$PTT_RELEASE/index.html
printf '%s\n' "$PTT_RELEASE" | sudo tee /run/ptt-candidate-release >/dev/null
```

After users close their PTT tabs:

```bash
set -euo pipefail
PTT_RELEASE=$(sudo cat /run/ptt-candidate-release)
test -f /progs2/ptt-releases/$PTT_RELEASE/index.html
sudo systemctl restart ptt-backend
curl --fail http://127.0.0.1:8000/api/health
curl --fail --silent --show-error http://127.0.0.1:8000/api/analysis-sources >/dev/null
curl --fail --silent --show-error http://127.0.0.1:8000/api/trash >/dev/null
curl --fail --silent http://127.0.0.1:8000/openapi.json | grep -q '"/api/uploads"'
sudo ln -sfn /progs2/ptt-releases/$PTT_RELEASE /var/www/heliweb1/ptt.next
sudo mv -Tf /var/www/heliweb1/ptt.next /var/www/heliweb1/ptt
curl --fail http://heliweb1/ptt/api/health
sudo rm -f /run/ptt-candidate-release
```

Reload nginx with `sudo nginx -t` followed by `sudo nginx -s reload` only when
its configuration changed.

### Rolling back to the pre-resumable release

Completed tests remain rollback-compatible because their final layout is still
`raw.csv` plus the existing Parquet/pyramid files. Schedule a maintenance
window, have all users close PTT, and then prepare the state in this order:

1. Wait for every `ingesting` upload to reach `ready` or `error`.
2. Finish or cancel every `receiving` upload.
3. Cancel every failed upload that still offers a Cancel action.
4. Confirm no `receiving` or `ingesting` row remains.
5. Confirm `git status --short` is empty.

The old backend does not understand `.upload/` manifests and will mark
unresolved partial sessions as interrupted. Once the preconditions above are
met, stop the backend first. This prevents any new upload mutation while the
checkout and frontend are changed. The API is intentionally unavailable during
this rollback window.

Before this feature is merged, `origin/main` is the rollback target used below.
After it is merged, first publish a normal revert commit (or revert PR) to
`main`, then use the same sequence. Do not use the routine-update activation
block for a pre-resumable rollback: that block intentionally requires the new
`/api/uploads` route. Do not rewrite server history with `git reset --hard`.

```bash
set -euo pipefail
cd /progs2/ptt
if [ -n "$(sudo -u ptt git status --porcelain)" ]
then
    echo "STOP: /progs2/ptt has local changes"
    exit 1
fi
sudo systemctl stop ptt-backend
sudo -u ptt git switch main
sudo -u ptt git pull --ff-only origin main
sudo -u ptt backend/.venv/bin/pip install -r backend/requirements.txt
sudo install -d -o ptt -g ptt /progs2/ptt-releases
PTT_RELEASE=$(date -u +%Y%m%dT%H%M%SZ)-$(sudo -u ptt git -C /progs2/ptt rev-parse --short=12 HEAD)
test ! -e /progs2/ptt-releases/$PTT_RELEASE
cd /progs2/ptt/frontend
sudo -u ptt npm ci
sudo -u ptt npm run build -- --outDir /progs2/ptt-releases/$PTT_RELEASE --emptyOutDir
test -f /progs2/ptt-releases/$PTT_RELEASE/index.html
sudo systemctl start ptt-backend
curl --fail http://127.0.0.1:8000/api/health
if ! curl --fail --silent http://127.0.0.1:8000/openapi.json | grep -q '"/api/tests/upload"'
then
    sudo systemctl stop ptt-backend
    echo "STOP: restored backend does not expose the legacy upload route"
    exit 1
fi
sudo ln -sfn /progs2/ptt-releases/$PTT_RELEASE /var/www/heliweb1/ptt.next
sudo mv -Tf /var/www/heliweb1/ptt.next /var/www/heliweb1/ptt
curl --fail http://heliweb1/ptt/
curl --fail http://heliweb1/ptt/api/health
```

Hard-refresh or close every PTT tab after the rollback build as well.

## 5. Diagnosing analysis-source 500s and an incorrect ready count

If Analyze reports a source-loading error, the header initially shows zero
ready tests, and opening Uploads reveals the existing tests (possibly alongside
a Trash error), inspect the backend runtime and traceback:

```bash
sudo -u ptt /progs2/ptt/backend/.venv/bin/python --version
sudo journalctl -u ptt-backend -n 150 --no-pager
curl --include http://127.0.0.1:8000/api/analysis-sources
curl --include http://heliweb1/ptt/api/analysis-sources
```

The September 2026 source-identity/trash code initially used
`Path.is_junction()`, a Python 3.12 API, despite this deployment's Python 3.11
baseline. A traceback ending with `AttributeError: 'PosixPath' object has no
attribute 'is_junction'` identifies that compatibility bug. The fix uses the
portable `app.paths.is_link_or_junction` check for analysis sources, component
statistics and trash, preserving link protections. The frontend also loads the
ready count independently and distinguishes a source-verification failure from
an unavailable test list. Failed source checks retain saved sessions and pause
analysis until retry succeeds.

Deploy the corrected backend and rebuilt frontend using section 4, restart
`ptt-backend`, then hard-refresh the browser. Verify `/api/analysis-sources` and
`/api/trash` as well as `/api/health`; a health-only probe does not exercise
dataset paths. Existing tests do not need re-uploading or conversion, and this
fix does not require a Python upgrade. If the traceback is different, preserve
it and its request ID for diagnosis rather than deleting test data.

See [verification and runtime limits](docs/LINUX_CATALOG_FIX.md).

## 6. Notes

- **Python 3.11 is fully supported** on Linux: `run.py` targets 3.11 as the baseline
  (`asyncio.Runner`), and all the Windows-only workarounds (SelectorEventLoop, the
  py3.14 polars read gate) are behind `sys.platform` guards — Linux gets the default
  event loop and 4 concurrent native reads.
- `start.sh` / `stop.sh` are for ad-hoc dev runs, not servers: no auto-restart,
  localhost-only vite dev server. systemd + nginx above replace them.
- Run checkout-owned `git`, Python/pip, and npm commands as `ptt`, as
  shown above. Running them as root can leave root-owned caches, bytecode, or
  dependencies behind and later cause `Permission denied` errors.
- Backend binds `127.0.0.1` on purpose — only nginx is exposed. Don't set
  `KIHA_HOST=0.0.0.0` unless you deliberately intend to bypass nginx and serve
  the API separately from the frontend.
- The production frontend base defaults to `/ptt/`. To deploy a build at a
  different path, set `VITE_BASE_PATH=/other-path/` for `npm run build` and
  change both nginx locations to the same prefix. `VITE_API_BASE` can point the
  frontend at a deliberately separate API URL, but then that URL's exact
  scheme/host/port must be included in `KIHA_CORS_ORIGINS`.
- A CORS origin contains only scheme, host, and optional port. For
  `http://heliweb1/ptt/`, the origin is `http://heliweb1`, never
  `http://heliweb1/ptt`.
- Deleted tests move to `data/trash/` and purge ~1 h after the next delete; disk
  usage is roughly 2× the retained tests (raw.csv + parquet + pyramid).
- Known multi-user caveat (possible_bugs2.md §1.1): a very slow client downloading a
  large CSV export holds one of the 4 global read slots for the whole download; a
  handful of simultaneous slow exports can stall plot reads until they finish.
