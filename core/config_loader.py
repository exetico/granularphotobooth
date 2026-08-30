"""Configuration loader with JSON Schema validation."""

import json
import logging
import os
from typing import Any

import jsonschema

logger = logging.getLogger(__name__)

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
            },
        },
        "camera": {
            "type": "object",
            "required": ["backend"],
            "properties": {
                "backend": {"type": "string", "enum": ["webcam", "dslr_gphoto2", "ip_cam"]},
                "backends": {"type": "object"},
            },
        },
        "input": {
            "type": "object",
            "required": ["triggers"],
            "properties": {
                "triggers": {"type": "array", "items": {"type": "string"}},
                "keyboard": {
                    "type": "object",
                    "properties": {"trigger_key": {"type": "string"}},
                },
            },
        },
        "sequence": {
            "type": "object",
            "properties": {
                "shots": {"type": "integer", "minimum": 1, "maximum": 10},
                "countdown_seconds": {"type": "integer", "minimum": 0},
                "preview_duration_seconds": {"type": "integer", "minimum": 1},
                "delay_between_shots_seconds": {"type": "integer", "minimum": 0},
            },
        },
        "compositor": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "template_image": {"type": "string"},
                "template_map": {"type": "string"},
                "output_format": {"type": "string"},
                "output_quality": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
        "storage": {
            "type": "object",
            "required": ["targets"],
            "properties": {
                "targets": {"type": "array", "items": {"type": "string"}},
                "local_disk": {"type": "object"},
                "s3_minio": {"type": "object"},
                "ftp_nas": {"type": "object"},
            },
        },
    },
}


class ConfigLoader:
    """Loads and validates config.json."""

    def __init__(self, path: str = "config.json") -> None:
        self._path = path

    def load(self) -> dict[str, Any]:
        """Read, parse, and validate the configuration file."""
        if not os.path.isfile(self._path):
            raise FileNotFoundError(f"Configuration file not found: {self._path}")

        with open(self._path, encoding="utf-8") as fh:
            raw = fh.read()

        try:
            config: dict = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {self._path}: {exc}") from exc

        try:
            jsonschema.validate(instance=config, schema=_SCHEMA)
        except jsonschema.ValidationError as exc:
            raise ValueError(f"Configuration validation error: {exc.message}") from exc

        logger.debug("Configuration validated successfully")
        return config
