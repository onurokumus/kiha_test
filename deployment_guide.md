# PTT — Linux Deployment Guide (systemd + nginx, Python 3.11)

Production layout: **one** uvicorn process (via systemd) on `127.0.0.1:8000`, nginx
serving the built frontend from `frontend/dist/` and proxying `/api` to the backend.
Same-origin, so no CORS involved. Node is only needed at **build** time.

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

# one-time sanity check (78 tests)
sudo -u ptt .venv/bin/pip install -r requirements-dev.txt
sudo -u ptt .venv/bin/python -m pytest tests

# frontend build -> frontend/dist  (or build elsewhere and copy dist/ over)
cd /opt/ptt/frontend
sudo -u ptt npm ci
sudo -u ptt npm run build
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
# Environment=KIHA_DATA_DIR=/srv/ptt-data
# Environment=KIHA_MAX_UPLOAD_BYTES=21474836480

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

## 3. nginx — `/etc/nginx/sites-available/ptt`

```nginx
server {
    listen 80;
    server_name _;                      # or your hostname

    root /opt/ptt/frontend/dist;
    index index.html;

    location / {
        try_files $uri /index.html;     # SPA fallback
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;

        # --- load-bearing: multi-GB CSV uploads/exports ---
        client_max_body_size 20g;       # nginx default is 1 MB -> every upload 413s
        proxy_request_buffering off;    # stream the body to the backend as it arrives;
                                        # buffering would spool the whole upload first and
                                        # break the live 'receiving' status/progress
        proxy_buffering off;            # stream CSV exports back without disk spooling
        proxy_read_timeout 1h;          # long exports/uploads are legitimate
        proxy_send_timeout 1h;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/ptt /etc/nginx/sites-enabled/ptt
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
# firewall, if enabled:
sudo ufw allow 80/tcp
```

Open `http://<server>/` — drag a CSV in; the Uploads tab should show live progress
and the status chain receiving → ingesting → ready.

## 4. Updating

```bash
cd /opt/ptt
sudo -u ptt git pull
sudo -u ptt backend/.venv/bin/pip install -r backend/requirements.txt   # if changed
cd frontend && sudo -u ptt npm ci && sudo -u ptt npm run build && cd ..
sudo systemctl restart ptt-backend        # nginx reload only if its config changed
```

## 5. Notes

- **Python 3.11 is fully supported** on Linux: `run.py` targets 3.11 as the baseline
  (`asyncio.Runner`), and all the Windows-only workarounds (SelectorEventLoop, the
  py3.14 polars read gate) are behind `sys.platform` guards — Linux gets the default
  event loop and 4 concurrent native reads.
- `start.sh` / `stop.sh` are for ad-hoc dev runs, not servers: no auto-restart,
  localhost-only vite dev server. systemd + nginx above replace them.
- Backend binds `127.0.0.1` on purpose — only nginx is exposed. Don't set
  `KIHA_HOST=0.0.0.0` unless you intend to bypass nginx (you'd lose the upload
  buffering/timeout handling and serve no frontend).
- Deleted tests move to `data/trash/` and purge ~1 h after the next delete; disk
  usage is roughly 2× the retained tests (raw.csv + parquet + pyramid).
- Known multi-user caveat (possible_bugs2.md §1.1): a very slow client downloading a
  large CSV export holds one of the 4 global read slots for the whole download; a
  handful of simultaneous slow exports can stall plot reads until they finish.
