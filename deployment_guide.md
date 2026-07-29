# PTT — Linux Deployment Guide (systemd + nginx, Python 3.11)

Production layout: **one** uvicorn process (via systemd) on `127.0.0.1:8000`,
with nginx serving the built frontend at `http://heliweb/ptt/` and proxying
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

sudo useradd -r -m -d /opt/ptt -s /usr/sbin/nologin ptt
sudo git clone https://github.com/onurokumus/kiha_test.git /opt/ptt
sudo chown -R ptt:ptt /opt/ptt

# backend venv — python3.11 executable explicitly
cd /opt/ptt/backend
sudo -u ptt python3.11 -m venv .venv
sudo -u ptt .venv/bin/pip install -r requirements.txt

# one-time sanity check
sudo -u ptt .venv/bin/pip install -r requirements-dev.txt
sudo -u ptt .venv/bin/python -m pytest tests

# frontend build -> frontend/dist  (or build elsewhere and copy dist/ over)
cd /opt/ptt/frontend
sudo -u ptt npm ci
sudo -u ptt npm run build

# Expose that build under the existing heliweb document root. If heliweb uses
# another root, substitute it here and in the nginx `root` directive below.
sudo install -d /var/www/heliweb
sudo ln -s /opt/ptt/frontend/dist /var/www/heliweb/ptt
```

Test data lands in `/opt/ptt/data/` by default (`KIHA_DATA_DIR` overrides — put it
on a disk with room; a 1 h test is ~2 GB on disk plus the retained raw.csv).

## 2. Backend service — `/etc/systemd/system/ptt-backend.service`

```ini
[Unit]
Description=PTT backend (FastAPI/uvicorn, single process by design)
After=network.target

[Service]
Type=simple
User=ptt
WorkingDirectory=/opt/ptt/backend
ExecStart=/opt/ptt/backend/.venv/bin/python run.py
Restart=always
RestartSec=2
Environment=KIHA_HOST=127.0.0.1
Environment=KIHA_PORT=8000
# native-crash tracebacks in the journal (same as run_backend.bat on Windows)
Environment=PYTHONFAULTHANDLER=1
# Optional: complete comma-separated CORS allowlist for direct backend access.
# /ptt is a URL path, not part of an Origin; change these if the host/scheme does.
Environment=KIHA_CORS_ORIGINS=http://heliweb,https://heliweb
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
journalctl -u ptt-backend -f                 # logs (kiha.* + uvicorn)
```

## 3. nginx — existing `heliweb` server

Add these three `location` blocks inside the existing `server` block whose
`server_name` is `heliweb`. Do not create a second `server_name heliweb` block;
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
    root /var/www/heliweb;
    try_files $uri $uri/ /ptt/index.html;
}
```

```bash
sudo nginx -t
sudo systemctl reload nginx
# If this nginx installation has no systemd unit, use:
# sudo nginx -s reload
# firewall, if enabled:
sudo ufw allow 80/tcp

# Both should succeed through nginx:
curl --fail http://heliweb/ptt/
curl --fail http://heliweb/ptt/api/health
```

Open `http://heliweb/ptt/` — drag a CSV in; the Uploads tab should show
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

## 4. Updating

```bash
cd /opt/ptt
sudo -u ptt git pull
sudo -u ptt backend/.venv/bin/pip install -r backend/requirements.txt   # if changed
cd frontend && sudo -u ptt npm ci && sudo -u ptt npm run build && cd ..
sudo systemctl restart ptt-backend        # nginx reload only if its config changed
```

Deploy backend changes before the matching frontend build. The former
single-request raw-body upload route is not a compatibility fallback, so an
already-open browser tab from the old build must be refreshed after this
upgrade.

Completed tests remain rollback-compatible because their final layout is still
`raw.csv` plus the existing Parquet/pyramid files. Before rolling back to a
pre-resumable backend, finish or cancel every `receiving` upload and wait for
every `ingesting` upload to reach `ready` or `error`. The old backend does not
understand `.upload/` manifests and will mark unresolved partial sessions as
interrupted. Then deploy the chosen earlier Git commit normally.

## 5. Notes

- **Python 3.11 is fully supported** on Linux: `run.py` targets 3.11 as the baseline
  (`asyncio.Runner`), and all the Windows-only workarounds (SelectorEventLoop, the
  py3.14 polars read gate) are behind `sys.platform` guards — Linux gets the default
  event loop and 4 concurrent native reads.
- `start.sh` / `stop.sh` are for ad-hoc dev runs, not servers: no auto-restart,
  localhost-only vite dev server. systemd + nginx above replace them.
- Backend binds `127.0.0.1` on purpose — only nginx is exposed. Don't set
  `KIHA_HOST=0.0.0.0` unless you deliberately intend to bypass nginx and serve
  the API separately from the frontend.
- The production frontend base defaults to `/ptt/`. To deploy a build at a
  different path, set `VITE_BASE_PATH=/other-path/` for `npm run build` and
  change both nginx locations to the same prefix. `VITE_API_BASE` can point the
  frontend at a deliberately separate API URL, but then that URL's exact
  scheme/host/port must be included in `KIHA_CORS_ORIGINS`.
- A CORS origin contains only scheme, host, and optional port. For
  `http://heliweb/ptt/`, the origin is `http://heliweb`, never
  `http://heliweb/ptt`.
- Deleted tests move to `data/trash/` and purge ~1 h after the next delete; disk
  usage is roughly 2× the retained tests (raw.csv + parquet + pyramid).
- Known multi-user caveat (possible_bugs2.md §1.1): a very slow client downloading a
  large CSV export holds one of the 4 global read slots for the whole download; a
  handful of simultaneous slow exports can stall plot reads until they finish.
