#!/usr/bin/env sh
# start backend + frontend in the background (Linux/macOS)
cd "$(dirname "$0")" || exit 1

if [ ! -x backend/.venv/bin/python ]; then
  echo "backend/.venv not found — run:  cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
if [ ! -d frontend/node_modules ]; then
  echo "frontend/node_modules not found — run:  cd frontend && npm install"
  exit 1
fi

(cd backend && nohup .venv/bin/python run.py >> backend.log 2>&1 &)
(cd frontend && BROWSER=none nohup npm run dev >> vite.log 2>&1 &)
echo "servers starting..."
echo "  backend:  http://127.0.0.1:8000"
echo "  frontend: http://localhost:3000"
