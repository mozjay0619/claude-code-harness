#!/bin/zsh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${1:-8420}"
URL="http://127.0.0.1:${PORT}"
LOG="/tmp/code_harness_${PORT}.log"

if ! lsof -ti "tcp:${PORT}" >/dev/null 2>&1; then
  cd "$DIR"
  nohup python3 server.py "$PORT" >"$LOG" 2>&1 &
  sleep 1
fi

CHROME_BIN=""
for candidate in \
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary" \
  "/Applications/Chromium.app/Contents/MacOS/Chromium"
do
  if [[ -x "$candidate" ]]; then
    CHROME_BIN="$candidate"
    break
  fi
done

if [[ -z "$CHROME_BIN" ]]; then
  echo "Chrome or Chromium was not found in /Applications."
  exit 1
fi

nohup "$CHROME_BIN" \
  --app="$URL" \
  --start-fullscreen \
  --disable-session-crashed-bubble \
  --no-first-run \
  >/dev/null 2>&1 &
