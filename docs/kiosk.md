# Kiosk Deployment Guide

> **Shortcut:** `./scripts/start-kiosk.sh` starts the backend and opens a
> locked-down full-screen Chromium/Chrome/Edge on Linux and macOS.

## What the UI locks down by itself (`ui.lock_ui`, default on)

No right-click, text selection, image dragging, pinch / double-tap / ctrl+wheel
zoom, back-swipe navigation, or F5 / Ctrl+R / Ctrl+P / Ctrl+S-style
shortcuts; the mouse cursor hides after 3 s; the screen is kept awake (Wake
Lock API).  With `app.kiosk_mode: true` the page also goes full-screen on the
first touch.  What a web page *cannot* block (Alt+F4, Cmd+Q, Alt+Tab) is
handled by running the browser in `--kiosk` mode on a dedicated user account.

**Operator exit:** plug in a keyboard and press Alt+F4 (Linux) / Cmd+Q (macOS).

**Admin panel on the kiosk:** set `admin.pin` (8+ digits) and tap the
top-left corner 5 times → PIN pad → settings, test photo, restart.

## macOS kiosk

1. Create a separate standard user "booth" with automatic login.
2. Install the launchd agent: see `scripts/com.granularphotobooth.plist`.
3. Add `scripts/start-kiosk.sh` to Login Items (System Settings → General).
4. System Settings: Lock Screen → never turn off display; Battery → prevent sleep;
   Notifications → Do Not Disturb.
5. Allow camera access for Python the first time macOS asks.

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
ExecReload=/bin/kill -HUP $MAINPID
Restart=always
RestartSec=2

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

Most USB "buzzer" buttons, foot pedals and presenter clickers simply send a
key press (Space, Enter, PageDown…).  The kiosk page listens for the keys in
`ui.trigger_keys`, so they work out of the box while the browser is focused
(which it always is in kiosk mode).

If the browser may *not* have focus, enable the global hotkey listener:

```json
"input": {
  "triggers": ["keyboard", "websocket_button"],
  "keyboard": { "trigger_key": "space" }
}
```

* macOS: allow Python in System Settings → Privacy → Accessibility + Input Monitoring.
* Linux: requires an X11 session (Wayland blocks global key hooks).

---

## 8. Sending SIGHUP to Reload Config Without Restart

After editing `config.json` at a live event:

```bash
kill -HUP $(pgrep -f "python run.py")     # or: sudo systemctl reload photobooth
```

(Saving `config.json` is also detected automatically.)  The app applies the new countdown duration, storage target, etc. on the next
session — without dropping the kiosk UI.
