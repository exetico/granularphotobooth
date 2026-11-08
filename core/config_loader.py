"""Configuration loader with defaults, JSON Schema validation and hot-reload."""

import asyncio
import json
import logging
import os
from typing import Any, Callable

import jsonschema

from core.defaults import with_defaults

logger = logging.getLogger(__name__)

CAMERA_BACKENDS = ["webcam", "dslr_gphoto2", "ip_cam", "dummy"]

_num = {"type": "number", "minimum": 0}

# The schema validates the *merged* config (user config on top of defaults),
# so every section is guaranteed to exist.  Unknown keys are allowed so that
# a newer config file still loads on an older version.
_SCHEMA: dict = {
    "type": "object",
    "required": ["app", "camera", "input", "sequence", "compositor", "storage"],
    "properties": {
        "app": {
            "type": "object",
            "required": ["host", "port"],
            "properties": {
                "host": {"type": "string"},
                "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                "debug": {"type": "boolean"},
                "kiosk_mode": {"type": "boolean"},
                "log_file": {"type": ["string", "null"]},
                "min_free_disk_mb": _num,
            },
        },
        "ui": {
            "type": "object",
            "properties": {
                "countdown_blur": {"type": "boolean"},
                "mirror_preview": {"type": "boolean"},
                "trigger_keys": {"type": "array", "items": {"type": "string"}},
                "tap_anywhere_to_start": {"type": "boolean"},
                "lock_ui": {"type": "boolean"},
                "hide_cursor_after_seconds": _num,
                "show_status_bar": {"type": "boolean"},
                "texts": {"type": "object", "additionalProperties": {"type": "string"}},
            },
        },
        "camera": {
            "type": "object",
            "required": ["backend"],
            "properties": {
                "backend": {"type": "string", "enum": CAMERA_BACKENDS},
                "fallback_to_webcam": {"type": "boolean"},
                "capture_timeout_seconds": {"type": "number", "minimum": 1},
                "capture_retries": {"type": "integer", "minimum": 0, "maximum": 10},
                "preview_fps": {"type": "number", "minimum": 1, "maximum": 60},
                "reconnect_interval_seconds": {"type": "number", "minimum": 0.5},
                "backends": {"type": "object"},
            },
        },
        "input": {
            "type": "object",
            "required": ["triggers"],
            "properties": {
                "triggers": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["keyboard", "websocket_button"]},
                },
                "keyboard": {
                    "type": "object",
                    "properties": {"trigger_key": {"type": "string"}},
                },
                "debounce_ms": _num,
            },
        },
        "sequence": {
            "type": "object",
            "properties": {
                "shots": {"type": "integer", "minimum": 1, "maximum": 10},
                "countdown_seconds": {"type": "integer", "minimum": 0, "maximum": 120},
                "first_countdown_seconds": {"type": ["integer", "null"], "minimum": 0, "maximum": 120},
                "show_shot_seconds": _num,
                "preview_duration_seconds": {"type": "number", "minimum": 1},
                "delay_between_shots_seconds": _num,
                "retake_mode": {"type": "string", "enum": ["restart", "idle"]},
                "max_session_seconds": {"type": "number", "minimum": 10},
            },
        },
        "compositor": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "template_image": {"type": "string"},
                "overlay_image": {"type": "string"},
                "template_map": {"type": "string"},
                "background_color": {"type": "string"},
                "output_format": {"type": "string", "enum": ["JPEG", "PNG", "jpeg", "png"]},
                "output_quality": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
        "storage": {
            "type": "object",
            "required": ["targets"],
            "properties": {
                "targets": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["local_disk", "s3_minio", "ftp_nas"]},
                },
                "local_disk": {"type": "object"},
                "s3_minio": {"type": "object"},
                "ftp_nas": {"type": "object"},
            },
        },
    },
}


def validate(config: dict) -> dict:
    """Merge *config* onto the defaults, validate, and return the result.

    Raises ``ValueError`` with a human-readable message on problems.
    """
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")
    merged = with_defaults(config)
    try:
        jsonschema.validate(instance=merged, schema=_SCHEMA)
    except jsonschema.ValidationError as exc:
        where = ".".join(str(p) for p in exc.absolute_path) or "(root)"
        raise ValueError(f"Configuration error at '{where}': {exc.message}") from exc
    return merged


class ConfigLoader:
    """Loads and validates config.json."""

    def __init__(self, path: str = "config.json") -> None:
        self._path = path

    @property
    def path(self) -> str:
        return self._path

    def load(self) -> dict[str, Any]:
        """Read, parse, merge with defaults and validate the configuration."""
        if not os.path.isfile(self._path):
            raise FileNotFoundError(f"Configuration file not found: {self._path}")

        with open(self._path, encoding="utf-8") as fh:
            raw = fh.read()

        try:
            config: dict = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {self._path}: {exc}") from exc

        merged = validate(config)
        logger.debug("Configuration validated successfully")
        return merged

    async def watch(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Watch the config file and call *callback* with each new valid config.

        Uses ``watchfiles`` when installed, otherwise polls the mtime every 2 s.
        Invalid edits (bad JSON / schema errors) are logged and ignored, so a
        half-saved file never takes the booth down.  Runs until cancelled.
        """
        try:
            from watchfiles import awatch
        except ImportError:
            awatch = None

        if awatch is not None:
            logger.info("Config watcher started (watchfiles) for %s", self._path)
            # Watch the directory: editors often replace the file atomically,
            # which breaks a watch on the file itself.
            directory = os.path.dirname(os.path.abspath(self._path))
            target = os.path.abspath(self._path)
            async for changes in awatch(directory):
                if any(os.path.abspath(p) == target for _, p in changes):
                    self._on_file_change(callback)
            return

        logger.warning("watchfiles not installed — polling config every 2 s")
        mtime = os.path.getmtime(self._path) if os.path.isfile(self._path) else 0
        while True:
            await asyncio.sleep(2)
            try:
                new_mtime = os.path.getmtime(self._path)
            except OSError:
                continue
            if new_mtime != mtime:
                mtime = new_mtime
                self._on_file_change(callback)

    def _on_file_change(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Attempt to reload the config and invoke *callback* if valid."""
        try:
            new_config = self.load()
            logger.info("Config reloaded from %s", self._path)
            callback(new_config)
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("Config reload skipped (invalid file): %s", exc)
