"""Tests for the local disk storage target."""

import os
import pytest

from storage.local_disk import StorageTarget


def _make_jpeg_bytes() -> bytes:
    from PIL import Image
    import io
    img = Image.new("RGB", (10, 10), color=(0, 128, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ── is_configured() ───────────────────────────────────────────────────

def test_is_configured_with_output_dir():
    target = StorageTarget({"output_dir": "/tmp/test_output/", "filename_format": "photo_{timestamp}_{uuid}.jpg"})
    assert target.is_configured() is True


def test_is_configured_without_output_dir():
    target = StorageTarget({})
    assert target.is_configured() is False


# ── upload() ─────────────────────────────────────────────────────────

def test_upload_creates_file(tmp_path):
    target = StorageTarget({
        "output_dir": str(tmp_path),
        "filename_format": "photo_{timestamp}_{uuid}.jpg",
    })
    image_bytes = _make_jpeg_bytes()
    result_path = target.upload(image_bytes, {"shots": 1})

    assert os.path.isfile(result_path)
    with open(result_path, "rb") as fh:
        saved = fh.read()
    assert saved == image_bytes


def test_upload_creates_output_dir_if_missing(tmp_path):
    nested = os.path.join(str(tmp_path), "deeply", "nested", "output")
    target = StorageTarget({
        "output_dir": nested,
        "filename_format": "photo_{timestamp}_{uuid}.jpg",
    })
    result_path = target.upload(_make_jpeg_bytes(), {})
    assert os.path.isfile(result_path)


def test_upload_filename_contains_timestamp_and_uuid(tmp_path):
    target = StorageTarget({
        "output_dir": str(tmp_path),
        "filename_format": "photo_{timestamp}_{uuid}.jpg",
    })
    path = target.upload(_make_jpeg_bytes(), {})
    filename = os.path.basename(path)
    assert filename.startswith("photo_")
    assert filename.endswith(".jpg")
    # filename should have timestamp and uuid parts
    parts = filename[len("photo_"):-len(".jpg")].split("_")
    assert len(parts) == 2


def test_upload_multiple_writes_unique_files(tmp_path):
    target = StorageTarget({
        "output_dir": str(tmp_path),
        "filename_format": "photo_{timestamp}_{uuid}.jpg",
    })
    paths = [target.upload(_make_jpeg_bytes(), {}) for _ in range(3)]
    assert len(set(paths)) == 3
