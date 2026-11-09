# DSLR / Mirrorless Setup (gphoto2)

Works on **Linux** and **macOS** with any camera supported by libgphoto2
(most Canon EOS, Nikon, Sony Alpha, Fuji X).  Check yours:
<http://www.gphoto.org/proj/libgphoto2/support.php>

## Install

```bash
./scripts/install.sh --dslr
```

## Check

```bash
gphoto2 --auto-detect          # should list your camera
.venv/bin/python run.py --doctor --camera dslr_gphoto2   # takes a real test photo
```

Then set `"camera": {"backend": "dslr_gphoto2"}` in `config.json`.

## Camera settings checklist (Canon EOS 650D and similar)

| Setting | Recommended | Why |
|---|---|---|
| Mode dial | **M** (manual) | Same exposure for every shot |
| Image quality | **JPEG L** (or RAW+JPEG) | The booth needs a JPEG; RAW-only is rejected with a clear message |
| Focus | **MF** after focusing once, or AF with good light | Autofocus failing in the dark is the #1 cause of failed captures |
| Auto power off | **Disable** | Otherwise the camera disappears after a few minutes |
| Live view | Works through USB | Used for the on-screen preview |
| Power | **AC adapter / dummy battery** (Canon ACK-E8 for the 650D) | Live view drains batteries in ~1–2 h |
| Wi-Fi / Eye-Fi | Off | Can block USB control |
| SD card | Inserted | Photos also stay on the card as a backup (unless `delete_after_download`) |

Optional settings in `config.json` (`camera.backends.dslr_gphoto2`):
`iso`, `aperture`, `shutter_speed`, `white_balance`, `image_format`,
`capture_target` — values must match what `gphoto2 --get-config iso` lists.
Invalid values are logged with the list of valid ones.

## macOS

macOS starts `PTPCamera` whenever a camera is plugged in and it locks the
USB device.  The booth kills it automatically
(`kill_ptpcamera_on_macos: true`).  Also quit Photos / Image Capture.

## Linux

* Add your user to the `plugdev` group (`sudo usermod -aG plugdev $USER`).
* If GNOME/KDE auto-mounts the camera (gvfs), it blocks gphoto2:
  `sudo apt remove gvfs-backends` or `systemctl --user mask gvfs-gphoto2-volume-monitor`.

## When things go wrong at the event

* Camera unplugged / battery swapped: just plug it back in — the booth
  reconnects by itself within a few seconds.
* `Camera produced no JPEG`: switch image quality to JPEG.
* Repeated `capture failed`: check focus (switch the lens to MF) and light.
* Check `http://localhost:8080/api/status` and `logs/photobooth.log`.
