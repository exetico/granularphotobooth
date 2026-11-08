#!/usr/bin/env bash
# Start the photo booth backend (if not already running) and open the UI in a
# locked-down, full-screen browser window.  Works on Linux and macOS.
#
#   ./scripts/start-kiosk.sh [--port 8080] [--no-browser]
set -euo pipefail
cd "$(dirname "$0")/.."

PORT=8080
BROWSER=1
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift ;;
    --no-browser) BROWSER=0 ;;
  esac
  shift
done
URL="http://localhost:${PORT}"
PY=".venv/bin/python"; [ -x "$PY" ] || PY="python3"

if ! curl -fs "${URL}/healthz" >/dev/null 2>&1; then
  echo "Starting backend…"
  mkdir -p logs
  nohup "$PY" run.py --port "$PORT" >>logs/stdout.log 2>&1 &
fi

for _ in $(seq 1 60); do
  curl -fs "${URL}/healthz" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fs "${URL}/healthz" >/dev/null || { echo "Backend did not start — see logs/photobooth.log"; exit 1; }
[ "$BROWSER" = 1 ] || exit 0

PROFILE="${HOME}/.cache/granularphotobooth-browser"
mkdir -p "$PROFILE"
# Clear the "Chrome didn't shut down correctly" bubble after a power cut
sed -i.bak 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"Crashed"/"exit_type":"Normal"/' \
  "$PROFILE/Default/Preferences" 2>/dev/null || true

FLAGS=(--kiosk "--app=${URL}" "--user-data-dir=${PROFILE}" --noerrdialogs --disable-infobars
       --disable-session-crashed-bubble --disable-features=TranslateUI,Translate --no-first-run
       --overscroll-history-navigation=0 --disable-pinch --check-for-update-interval=31536000
       --autoplay-policy=no-user-gesture-required --password-store=basic)

if [ "$(uname -s)" = "Darwin" ]; then
  for app in "Google Chrome" "Chromium" "Microsoft Edge" "Brave Browser"; do
    if [ -d "/Applications/${app}.app" ]; then
      exec open -na "/Applications/${app}.app" --args "${FLAGS[@]}"
    fi
  done
  echo "No Chromium-based browser found — opening Safari (use View → Enter Full Screen)"
  exec open "$URL"
else
  for b in chromium chromium-browser google-chrome google-chrome-stable microsoft-edge brave-browser; do
    if command -v "$b" >/dev/null 2>&1; then
      exec "$b" "${FLAGS[@]}"
    fi
  done
  if command -v firefox >/dev/null 2>&1; then exec firefox --kiosk "$URL"; fi
  echo "No browser found. Install Chromium:  sudo apt install chromium"
  exit 1
fi
