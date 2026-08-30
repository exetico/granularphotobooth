# GranularPhotoBooth

A lightweight, open-source, **component-based photo booth** web application.  
Every function is a plug-and-play module — swap cameras, storage backends, and
layout templates without touching code.

---

## Quick Start

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Run the application (uses your webcam by default)
python run.py

# 3. Open in a browser
open http://localhost:8080
```

Press **Space** (keyboard) or click **Take Photo** in the UI to start the sequence.

---

## Architecture

```
run.py
 └─ core/broker.py          ← Async state machine (the glue)
     ├─ cameras/            ← Webcam · DSLR (gphoto2) · IP cam
     ├─ inputs/             ← Keyboard · WebSocket button
     ├─ compositor/         ← Passthrough (single shot) · Grid layout (Pillow)
     ├─ storage/            ← Local disk · S3/MinIO · FTP/NAS
     └─ web/server.py       ← aiohttp HTTP + WebSocket server
            └─ web/static/  ← Vanilla HTML5/JS touch-first UI
```

State flow: **IDLE → COUNTDOWN → CAPTURE → PREVIEW → PROCESS → IDLE**

All state transitions are broadcast in real-time over WebSocket so the browser
UI stays in sync without polling.

---

## Configuration

Everything is controlled by `config.json`.  Key toggles:

| Setting | Effect |
|---|---|
| `camera.backend` | `"webcam"` / `"dslr_gphoto2"` / `"ip_cam"` |
| `sequence.shots` | 1–10 photos per session |
| `compositor.enabled` | `true` → multi-shot grid; `false` → passthrough |
| `storage.targets` | `["local_disk"]`, `["local_disk","s3_minio"]`, etc. |

---

## Running Tests

```bash
pytest
```

S3/MinIO integration tests require a local MinIO instance:

```bash
docker run -p 9000:9000 -e MINIO_ROOT_USER=minioadmin \
           -e MINIO_ROOT_PASSWORD=minioadmin \
           quay.io/minio/minio server /data

MINIO_ENDPOINT=http://localhost:9000 pytest tests/test_storage_s3.py
```

Webcam tests skip automatically in headless CI when no camera device is found.

---

## Kiosk Deployment

For production event use — systemd service, Chromium autostart, hiding the
mouse cursor, SIGHUP hot-reload, and more — see **[`docs/kiosk.md`](docs/kiosk.md)**.

---

## Adding a Component

1. Create `cameras/my_cam.py` with a class named `CameraBackend` that extends
   `cameras.base.CameraBackend`.
2. Add `"my_cam": "cameras.my_cam"` to the `backends` dict inside
   `Broker._load_camera()` in `core/broker.py`.
3. Set `"backend": "my_cam"` in `config.json`.

