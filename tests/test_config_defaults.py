import json

import pytest

from core.config_loader import ConfigLoader, validate


def test_empty_config_gets_defaults():
    cfg = validate({})
    assert cfg["app"]["port"] == 8080
    assert cfg["camera"]["backend"] == "webcam"
    assert cfg["storage"]["targets"] == ["local_disk"]


def test_partial_config_merges_deeply():
    cfg = validate({"camera": {"backends": {"webcam": {"device_index": 2}}}})
    assert cfg["camera"]["backends"]["webcam"]["device_index"] == 2
    assert cfg["camera"]["backends"]["webcam"]["resolution"] == [1280, 720]


def test_error_message_names_the_key():
    with pytest.raises(ValueError, match="camera.backend"):
        validate({"camera": {"backend": "polaroid"}})


def test_shipped_config_is_valid():
    cfg = ConfigLoader("config.json").load()
    assert cfg["sequence"]["shots"] >= 1


def test_legacy_config_still_loads(tmp_path):
    """Config files from before this release must keep working."""
    legacy = {
        "app": {"host": "0.0.0.0", "port": 8080, "debug": True, "kiosk_mode": False},
        "ui": {"countdown_blur": False},
        "camera": {"backend": "webcam", "backends": {"webcam": {"device_index": 0}}},
        "input": {"triggers": ["keyboard", "websocket_button"], "keyboard": {"trigger_key": "space"}},
        "sequence": {"shots": 4, "countdown_seconds": 3, "preview_duration_seconds": 8,
                     "delay_between_shots_seconds": 2},
        "compositor": {"enabled": True, "template_image": "assets/layouts/4shot_strip.png",
                       "template_map": "assets/layouts/4shot_strip.json", "output_format": "JPEG",
                       "output_quality": 90},
        "storage": {"targets": ["local_disk"], "local_disk": {"enabled": True, "raw_dir": "output/raw/",
                    "composite_dir": "output/composites/", "filename_format": "photo_{timestamp}_{uuid}.jpg"}},
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(legacy))
    cfg = ConfigLoader(str(p)).load()
    assert cfg["app"]["host"] == "0.0.0.0"
