# TODO — road to replacing dslrBooth

Goal: a booth that can run a whole event night unattended on Linux or macOS,
with a DSLR, branded layouts, and photos saved locally.  Uploading, printing
and sharing are intentionally **out of scope** (see bottom).

Legend: **P0** = before the first real event · **P1** = dslrBooth parity
for how it is used · **P2** = nice to have / hardening

---

## P0 — before the first real event

- [ ] **DSLR field test (Canon EOS 650D)** on the real booth computer, Linux *and*
      macOS: `run.py --doctor --camera dslr_gphoto2`, then 200 sessions with
      `scripts/soak_test.py`.  Note quirks in `docs/dslr_setup.md`.
- [ ] **Unplug test:** pull the USB cable / switch the camera off mid-countdown
      and during IDLE → booth must show "Camera not connected", recover on re-plug.
- [ ] **Dark-room autofocus test** → decide MF vs AF; tune `capture_retries`.
- [ ] **4-hour soak test** with live view on (camera heat, battery/AC adapter, RAM).
- [ ] **Real screen + touch test** (the event monitor, portrait or landscape) —
      check text sizes, buttons, review screen, cursor hiding.
- [ ] **Event layout:** create the background/overlay PNGs for your strip(s)
      and verify with `--doctor`.
- [ ] Translate `ui.texts` (Danish) in `config.json`.
- [ ] Power-cut test: pull power mid-session, boot again → booth auto-starts,
      no corrupt files.

## P1 — dslrBooth parity

- [ ] **Admin panel** (roadmap Phase 13): PIN-protected page (hidden 5-tap corner
      gesture on the kiosk) to change shots / countdown / layout, test the
      camera, see last photos & status, restart/shutdown.  Must write
      `config.json` atomically and validate before saving.
- [ ] **Event folders:** `storage.event_name` → `output/<event>/raw|composites`,
      switchable from the admin panel.
- [ ] **Multiple layouts / modes chosen by the guest** (e.g. "1 photo" vs
      "4-photo strip") on the start screen.
- [ ] **Layout preview tool:** `run.py --render-layout <layout.json>` that
      renders a layout with sample photos (for designing overlays without a booth).
- [ ] **Ship 2–3 example layout packs** (portrait strip, 2×2 postcard,
      single photo with frame) with background + overlay PNGs.
- [ ] **Attract screen:** after N minutes idle, show a slideshow of the
      night's composites (and stop DSLR live view to save battery/heat).
- [ ] **Countdown/shutter sounds** (optional, configurable).
- [ ] **Auto-switch back** from webcam fallback to the DSLR when it is reconnected.
- [ ] **Camera settings from the camera:** read valid ISO/aperture/shutter
      choices via gphoto2 and offer them in the admin panel.
- [ ] **Watermark/date text** rendered by the compositor (event name, date)
      without needing an overlay PNG.

## P2 — hardening & polish

- [ ] systemd `Type=notify` + `WatchdogSec` (sd_notify heartbeat from the broker).
- [ ] Old-session cleanup / warn when output folder exceeds X GB.
- [ ] Browser end-to-end tests (Playwright) in CI for the kiosk UI.
- [ ] Packaging: `pipx install granularphotobooth`, a `.deb`, and a macOS
      `.app` bundle so non-technical users don't need a terminal.
- [ ] Raspberry Pi GPIO / Arduino-serial button input backend.
- [ ] Per-shot live view fallback when the DSLR has no live view
      (`liveview: false` → show a static "look at the camera" screen).
- [ ] Persistent upload queue (only if S3/FTP is ever used again).
- [ ] Prometheus-style `/metrics` (sessions, failures, capture times).
- [ ] Type checking (mypy) + ruff in CI.

## Done in the stability release (0.11.0)

- [x] Single camera thread, shared live view, timeouts, retries, auto-reconnect
- [x] Originals saved immediately and atomically; session-grouped filenames
- [x] Retake starts a new session; rejected composites kept in `rejected/`
- [x] Fixed: swallowed trigger after retake, keyboard trigger never wired,
      app not exiting on SIGTERM, full-res base64 images over WebSocket
- [x] Disk-space guard, session time limit, friendly error screens
- [x] Locked-down kiosk UI with auto-reconnect, self-reload, wake lock
- [x] Overlay PNG on top of photos, EXIF rotation, layout validation
- [x] Config defaults + partial configs, clear validation errors
- [x] `--doctor` self-check, `dummy` camera, soak-test script
- [x] Install script, kiosk start script, systemd + launchd files
- [x] GitHub Actions CI (Ubuntu + macOS, Python 3.10–3.13)

## Out of scope (by decision)

- Printing
- Uploading / QR / email / social sharing (S3/FTP code stays, optional, untouched)
- GIF / boomerang / video, green screen, AI props
- Windows support (gphoto2 does not run on Windows; would need Canon EDSDK)
