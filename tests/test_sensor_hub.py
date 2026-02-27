"""Tests for SensorHub concurrent reads, timeouts, and failure handling."""

import time
from typing import Dict, List, Optional

import pytest

from plexus.sensors.base import BaseSensor, SensorHub, SensorReading


class FastSensor(BaseSensor):
    """Sensor that returns immediately."""
    name = "Fast"
    metrics = ["fast_value"]

    def read(self) -> List[SensorReading]:
        return [SensorReading("fast_value", 42.0)]


class SlowSensor(BaseSensor):
    """Sensor that blocks for a long time (simulates I2C hang)."""
    name = "Slow"
    metrics = ["slow_value"]

    def __init__(self, delay: float = 10.0, **kwargs):
        super().__init__(**kwargs)
        self._delay = delay

    def read(self) -> List[SensorReading]:
        time.sleep(self._delay)
        return [SensorReading("slow_value", 99.0)]


class FailingSensor(BaseSensor):
    """Sensor that always raises."""
    name = "Failing"
    metrics = ["fail_value"]

    def read(self) -> List[SensorReading]:
        raise RuntimeError("sensor exploded")


class CountingSensor(BaseSensor):
    """Sensor that tracks call count and alternates success/failure."""
    name = "Counter"
    metrics = ["count"]

    def __init__(self, fail_until: int = 0, **kwargs):
        super().__init__(**kwargs)
        self.call_count = 0
        self._fail_until = fail_until

    def read(self) -> List[SensorReading]:
        self.call_count += 1
        if self.call_count <= self._fail_until:
            raise RuntimeError("not yet")
        return [SensorReading("count", self.call_count)]


class ValidatingSensor(BaseSensor):
    """Sensor with a validate_reading override that rejects negatives."""
    name = "Validating"
    metrics = ["val"]

    def read(self) -> List[SensorReading]:
        return [
            SensorReading("val", 10.0),
            SensorReading("val", -5.0),
            SensorReading("val", 20.0),
        ]

    def validate_reading(self, reading: SensorReading) -> bool:
        return reading.value >= 0


# ─── Concurrent reads / timeouts ─────────────────────────────────────────────


class TestConcurrentReads:
    def test_slow_sensor_does_not_block_fast(self):
        """A hanging sensor should not prevent other sensors from reading."""
        hub = SensorHub(default_timeout=0.5)
        hub.add(FastSensor(sample_rate=10.0))
        hub.add(SlowSensor(delay=10.0, sample_rate=10.0))

        start = time.monotonic()
        readings = hub.read_all()
        elapsed = time.monotonic() - start

        # Should return fast sensor reading within ~0.5s (timeout), not 10s
        assert elapsed < 2.0
        metrics = [r.metric for r in readings]
        assert "fast_value" in metrics
        assert "slow_value" not in metrics

    def test_per_sensor_timeout_override(self):
        """Sensor-level read_timeout should override hub default."""
        hub = SensorHub(default_timeout=10.0)
        slow = SlowSensor(delay=5.0, sample_rate=10.0)
        slow.read_timeout = 0.3
        hub.add(slow)

        start = time.monotonic()
        readings = hub.read_all()
        elapsed = time.monotonic() - start

        assert elapsed < 2.0
        assert len(readings) == 0


# ─── Failure tracking and degradation ────────────────────────────────────────


class TestFailureHandling:
    def test_sensor_disabled_after_repeated_failures(self):
        """Sensor should be disabled after enough consecutive failures."""
        hub = SensorHub(default_timeout=1.0)
        failing = FailingSensor(sample_rate=10.0)
        hub.add(failing)

        # Each read_all triggers a failure
        for _ in range(20):
            hub.read_all()

        assert failing._disabled is True

    def test_failure_counter_resets_on_success(self):
        """Consecutive failure counter should reset after a successful read."""
        hub = SensorHub(default_timeout=1.0)
        sensor = CountingSensor(fail_until=3, sample_rate=10.0)
        hub.add(sensor)

        # First 3 calls fail
        for _ in range(3):
            hub.read_all()
        assert sensor._consecutive_failures == 3

        # 4th call succeeds -> counter resets
        readings = hub.read_all()
        assert sensor._consecutive_failures == 0
        assert len(readings) == 1

    def test_sample_rate_halves_on_failures(self):
        """Sample rate should decrease after 5 consecutive failures."""
        hub = SensorHub(default_timeout=1.0)
        failing = FailingSensor(sample_rate=10.0)
        hub.add(failing)

        for _ in range(5):
            hub.read_all()

        assert failing.sample_rate == 5.0

    def test_sample_rate_restored_on_recovery(self):
        """Sample rate should restore to original after recovery."""
        hub = SensorHub(default_timeout=1.0)
        sensor = CountingSensor(fail_until=6, sample_rate=8.0)
        hub.add(sensor)

        # 6 failures -> rate halved at least once
        for _ in range(6):
            hub.read_all()
        assert sensor.sample_rate < 8.0

        # Success -> rate restored
        hub.read_all()
        assert sensor.sample_rate == 8.0


# ─── Validation ──────────────────────────────────────────────────────────────


class TestValidation:
    def test_validate_reading_filters(self):
        """validate_reading() should filter out invalid readings."""
        hub = SensorHub()
        hub.add(ValidatingSensor(sample_rate=10.0))

        readings = hub.read_all()
        values = [r.value for r in readings]
        assert -5.0 not in values
        assert 10.0 in values
        assert 20.0 in values

    def test_default_validate_reading_passes_all(self):
        """Base validate_reading() should accept everything."""
        hub = SensorHub()
        hub.add(FastSensor(sample_rate=10.0))

        readings = hub.read_all()
        assert len(readings) == 1


# ─── I2C permission error (Phase 4) ─────────────────────────────────────────


class TestI2CPermissionError:
    def test_permission_error_logged(self, caplog):
        """PermissionError on SMBus should produce a warning with instructions."""
        import logging

        with caplog.at_level(logging.WARNING):
            from unittest.mock import patch, MagicMock

            mock_smbus = MagicMock()
            mock_smbus.SMBus.side_effect = PermissionError("Permission denied")

            with patch.dict("sys.modules", {"smbus2": mock_smbus}):
                from plexus.sensors.auto import scan_i2c
                result = scan_i2c(bus=1)

        assert result == []
        assert any("Permission denied" in r.message for r in caplog.records)
        assert any("usermod" in r.message for r in caplog.records)
