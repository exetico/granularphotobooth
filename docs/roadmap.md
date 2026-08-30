# GranularPhotoBooth — Developer Implementation Roadmap

> Phases are ordered so you can run and test the app at every milestone.  
> **Never touch DSLR hardware until Phase 7** — everything before that works with a laptop webcam.

### Status legend
- `[DONE]` — Implemented, tests pass, verified on a real device
- `[DONE – NEEDS FIELD TEST]` — Implemented and unit-tested, but requires real hardware/service to fully verify
- `[IN PROGRESS]` — Currently being worked on
- `[TODO]` — Not yet started

---

## Phase 0 — Skeleton & Tooling `[DONE]`

**Goal:** `python run.py` prints the loaded config and exits cleanly.

### Deliverables
- Directory structure (`core/`, `cameras/`, `inputs/`, `compositor/`, `storage/`, `web/`)
- `config.json` — master configuration file
- `requirements.txt` — minimal Python dependencies
- `core/config_loader.py` — JSON Schema validation
- `run.py` — entry point that loads config, sets up logging, launches broker + server

### Verify
```bash
python run.py --config config.json
# Expected: "Configuration loaded" log line, no errors
```

---

## Phase 1 — State Machine Core `[DONE]`

**Goal:** State machine cycles through all states internally, broadcasting events.

### Deliverables
- `core/state.py` — `State` enum
- `core/broker.py` — asyncio state machine with `trigger()`, `confirm()`, `retake()`, shutdown

### Tests
```bash
pytest tests/test_broker.py -v
# All 10 tests pass (no camera, no UI needed)
```

### Verify
The broker transitions states independently of any hardware.  Connect a WebSocket
client (e.g. `wscat -c ws://localhost:8080/ws`) and watch state_change events.

---

## Phase 2 — WebSocket Server + Minimal UI `[DONE]`

**Goal:** Open browser, click Take Photo, watch state labels cycle.

### Deliverables
- `web/server.py` — aiohttp server: static files, `/ws`, `/preview`, `/output/`
- `web/static/index.html` — kiosk layout
- `web/static/app.js` — WebSocket client, state-driven UI
- `web/static/style.css` — dark touch-first theme

### Verify
```bash
python run.py
# Open http://localhost:8080
# Click "Take Photo" → status bar cycles IDLE→COUNTDOWN→CAPTURE→PREVIEW→PROCESS→IDLE
```

---

## Phase 3 — Webcam Backend `[DONE]` ← First Camera Milestone

**Goal:** Countdown + live preview + captured image displayed in browser.

### Deliverables
- `cameras/base.py` — Abstract `CameraBackend`
- `cameras/webcam.py` — OpenCV backend; supports int device index **or video file path** (for CI)
- Broker wires: on CAPTURE → `camera.capture()`, base64-encodes, sends in PREVIEW event
- MJPEG endpoint `/preview` streams live countdown view

### Tests
```bash
pytest tests/test_webcam_backend.py -v
# Live device tests skip if no camera; unit guards always run
```

### Verify
Full end-to-end: browser shows MJPEG live view during countdown, captured image
during preview.

### CI Tip
Set `device_index` to a `.mp4` file path — OpenCV treats video files as capture devices:
```json
"webcam": { "device_index": "tests/fixtures/sample_video.mp4" }
```

---

## Phase 4 — Keyboard Input `[DONE – NEEDS FIELD TEST]`

**Goal:** Press Spacebar on the host → capture sequence starts.

### Deliverables
- `inputs/base.py` — Abstract `InputTrigger`
- `inputs/keyboard.py` — pynput global hotkey listener; key from `config.json`
- `inputs/websocket_trigger.py` — browser-button trigger (already wired in Phase 2)

### Tests
```bash
pytest tests/test_input_keyboard.py -v
# Keyboard simulation tests skip if no X11/display; guard tests always run
```

### Config
```json
"input": { "triggers": ["keyboard"], "keyboard": { "trigger_key": "space" } }
```

---

## Phase 5 — Compositor `[DONE]`

**Goal:** 4-shot session produces a composited strip image.

### Deliverables
- `compositor/passthrough.py` — returns last frame unchanged (single shot / disabled)
- `compositor/grid_layout.py` — Pillow: pastes frames onto a PNG template using a JSON slot map
- `assets/layouts/4shot_strip.json` — default 4-slot layout map

### Tests
```bash
pytest tests/test_compositor.py -v
```

### Verify
```json
"sequence": { "shots": 4 },
"compositor": { "enabled": true, "template_map": "assets/layouts/4shot_strip.json" }
```
After a 4-shot session the output image dimensions match `canvas_size` from the JSON map.

### Creating a Custom Layout
1. Prepare a background PNG (e.g. 1200×1800 px)
2. Write a JSON map with `canvas_size` and `slots` (x, y, width, height per slot)
3. Point `compositor.template_image` and `compositor.template_map` at those files

---

## Phase 6 — Storage Pipeline `[DONE]`

**Goal:** Images saved locally (and optionally to S3 / FTP) without freezing the UI.

### Deliverables
- `storage/pipeline.py` — `ThreadPoolExecutor` fan-out; failures in one target don't block others
- `storage/local_disk.py` — saves to `output/` with `{timestamp}_{uuid}` filename
- `storage/s3_minio.py` — boto3; works with AWS S3 **and** local MinIO
- `storage/ftp_nas.py` — standard library `ftplib`

