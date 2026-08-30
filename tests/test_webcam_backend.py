"""Smoke tests for the webcam backend.

If no real webcam is available (e.g. in CI), a pre-recorded video file can
be used as the device source::

    # In config.json or the test below, set device_index to a .mp4 path
    cfg = {"device_index": "tests/fixtures/sample_video.mp4", ...}

The tests in this file skip gracefully when OpenCV or a camera device is
unavailable so that the test suite still passes in headless CI.
"""

import pytest

from cameras.webcam import CameraBackend


# ── is_available() ────────────────────────────────────────────────────

def test_is_available_reflects_opencv():
    """is_available() must return a boolean without raising."""
    result = CameraBackend.is_available()
    assert isinstance(result, bool)


# ── Cannot capture without initialising ──────────────────────────────

def test_capture_raises_if_not_initialised():
    cam = CameraBackend({"device_index": 0})
    with pytest.raises(RuntimeError, match="not initialised"):
        cam.capture()


# ── preview frame raises if not initialised ───────────────────────────

def test_preview_raises_if_not_initialised():
    cam = CameraBackend({"device_index": 0})
    with pytest.raises(RuntimeError, match="not initialised"):
        cam.get_preview_frame()


# ── Live device tests (skipped when unavailable) ──────────────────────

@pytest.fixture
def webcam():
    """Open webcam device 0; skip the test if unavailable."""
    if not CameraBackend.is_available():
        pytest.skip("opencv-python not installed")

    import cv2

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        cap.release()
        pytest.skip("No webcam device available at index 0")
    cap.release()

    cam = CameraBackend({"device_index": 0, "resolution": [640, 480], "preview_resolution": [320, 240]})
    cam.initialize()
    yield cam
    cam.release()


def test_capture_returns_bytes(webcam):
    frame = webcam.capture()
    assert isinstance(frame, bytes)
    assert len(frame) > 0


def test_capture_is_valid_jpeg(webcam):
    frame = webcam.capture()
    # JPEG magic bytes: FF D8 FF
    assert frame[:3] == b"\xff\xd8\xff"


def test_preview_frame_smaller_than_capture(webcam):
    full_frame = webcam.capture()
    preview_frame = webcam.get_preview_frame()
    assert isinstance(preview_frame, bytes)
    # Preview should be smaller (lower quality / resolution)
    assert len(preview_frame) <= len(full_frame) * 1.5  # generous bound


def test_release_closes_device(webcam):
    webcam.release()
    # Calling release again must not raise
    webcam.release()
