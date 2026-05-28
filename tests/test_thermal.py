"""Tests for thermal camera encoding and the send_thermal_frame wire message."""

from __future__ import annotations

import pytest

# Thermal support requires opencv + numpy (the `thermal`/`dev` extras). Skip the
# whole module cleanly if they're not installed rather than erroring on import.
np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from plexus.cameras.thermal import (  # noqa: E402
    SimulatedThermalCamera,
    ThermalFrame,
    _upscale_size,
    build_thermal_frame,
    encode_frame,
)

# ---------------------------------------------------------------------------
# _upscale_size — pure geometry, no encoding
# ---------------------------------------------------------------------------


class TestUpscaleSize:
    def test_large_sensor_unchanged(self):
        assert _upscale_size(256, 192) == (256, 192)

    def test_small_sensor_upscaled_to_min_side(self):
        # MLX90640 32x24: shortest side (24) scaled up to 160.
        dw, dh = _upscale_size(32, 24)
        assert min(dw, dh) == 160
        # Aspect ratio preserved (4:3).
        assert dw == round(dh * 32 / 24)

    def test_tiny_sensor_upscaled(self):
        # MLX90641 16x12.
        dw, dh = _upscale_size(16, 12)
        assert min(dw, dh) == 160

    def test_square_below_threshold(self):
        assert _upscale_size(100, 100) == (160, 160)


# ---------------------------------------------------------------------------
# SimulatedThermalCamera
# ---------------------------------------------------------------------------


class TestSimulatedCamera:
    def test_frame_shape_and_dtype(self):
        cam = SimulatedThermalCamera()
        frame = cam.read_frame()
        assert frame.shape == (24, 32)  # (height, width)
        assert frame.dtype == np.float32

    def test_dimensions(self):
        cam = SimulatedThermalCamera()
        assert cam.width == 32
        assert cam.height == 24


# ---------------------------------------------------------------------------
# build_thermal_frame + ThermalFrame.to_message
# ---------------------------------------------------------------------------


class TestBuildThermalFrame:
    def test_small_sensor_includes_temps(self):
        temps = np.arange(768, dtype=np.float32).reshape(24, 32)
        frame = build_thermal_frame(temps)
        assert isinstance(frame, ThermalFrame)
        assert frame.sensor_width == 32
        assert frame.sensor_height == 24
        assert frame.temp_min == 0.0
        assert frame.temp_max == 767.0
        # 768 px <= 4096 threshold → temps retained.
        assert frame.temps is not None

    def test_large_sensor_drops_temps(self):
        # 100x100 = 10_000 px > 4096 threshold → temps omitted.
        temps = np.zeros((100, 100), dtype=np.float32)
        frame = build_thermal_frame(temps)
        assert frame.temps is None

    def test_image_upscaled_for_small_sensor(self):
        temps = np.zeros((24, 32), dtype=np.float32)
        frame = build_thermal_frame(temps)
        assert min(frame.width, frame.height) == 160

    def test_timestamp_passthrough(self):
        temps = np.zeros((24, 32), dtype=np.float32)
        frame = build_thermal_frame(temps, timestamp_ms=1_234_567_890)
        assert frame.timestamp_ms == 1_234_567_890

    def test_uniform_frame_does_not_crash(self):
        # Zero span (all pixels equal) must not divide-by-zero.
        temps = np.full((24, 32), 25.0, dtype=np.float32)
        frame = build_thermal_frame(temps)
        assert frame.temp_min == 25.0
        assert frame.temp_max == 25.0


class TestToMessage:
    def _msg(self, **kwargs):
        temps = np.arange(768, dtype=np.float32).reshape(24, 32)
        frame = build_thermal_frame(temps, timestamp_ms=1000)
        return frame, frame.to_message(**kwargs)

    def test_message_shape(self):
        _, msg = self._msg(camera_id="thermal", source_id="dev1")
        assert msg["type"] == "video_frame"
        assert msg["camera_id"] == "thermal"
        assert msg["source_id"] == "dev1"
        assert msg["video_type"] == "thermal"
        assert msg["sensor_width"] == 32
        assert msg["sensor_height"] == 24
        assert msg["timestamp"] == 1000
        assert isinstance(msg["frame"], str) and msg["frame"]  # base64 JPEG

    def test_temps_flattened_and_preserved(self):
        _, msg = self._msg(camera_id="thermal")
        assert "temps" in msg
        assert len(msg["temps"]) == 768
        assert msg["temps"] == list(range(768))

    def test_source_id_omitted_when_none(self):
        _, msg = self._msg(camera_id="thermal")
        assert "source_id" not in msg

    def test_display_dims_match_upscaled_image(self):
        frame, msg = self._msg(camera_id="thermal")
        assert msg["width"] == frame.width
        assert msg["height"] == frame.height
        assert min(msg["width"], msg["height"]) == 160

    def test_large_sensor_message_has_no_temps(self):
        temps = np.zeros((100, 100), dtype=np.float32)
        msg = build_thermal_frame(temps).to_message(camera_id="t")
        assert "temps" not in msg


# ---------------------------------------------------------------------------
# encode_frame — camera → ThermalFrame
# ---------------------------------------------------------------------------


def test_encode_frame_reads_camera():
    cam = SimulatedThermalCamera()
    frame = encode_frame(cam)
    assert frame.sensor_width == 32
    assert frame.sensor_height == 24
    assert frame.temps is not None
