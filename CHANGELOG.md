# Changelog

## 0.12.0 — Admin panel

- `/admin`: status, test photo, reconnect camera, restart app, settings form,
  overlay/background upload, latest photos, log viewer, raw `config.json` editor.
- Access with the master token (`admin_token.txt`, link printed at start-up) or
  an optional PIN (`admin.pin`, 8+ digits) via 5 taps in the kiosk's top-left
  corner; PIN lockout after 5 wrong attempts; Ctrl+Shift+A shortcut.
- Config saves are validated, written atomically, backed up to `config.json.bak`
  and applied from the next session.  Only changed values are written.
- Restart re-executes the app in place (works under systemd / launchd).

## 0.11.0 — Stability release

### Fixed
- Pressing **Retake** made the next "Take photo" press get swallowed.
- `input.triggers: ["keyboard"]` did nothing — the keyboard trigger was never started.
- The app never exited on Ctrl+C / SIGTERM (config watcher kept running).
- Live view and capture used the camera from different threads at the same time
  (crashes/hangs with gphoto2).
- Full-resolution photos were sent as base64 over the WebSocket (≈40 MB per
  DSLR session) — the browser now loads screen-sized images by URL.
- Originals were only saved after the review screen; a crash lost them.
- A missing or unplugged camera was never reconnected.
- A browser that connected mid-session didn't know the current state.
- Webcam photos could be a stale buffered frame.
- gphoto2 widget lookup used the wrong API; RAW+JPEG captures could save the RAW.

### Added
- `CameraManager`: one camera thread, shared live view, timeouts, retries, auto-reconnect, status in the UI.
- `dummy` simulated camera; `--camera`, `--port` and `--doctor` command-line options.
- New kiosk UI: start screen, big countdown, flash, photo shown right after each shot,
  processing screen, review with Done/Retake and auto-return, camera/disk/connection banners,
  lockdown, wake lock, cursor hiding, self-reload after server restart, configurable texts and keys.
- Overlay PNG above photos, background colour, EXIF rotation, `repeat_frames`, layout validation.
- Session-based file names, atomic writes, never overwrite, rejected composites in `rejected/`.
- Disk-space guard, max session time, `/api/status`, `/healthz`, rotating log file.
- Config defaults (partial config files work), clearer validation errors.
- Install script, kiosk start script, systemd + launchd units, soak-test script, GitHub Actions CI.

### Changed
- Flow is now COUNTDOWN/CAPTURE × N → PROCESS → PREVIEW (review the finished result).
- Default `app.host` is `127.0.0.1`; set `0.0.0.0` to allow other devices.
- `boto3` and `gphoto2` moved to `requirements-optional.txt`; test tools to `requirements-dev.txt`.
