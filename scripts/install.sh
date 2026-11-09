#!/usr/bin/env bash
# GranularPhotoBooth installer for Linux (Debian/Ubuntu/Raspberry Pi OS) and macOS.
#
#   ./scripts/install.sh            # webcam / IP cam
#   ./scripts/install.sh --dslr     # + gphoto2 for DSLR / mirrorless cameras
#
set -euo pipefail
cd "$(dirname "$0")/.."

WITH_DSLR=0
for arg in "$@"; do
  case "$arg" in
    --dslr) WITH_DSLR=1 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
  esac
done

OS="$(uname -s)"
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10+ is required."
  [ "$OS" = "Darwin" ] && echo "Install it with:  brew install python@3.12"
  [ "$OS" = "Linux" ] && echo "Install it with:  sudo apt install python3 python3-venv"
  exit 1
fi
say "Using $($PY --version)"

if [ "$WITH_DSLR" = 1 ]; then
  say "Installing libgphoto2"
  if [ "$OS" = "Darwin" ]; then
    command -v brew >/dev/null || { echo "Homebrew is required: https://brew.sh"; exit 1; }
    brew install libgphoto2 pkg-config
  else
    sudo apt-get update && sudo apt-get install -y libgphoto2-dev pkg-config python3-dev gphoto2
  fi
fi

if [ "$OS" = "Linux" ] && ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then
  say "Installing python3-venv"
  sudo apt-get install -y python3-venv
fi

say "Creating virtual environment in .venv"
"$PY" -m venv .venv
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt
if [ "$WITH_DSLR" = 1 ]; then
  .venv/bin/pip install "gphoto2>=2.5.0"
fi

say "Checking the setup"
.venv/bin/python run.py --doctor || true

cat <<MSG

Done!  Start the booth with:

    ./scripts/start-kiosk.sh        # backend + full-screen browser
    .venv/bin/python run.py         # backend only, then open http://localhost:8080

MSG
