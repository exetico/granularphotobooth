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


# ── split raw_dir / composite_dir ─────────────────────────────────────

def test_raw_goes_to_raw_dir(tmp_path):
    raw_dir = str(tmp_path / "raw")
    composite_dir = str(tmp_path / "composites")
    target = StorageTarget({
        "raw_dir": raw_dir,
        "composite_dir": composite_dir,
        "filename_format": "photo_{timestamp}_{uuid}.jpg",
    })
    path = target.upload(_make_jpeg_bytes(), {"type": "raw"})
    assert path.startswith(raw_dir)
    assert os.path.isfile(path)


def test_composite_goes_to_composite_dir(tmp_path):
    raw_dir = str(tmp_path / "raw")
    composite_dir = str(tmp_path / "composites")
    target = StorageTarget({
        "raw_dir": raw_dir,
        "composite_dir": composite_dir,
        "filename_format": "photo_{timestamp}_{uuid}.jpg",
    })
    path = target.upload(_make_jpeg_bytes(), {"type": "composite"})
    assert path.startswith(composite_dir)
    assert os.path.isfile(path)


def test_is_configured_with_raw_dir():
    target = StorageTarget({"raw_dir": "/tmp/raw/", "filename_format": "photo_{timestamp}_{uuid}.jpg"})
    assert target.is_configured() is True


# ── Session naming / safety ───────────────────────────────────────────

def test_session_naming(tmp_path):
    target = StorageTarget({"raw_dir": str(tmp_path / "r"), "composite_dir": str(tmp_path / "c")})
    raw = target.upload(b"x", {"type": "raw", "frame": 2, "session_id": "S1"})
    comp = target.upload(b"y", {"type": "composite", "session_id": "S1"})
    assert raw.endswith("S1_shot2.jpg") and comp.endswith("S1_composite.jpg")


def test_never_overwrites(tmp_path):
    target = StorageTarget({"raw_dir": str(tmp_path), "filename_format": "same.jpg"})
    a = target.upload(b"a", {"type": "raw", "frame": 1})
    b = target.upload(b"b", {"type": "raw", "frame": 1})
    assert a != b
    assert open(a, "rb").read() == b"a" and open(b, "rb").read() == b"b"


def test_legacy_format_gets_shot_suffix(tmp_path):
    target = StorageTarget({"raw_dir": str(tmp_path), "filename_format": "photo_{timestamp}_{uuid}.jpg"})
    path = target.upload(b"a", {"type": "raw", "frame": 3})
    assert path.endswith("_shot3.jpg")


def test_no_temp_files_left(tmp_path):
    target = StorageTarget({"raw_dir": str(tmp_path)})
    target.upload(b"a" * 1000, {"type": "raw", "frame": 1})
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_filename_cannot_escape_dir(tmp_path):
    target = StorageTarget({"raw_dir": str(tmp_path / "r"), "filename_format": "../../{kind}.jpg"})
    path = target.upload(b"a", {"type": "raw", "frame": 1})
    assert os.path.dirname(path) == str(tmp_path / "r")


def test_pipeline_local_failure_raises(tmp_path):
    from storage.pipeline import StoragePipeline
    blocker = tmp_path / "file"
    blocker.write_text("not a dir")
    pipe = StoragePipeline({"storage": {"targets": ["local_disk"], "local_disk": {
        "raw_dir": str(blocker / "sub"), "composite_dir": str(blocker / "sub")}}})
    with pytest.raises(RuntimeError):
        pipe.store_local(b"x", {"type": "raw", "frame": 1})
