# GranularPhotoBooth — Architecture & Design Decisions

> **Status:** Implemented (Phase 0–12b complete)  
> **Last updated:** 2026-11-08

---

## Philosophy

| Principle | Decision |
|---|---|
| Granular & Modular | Every function is an isolated, swappable component behind an abstract base class |
| Hardware Genesis (Linux Focus) | Lightweight Python async stack; works on recycled laptops running Linux |
| Zero-Configuration First Run | Built-in defaults for every setting; `--doctor` explains what is missing; optional webcam fallback |
| No Bloat | No social media, no frameworks; vanilla HTML5/JS frontend, no build step |

---

## System Overview

```
Browser (Touch-First UI)
        │  WebSocket (JSON)
        ▼
web/server.py  ──────────────────────────────────────────────
        │                                                    │
        │  HTTP /output/  HTTP /preview (MJPEG)             │
        │                                                    │
core/broker.py  ←── inputs/ (keyboard, ws_trigger)         │
  State Machine                                             │
  IDLE → COUNTDOWN → CAPTURE → PREVIEW → PROCESS → IDLE    │
        │                                                    │
        ├── cameras/  (webcam · dslr_gphoto2 · ip_cam)      │
        ├── compositor/  (passthrough · grid_layout)        │
        └── storage/pipeline.py                             │
                ├── local_disk                              │
                ├── s3_minio                                │
                └── ftp_nas                                 │
```

---

## State Machine

Every state transition is broadcast to all WebSocket clients as:

```json
{ "event": "state_change", "state": "COUNTDOWN", "data": { "shot": 1, "total": 4 } }
```

| State | Entry condition | Exit condition |
|---|---|---|
| IDLE | App start / sequence done | `trigger` event received |
| COUNTDOWN | Trigger fired | countdown timer reaches zero |
| CAPTURE | Countdown done | Camera returns JPEG bytes |
| PREVIEW | All shots captured | `confirm` / `retake` / timeout |
| PROCESS | Preview confirmed | Compositor + storage pipeline completes |

---

## Component Contracts (Abstract Base Classes)

### CameraBackend (`cameras/base.py`)
```
is_available() → bool       # classmethod: can this backend run here?
initialize()                # open device
capture() → bytes           # return JPEG
get_preview_frame() → bytes # low-res live view JPEG
release()                   # close device
```

### InputTrigger (`inputs/base.py`)
```
start(callback: Callable)   # begin listening; call callback() on trigger
stop()                      # stop listening
```

### BaseCompositor (`compositor/base.py`)
```
compose(frames: list[bytes], config: dict) → bytes
```

### StorageTarget (`storage/base.py`)
```
is_configured() → bool
upload(image_bytes, metadata) → str   # returns URI/path
```

---

## Configuration Architecture

A single `config.json` drives the entire application.  Component selection is
purely declarative — no code changes needed to swap backends.

Key fields:

```json
{
  "camera":    { "backend": "webcam" },         // webcam | dslr_gphoto2 | ip_cam
  "sequence":  { "shots": 4, "countdown_seconds": 3 },
  "compositor":{ "enabled": true, "template_map": "..." },
  "storage":   { "targets": ["local_disk", "s3_minio"] }
}
```

---

## WebSocket Message Protocol

| Direction | Event | Key payload fields |
|---|---|---|
| Server→Client | `state_change` | `state`, `data` |
| Server→Client | `countdown_tick` | `remaining` |
| Server→Client | `storage_complete` | `paths[]` |
| Server→Client | `error` | `message` |
| Client→Server | `trigger` | — |
| Client→Server | `confirm` | — |
| Client→Server | `retake` | — |

---

## Camera Fallback Chain

```
config.json: "backend": "dslr_gphoto2"
       │
       ▼
CameraBackend.is_available()?
       │ No → log warning
       ▼
webcam.CameraBackend.is_available()?
       │ No → log error, no camera loaded
       ▼
camera.initialize()
```

---

## Security Decisions

| Concern | Mitigation |
|---|---|
| XSS via WebSocket `image_data` | Validated with strict `^data:image/jpeg;base64,[A-Za-z0-9+/]+=*$` regex |
| Open redirect via `image_url` | Filename extracted with `split("/").pop()` + `^[\w\-.]+$` allow-list; always prefixed `/output/` |
| Dynamic JS method dispatch | Replaced bracket-notation dispatch with explicit `switch` statement |
| Secrets in config | `access_key` / `secret_key` / `password` fields are empty strings in the committed default |


---

## Stability architecture (0.11.0)

```
Browser ──ws──► web/server.py ──► core/broker.py (state machine, session time limit)
   ▲  hello snapshot on connect       │
   │  /preview (MJPEG) ◄── shared ────┤ cameras/manager.py  ── ONE camera thread
   │  /media/<shot>.jpg  (screen-     │      timeouts · retries · reconnect loop
   │   sized, in-memory cache)        │      └─ backend: webcam | dslr_gphoto2 | ip_cam | dummy
   │                                  ├─ compositor/grid_layout.py (background → photos → overlay)
   │                                  └─ storage/pipeline.py
   │                                         local_disk  — synchronous, atomic, first
   │                                         s3 / ftp    — background with retries
```

* **Flow:** `IDLE → (COUNTDOWN → CAPTURE) × shots → PROCESS → PREVIEW → IDLE`.
  Each original is saved right after CAPTURE.  Retake (in PREVIEW) moves the
  composite to `rejected/` and starts a new session (`retake_mode`).
* **Camera thread:** backends are plain blocking classes.  The manager calls
  them from a single-worker executor.  If a call exceeds its timeout the
  executor is abandoned and replaced, and the camera is reopened.
* **Live view:** one preview loop at `camera.preview_fps`, paused during
  capture; every MJPEG viewer waits on the same `asyncio.Condition`.
* **UI contract:** the server is the source of truth.  The page renders
  `state_change`, `countdown_tick`, `shot_captured`, `camera_status`,
  `disk_status` and `error` events; on reconnect it receives a `hello`
  snapshot, and reloads if the server's `boot_id` changed.
* **Config:** `config.json` is merged on top of `core/defaults.py` and
  validated.  Changes apply when IDLE; the camera is only reopened when the
  `camera` section changed.
