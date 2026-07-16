#!/usr/bin/env sh
# stop whatever listens on the app ports (needs psmisc for fuser)
fuser -k 8000/tcp 2>/dev/null
fuser -k 3000/tcp 2>/dev/null
echo "stopped."
