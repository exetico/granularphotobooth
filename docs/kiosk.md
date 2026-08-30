# Kiosk Deployment Guide

This guide explains how to run GranularPhotoBooth in a locked-down kiosk
setup — typically on a dedicated Linux laptop or Raspberry Pi at a live event.

---

## 1. Enable Kiosk Mode in config.json

```json
"app": { "kiosk_mode": true }
```

When `kiosk_mode` is `true` the backend serves the UI with no address bar and
the browser is expected to run full-screen.

---

## 2. Systemd Service (Linux)

Create `/etc/systemd/system/photobooth.service`:

```ini
[Unit]
Description=GranularPhotoBooth backend
After=network.target

[Service]
User=photobooth
WorkingDirectory=/opt/granularphotobooth
ExecStart=/opt/granularphotobooth/.venv/bin/python run.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable photobooth
sudo systemctl start photobooth
```

---

## 3. Chromium Autostart (Linux Desktop / Raspberry Pi OS)

Create `~/.config/autostart/photobooth-browser.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=PhotoBooth Browser
Exec=chromium-browser --kiosk --app=http://localhost:8080 --noerrdialogs --disable-infobars
X-GNOME-Autostart-enabled=true
```

### Raspberry Pi OS (Wayland / wayfire)

Add to `/etc/xdg/wayfire/autostart`:

```ini
[autostart]
chromium = chromium-browser --kiosk --app=http://localhost:8080
```

---

## 4. Hide the Mouse Cursor

Install `unclutter` to hide the cursor after a few seconds of inactivity:

```bash
sudo apt install unclutter
```

Add to autostart:

```ini
unclutter = unclutter -idle 1 -root
```

---

## 5. Disable Screen Blanking

```bash
# X11
xset s off
xset -dpms
xset s noblank

# Or set in /etc/X11/xorg.conf.d/10-monitor.conf:
# Option "BlankTime" "0"
# Option "StandbyTime" "0"
# Option "SuspendTime" "0"
# Option "OffTime" "0"
```

---

## 6. Optional: Countdown Blur Effect

The countdown overlay blur is disabled by default to keep the UI lightweight.
Enable it per-event in `config.json`:

```json
"ui": { "countdown_blur": true }
```

---

## 7. Physical Trigger Button

Wire a USB keyboard (or custom HID device) to the laptop and set the trigger
key in `config.json`:

```json
"input": {
  "triggers": ["keyboard"],
  "keyboard": { "trigger_key": "space" }
}
```

Any single-character key or special key name accepted by `pynput` works.

---

## 8. Sending SIGHUP to Reload Config Without Restart

After editing `config.json` at a live event:

```bash
kill -HUP $(pgrep -f "python run.py")
```

The app applies the new countdown duration, storage target, etc. on the next
session — without dropping the kiosk UI.
