# GranularPhotoBooth

A stable, open-source **photo booth** for Linux and macOS: webcam, DSLR /
mirrorless (gphoto2) or IP camera, a full-screen touch UI, photo-strip
layouts with your own background and overlay, and everything saved locally.
No cloud account, no license key, no subscription.

Every function is a plug-and-play module — swap cameras, layouts and storage
in `config.json` without touching code.

---

## Quick start

```bash
git clone https://github.com/exetico/granularphotobooth.git
cd granularphotobooth
./scripts/install.sh            # add --dslr for DSLR / mirrorless support
./scripts/start-kiosk.sh        # starts the booth + full-screen browser
```

No camera at hand?  Try everything with the built-in simulated camera:

```bash
.venv/bin/python run.py --camera dummy      # then open http://localhost:8080
```

Check your setup at any time (camera, disk, layout, dependencies):

```bash
.venv/bin/python run.py --doctor
```

Guests tap the screen (or press Space / Enter / a USB buzzer / presenter
clicker), get a countdown, see each photo right after it is taken, and then
see the finished strip with **Done** / **Retake**.

---

## What makes it stable

| Problem at events | What the booth does |
|---|---|
| Camera unplugged, switched off, battery swapped | Shows "Camera not connected — reconnecting…", reconnects automatically, no restart needed |
| DSLR / driver hangs | Every camera call has a timeout; the hung call is abandoned and the camera reopened |
| Two things using the camera at once (crashes gphoto2) | All camera access goes through **one** camera thread; live view is shared by all screens |
| Crash / power cut / Retake | Each original is written to disk **immediately** after capture, atomically — nothing is ever lost or half-written |
| Browser reloaded / server restarted / upgraded | The kiosk reconnects, gets the current state, and reloads itself if the server changed |
| Disk filling up | Free space is monitored; the booth refuses to start a session and shows a message before it is full |
| Guests poking at the browser | Locked UI: no zoom, no right-click, no text selection, no back-navigation, no reload keys, cursor hidden, screen kept awake |
| Something unexpected | Every session has a hard time limit; any error shows a friendly message and returns to the start screen |
| Network upload slow/offline (if you use S3/FTP) | Uploads run in the background with retries — never block the booth |

---

## Configuration

Everything is in `config.json`.  You only need the keys you want to change —
everything else falls back to sensible defaults (`core/defaults.py`), so old
config files keep working after upgrades.  Changes are picked up
automatically (applied before the next session).

| Setting | Effect |
|---|---|
| `camera.backend` | `"webcam"`, `"dslr_gphoto2"`, `"ip_cam"` or `"dummy"` (simulated) |
| `camera.fallback_to_webcam` | Use the webcam if the DSLR is missing (default `false`) |
| `sequence.shots` | 1–10 photos per session |
| `sequence.countdown_seconds` / `first_countdown_seconds` | Countdown per shot / before the first shot |
| `sequence.show_shot_seconds` | How long each photo is shown right after it is taken |
| `sequence.preview_duration_seconds` | How long the finished result is shown |
| `sequence.retake_mode` | `"restart"` (Retake starts again) or `"idle"` |
| `compositor.template_map` | Layout JSON (slot positions) — see below |
| `compositor.template_image` | Background image *under* the photos |
| `compositor.overlay_image` | Transparent PNG *over* the photos (logo, frame, text) |
| `storage.local_disk.raw_dir` / `composite_dir` | Where originals / finished strips go |
| `ui.texts.*` | Every text on screen (translate to your language here) |
| `ui.trigger_keys` | Keys that start a session (USB buttons usually send Space/Enter) |
| `ui.mirror_preview` | Mirror the live view like a mirror (photos are never mirrored) |
| `app.kiosk_mode` | Go full-screen on first touch |
| `app.host` | `127.0.0.1` (only this computer) or `0.0.0.0` (allow a tablet on the network) |

Example — Danish texts:

```json
"ui": { "texts": { "idle": "Tryk for at starte", "smile": "Smil!", "done": "Færdig",
                   "retake": "Tag om", "shot_of": "Billede {shot} af {total}" } }
```

### Layouts, backgrounds and overlays

A layout is a JSON file with the canvas size and one slot per photo:

```json
{
  "canvas_size": [1200, 1800],
  "overlay_image": "my_event_overlay.png",
  "slots": [
    { "x": 60, "y": 60,  "width": 1080, "height": 560 },
    { "x": 60, "y": 660, "width": 1080, "height": 560 }
  ]
}
```

Photos are crop-to-fill (never stretched) and EXIF-rotated.  Image paths are
relative to the layout file, so a layout folder is self-contained.  Set
`"repeat_frames": true` to reuse the shots for a doubled 2×6 strip.

### Output

```
output/raw/20261108-201502_ab12cd_shot1.jpg     ← every original, saved at capture time
output/composites/20261108-201502_ab12cd_composite.jpg
output/composites/rejected/…                    ← results the guest pressed Retake on
```

---

## Running as a service

* **Linux:** `scripts/photobooth.service` (systemd, auto-restart) — see [`docs/kiosk.md`](docs/kiosk.md)
* **macOS:** `scripts/com.granularphotobooth.plist` (launchd, auto-restart)
* DSLR setup and camera-specific tips: [`docs/dslr_setup.md`](docs/dslr_setup.md)
* Status for operators: `http://localhost:8080/api/status`
* Logs: `logs/photobooth.log` (rotated automatically)

---

## Development

```bash
pip install -r requirements-dev.txt
pytest                                            # ~80 tests, no hardware needed
.venv/bin/python scripts/soak_test.py --sessions 200   # stress-test a running booth
```

Architecture: [`docs/architecture.md`](docs/architecture.md) ·
Roadmap / what's next: [`docs/TODO.md`](docs/TODO.md) ·
History: [`docs/roadmap.md`](docs/roadmap.md), [`CHANGELOG.md`](CHANGELOG.md)

### Adding a camera

1. Create `cameras/my_cam.py` with a class `CameraBackend(cameras.base.CameraBackend)`.
   Methods are plain blocking code — `CameraManager` handles threads, timeouts and retries.
2. Register it in `BACKEND_MODULES` in `cameras/manager.py` and in
   `CAMERA_BACKENDS` in `core/config_loader.py`.
3. Set `"backend": "my_cam"` in `config.json`.