### Tests
```bash
pytest tests/test_storage_local.py -v

# S3/MinIO integration (requires Docker):
docker run -p 9000:9000 -e MINIO_ROOT_USER=minioadmin \
           -e MINIO_ROOT_PASSWORD=minioadmin \
           quay.io/minio/minio server /data
MINIO_ENDPOINT=http://localhost:9000 pytest tests/test_storage_s3.py -v
```

### Config
```json
"storage": {
  "targets": ["local_disk", "s3_minio"],
  "s3_minio": {
    "enabled": true,
    "endpoint_url": "http://localhost:9000",
    "bucket": "photobooth",
    "access_key": "minioadmin",
    "secret_key": "minioadmin"
  }
}
```

---

## Phase 7 — DSLR Backend `[DONE – NEEDS FIELD TEST]` ← Linux / macOS Only

**Goal:** Canon/Nikon DSLR connected via USB shoots full-res images.

### Deliverables
- `cameras/dslr_gphoto2.py` — gphoto2 Python bindings; `is_available()` checks for connected cameras

### Setup (Linux / macOS)
```bash
# Linux
sudo apt install libgphoto2-dev
pip install gphoto2

# macOS (Homebrew)
brew install libgphoto2
pip install gphoto2
```

### Config
```json
"camera": {
  "backend": "dslr_gphoto2",
  "backends": {
    "dslr_gphoto2": { "iso": "400", "aperture": "5.6", "shutter_speed": "1/125" }
  }
}
```

### Verify
```bash
gphoto2 --auto-detect   # should list your camera
python run.py           # full end-to-end with DSLR
```

If the DSLR is not detected, the broker automatically falls back to the webcam
backend and logs a warning — the app still works.

---

## Phase 8 — Hardening & Kiosk Mode `[DONE]`

**Goal:** Production-ready deployment on an event laptop.

### Deliverables
- Error boundary: any exception in CAPTURE/PROCESS → broadcast `error` event → return to IDLE
- Graceful shutdown: `SIGTERM`/`SIGINT` releases camera and closes WebSocket connections
- `is_shutdown_requested()` public method on `Broker` (no internal attribute leakage)

### Kiosk Mode (Linux — Chromium)
```json
"app": { "kiosk_mode": true }
```

Systemd unit example (`/etc/systemd/system/photobooth.service`):
```ini
[Unit]
Description=GranularPhotoBooth
After=network.target

[Service]
WorkingDirectory=/opt/granularphotobooth
ExecStart=/opt/granularphotobooth/.venv/bin/python run.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Chromium autostart (put in `~/.config/autostart/photobooth-browser.desktop`):
```ini
[Desktop Entry]
Type=Application
Exec=chromium-browser --kiosk --app=http://localhost:8080
```

---

## Known Issues & Bug Fixes Log

| Date | Issue | Fix |
|---|---|---|
| 2026-08-30 | JS syntax error: comment and `function applyState` merged onto one line (CodeQL edit artifact) | Split onto separate lines |
| 2026-08-30 | `request.transport` is `None` in MJPEG preview handler before stream established | Use `response.task.done()` instead |
| 2026-08-30 | `sendEvent` inside IIFE keydown handler resolved as undefined | Changed to `window.sendEvent` |
| 2026-08-30 | `cv2` lazy import in `capture()`/`get_preview_frame()` raised `ModuleNotFoundError` before "not initialised" guard | Moved `import cv2` after the guard |
| 2026-08-30 | Compositor stretched photos into slot shape instead of crop-filling | Switched from `resize` to `ImageOps.fit` (aspect-preserving crop-to-fill) |
| 2026-08-30 | Preview state only showed last frame of a multi-shot series | Broker now sends all frames as base64 array; UI renders a scrollable strip |
| 2026-08-30 | Individual raw frames were not saved — only the composite was | Storage pipeline now saves each raw frame with `_shot_N` suffix before saving the composite |

---

## Phase 9 — Config Hot-Reload & Operational Polish `[DONE]`

**Goal:** Change `config.json` while the app is running and have the changes take
effect on the next session — without restarting the process.

### Why this matters
On a live event the operator may want to change the countdown duration, swap the
storage target from local disk to S3, or enable the compositor mid-event.
Restarting the process means briefly losing the kiosk UI.

### Deliverables
- `core/config_loader.py` — `watch()` method using `watchfiles` (or mtime polling
  fallback if not installed) detects `config.json` changes and calls a reload callback.
- `core/broker.py` — `reload_config(new_config)` and `_apply_config(new_config)`:
  1. If IDLE: apply immediately, broadcast `{"event": "config_reloaded"}` to clients
  2. If mid-session: queue in `_pending_config`, apply on return to IDLE
  3. Re-loads camera / compositor / storage components
- `run.py` — wires `SIGHUP` → `loader.load()` → `broker.reload_config()` and
  launches `loader.watch()` as a third asyncio task alongside broker + server.
- `tests/test_config_reload.py` — 6 tests covering callback, invalid JSON,
  schema violations, immediate apply, queuing, and deferred apply.

### Verify
```bash
python run.py
# In another terminal:
# Edit config.json (e.g. change countdown_seconds from 3 to 5)
# Send SIGHUP:  kill -HUP <pid>
# Next session uses the new countdown without restarting
```

### Non-goals
- Do **not** apply config changes mid-session (between trigger and IDLE return)
- Do **not** reload `app.host` / `app.port` — those require a server restart
