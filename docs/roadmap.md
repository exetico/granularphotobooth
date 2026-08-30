# GranularPhotoBooth — Developer Implementation Roadmap

> Phases are ordered so you can run and test the app at every milestone.  
> **Never touch DSLR hardware until Phase 7** — everything before that works with a laptop webcam.

---

## Phase 0 — Skeleton & Tooling ✅

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

## Phase 1 — State Machine Core ✅

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

## Phase 2 — WebSocket Server + Minimal UI ✅

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

## Phase 3 — Webcam Backend ✅  ← First Camera Milestone

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

## Phase 4 — Keyboard Input ✅

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

## Phase 5 — Compositor ✅

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

## Phase 6 — Storage Pipeline ✅

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

## Phase 7 — DSLR Backend ✅  ← Linux / macOS Only

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

## Phase 8 — Hardening & Kiosk Mode ✅

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
