"""Built-in default configuration.

The user's ``config.json`` is deep-merged on top of these defaults, so a
config file only needs to contain the values that differ.  Older config
files (from before a key existed) therefore keep working after upgrades.
"""

import copy
from typing import Any

DEFAULTS: dict[str, Any] = {
    "app": {
        # 127.0.0.1 = only the booth computer itself can open the UI / trigger.
        # Use 0.0.0.0 if a tablet on the network should show the UI.
        "host": "127.0.0.1",
        "port": 8080,
        "debug": False,
        "kiosk_mode": False,
        "log_file": "logs/photobooth.log",
        "min_free_disk_mb": 500,
    },
    "ui": {
        "countdown_blur": False,
        "mirror_preview": True,
        "trigger_keys": ["Space", "Enter", "NumpadEnter", "PageDown", "PageUp"],
        "tap_anywhere_to_start": True,
        "lock_ui": True,
        "hide_cursor_after_seconds": 3,
        "show_status_bar": False,
        "texts": {
            "idle": "Tap anywhere to start",
            "get_ready": "Get ready!",
            "smile": "Smile!",
            "processing": "Making your photos…",
            "review_title": "Looking good!",
            "done": "Done",
            "retake": "Retake",
            "shot_of": "Photo {shot} of {total}",
            "camera_offline": "Camera not connected — reconnecting…",
            "disk_full": "Storage is almost full — please tell the host",
            "connection_lost": "Reconnecting…",
            "error": "Something went wrong — please try again",
        },
    },
    "camera": {
        "backend": "webcam",
        # If the selected backend cannot be opened, use the webcam instead.
        # Off by default: on a DSLR booth you want to *know* the DSLR is gone,
        # not silently get webcam-quality pictures.
        "fallback_to_webcam": False,
        "capture_timeout_seconds": 30,
        "capture_retries": 2,
        "preview_fps": 15,
        "reconnect_interval_seconds": 3,
        "backends": {
            "webcam": {
                "device_index": 0,
                "resolution": [1280, 720],
                "preview_resolution": [640, 360],
            },
            "dslr_gphoto2": {
                "model_hint": "",
                "liveview": True,
                "kill_ptpcamera_on_macos": True,
            },
            "ip_cam": {"stream_url": ""},
            "dummy": {"resolution": [1800, 1200], "preview_resolution": [640, 426]},
        },
    },
    "input": {
        "triggers": ["websocket_button"],
        "keyboard": {"trigger_key": "space"},
        "debounce_ms": 800,
    },
    "sequence": {
        "shots": 4,
        "countdown_seconds": 3,
        "first_countdown_seconds": None,  # None = same as countdown_seconds
        "show_shot_seconds": 1.5,
        "delay_between_shots_seconds": 0,
        "preview_duration_seconds": 10,
        # "restart" = Retake starts a new session right away
        # "idle"    = Retake goes back to the start screen
        "retake_mode": "restart",
        "max_session_seconds": 300,
    },
    "compositor": {
        "enabled": True,
        "template_image": "",
        "overlay_image": "",
        "template_map": "assets/layouts/4shot_strip.json",
        "background_color": "#ffffff",
        "output_format": "JPEG",
        "output_quality": 92,
    },
    "storage": {
        "targets": ["local_disk"],
        "local_disk": {
            "enabled": True,
            "raw_dir": "output/raw/",
            "composite_dir": "output/composites/",
            "filename_format": "{session}_{kind}.jpg",
        },
        "s3_minio": {"enabled": False},
        "ftp_nas": {"enabled": False},
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    """Return a new dict: *override* merged recursively on top of *base*."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def with_defaults(config: dict) -> dict:
    return deep_merge(DEFAULTS, config)
