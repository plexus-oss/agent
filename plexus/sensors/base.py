"""
Base sensor class and utilities for Plexus sensor drivers.

All sensor drivers inherit from BaseSensor and implement the read() method.
"""

import logging
import sys
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class SensorReading:
    """A single sensor reading with metric name and value."""
    metric: str
    value: Any
    timestamp: float = field(default_factory=time.time)
    tags: Dict[str, str] = field(default_factory=dict)


class BaseSensor(ABC):
    """
    Base class for all sensor drivers.

    Subclasses must implement:
    - read() -> List[SensorReading]: Read current sensor values
    - name: Human-readable sensor name
    - metrics: List of metric names this sensor provides

    Optional overrides:
    - setup(): Initialize the sensor (called once)
    - cleanup(): Clean up resources (called on stop)
    - is_available(): Check if sensor is connected
    """

    # Sensor metadata (override in subclass)
    name: str = "Unknown Sensor"
    description: str = ""
    metrics: List[str] = []

    # I2C address(es) for auto-detection
    i2c_addresses: List[int] = []

    # Per-sensor read timeout (seconds). None = use SensorHub default.
    read_timeout: Optional[float] = None

    def __init__(
        self,
        sample_rate: float = 10.0,
        prefix: str = "",
        tags: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize the sensor driver.

        Args:
            sample_rate: Readings per second (Hz). Default 10 Hz.
            prefix: Prefix for metric names (e.g., "robot1." -> "robot1.accel_x")
            tags: Tags to add to all readings from this sensor
        """
        self.sample_rate = sample_rate
        self.prefix = prefix
        self.tags = tags or {}
        self._running = False
        self._error: Optional[str] = None
        self._consecutive_failures = 0
        self._disabled = False
        self._original_sample_rate = sample_rate

    def validate_reading(self, reading: "SensorReading") -> bool:
        """
        Validate a sensor reading. Override in subclasses for domain checks.

        Returns:
            True if the reading is valid
        """
        return True

    @abstractmethod
    def read(self) -> List[SensorReading]:
        """
        Read current sensor values.

        Returns:
            List of SensorReading objects with current values
        """
        pass

    def setup(self) -> None:
        """
        Initialize the sensor hardware.
        Called once before reading starts.
        Override in subclass if needed.
        """
        pass

    def cleanup(self) -> None:
        """
        Clean up sensor resources.
        Called when sensor is stopped.
        Override in subclass if needed.
        """
        pass

    def is_available(self) -> bool:
        """
        Check if the sensor is connected and responding.

        Returns:
            True if sensor is available
        """
        try:
            self.read()
            return True
        except Exception:
            return False

    def get_prefixed_metric(self, metric: str) -> str:
        """Get metric name with prefix applied."""
        if self.prefix:
            return f"{self.prefix}{metric}"
        return metric

    def get_info(self) -> Dict[str, Any]:
        """Get sensor information for display."""
        return {
            "name": self.name,
            "description": self.description,
            "metrics": self.metrics,
            "sample_rate": self.sample_rate,
            "prefix": self.prefix,
            "available": self.is_available(),
        }


class SensorHub:
    """
    Manages multiple sensors and streams their data to Plexus.

    Usage:
        from plexus import Plexus
        from plexus.sensors import SensorHub, MPU6050, BME280

        hub = SensorHub()
        hub.add(MPU6050())
        hub.add(BME280())
        hub.run(Plexus())  # Streams forever
    """

    def __init__(
        self,
        default_timeout: float = 5.0,
        max_workers: Optional[int] = None,
    ):
        """
        Args:
            default_timeout: Default per-sensor read timeout in seconds.
            max_workers: Max threads for concurrent reads. None = number of sensors.
        """
        self.sensors: List[BaseSensor] = []
        self._running = False
        self.default_timeout = default_timeout
        self.max_workers = max_workers
        self.error_report_fn: Optional[Any] = None  # async fn(source, error, severity)

    def add(self, sensor: BaseSensor) -> "SensorHub":
        """Add a sensor to the hub."""
        self.sensors.append(sensor)
        return self

    def remove(self, sensor: BaseSensor) -> "SensorHub":
        """Remove a sensor from the hub."""
        self.sensors.remove(sensor)
        return self

    def setup(self) -> None:
        """Initialize all sensors."""
        for sensor in self.sensors:
            try:
                sensor.setup()
            except Exception as e:
                logger.warning(f"Failed to setup {sensor.name}: {e}")
                sensor._error = str(e)

    def cleanup(self) -> None:
        """Clean up all sensors."""
        for sensor in self.sensors:
            try:
                sensor.cleanup()
            except Exception:
                pass

    def _get_timeout(self, sensor: BaseSensor) -> float:
        """Get the effective timeout for a sensor."""
        return sensor.read_timeout if sensor.read_timeout is not None else self.default_timeout

    def _handle_sensor_failure(self, sensor: BaseSensor) -> None:
        """Track consecutive failures and degrade gracefully."""
        sensor._consecutive_failures += 1
        if sensor._consecutive_failures >= 5 and not sensor._disabled:
            new_rate = sensor.sample_rate / 2.0
            if new_rate < 0.1:
                sensor._disabled = True
                logger.warning(
                    "%s disabled after %d consecutive failures",
                    sensor.name, sensor._consecutive_failures,
                )
                self._report_sensor_error(
                    sensor,
                    f"Disabled after {sensor._consecutive_failures} consecutive failures",
                    "error",
                )
            else:
                sensor.sample_rate = new_rate
                logger.warning(
                    "%s: %d consecutive failures, reducing poll rate to %.2f Hz",
                    sensor.name, sensor._consecutive_failures, new_rate,
                )

    def _report_sensor_error(self, sensor: BaseSensor, error: str, severity: str) -> None:
        """Report sensor error to dashboard if error_report_fn is set."""
        if self.error_report_fn:
            import asyncio
            try:
                coro = self.error_report_fn(f"sensor.{sensor.name}", error, severity)
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(coro)
                else:
                    loop.run_until_complete(coro)
            except Exception:
                pass

    def _handle_sensor_success(self, sensor: BaseSensor) -> None:
        """Reset failure tracking on successful read."""
        if sensor._consecutive_failures > 0:
            sensor._consecutive_failures = 0
            if sensor.sample_rate != sensor._original_sample_rate:
                sensor.sample_rate = sensor._original_sample_rate
                logger.info(
                    "%s recovered, restoring poll rate to %.2f Hz",
                    sensor.name, sensor._original_sample_rate,
                )

    def read_all(self) -> List[SensorReading]:
        """Read from all sensors concurrently with per-sensor timeouts."""
        active = [s for s in self.sensors if not s._disabled]
        if not active:
            return []

        readings = []
        workers = self.max_workers or len(active)
        pool = ThreadPoolExecutor(max_workers=workers)

        try:
            futures = {pool.submit(s.read): s for s in active}
            for future in futures:
                sensor = futures[future]
                timeout = self._get_timeout(sensor)
                try:
                    sensor_readings = future.result(timeout=timeout)
                    validated = [r for r in sensor_readings if sensor.validate_reading(r)]
                    readings.extend(validated)
                    self._handle_sensor_success(sensor)
                except FuturesTimeoutError:
                    logger.warning("Timeout reading %s (%.1fs)", sensor.name, timeout)
                    sensor._error = f"timeout ({timeout}s)"
                    future.cancel()
                    self._handle_sensor_failure(sensor)
                except Exception as e:
                    logger.debug(f"Read error from {sensor.name}: {e}")
                    sensor._error = str(e)
                    self._handle_sensor_failure(sensor)
        finally:
            if sys.version_info >= (3, 9):
                pool.shutdown(wait=False, cancel_futures=True)
            else:
                pool.shutdown(wait=False)

        return readings

    def run(
        self,
        client,  # Plexus client
        session_id: Optional[str] = None,
    ) -> None:
        """
        Run the sensor hub, streaming data to Plexus.

        Args:
            client: Plexus client instance
            session_id: Optional session ID for grouping data
        """
        self.setup()
        self._running = True

        # Find the fastest sensor to determine loop timing
        max_rate = max(s.sample_rate for s in self.sensors) if self.sensors else 10.0
        min_interval = 1.0 / max_rate

        # Track last read time per sensor
        last_read = {id(s): 0.0 for s in self.sensors}

        try:
            context = client.session(session_id) if session_id else _nullcontext()

            with context:
                while self._running:
                    loop_start = time.time()
                    now = loop_start

                    # Collect sensors that are due for a read
                    due_sensors = []
                    for sensor in self.sensors:
                        if sensor._disabled:
                            continue
                        sensor_id = id(sensor)
                        interval = 1.0 / sensor.sample_rate
                        if now - last_read[sensor_id] >= interval:
                            due_sensors.append(sensor)

                    if due_sensors:
                        # Read all due sensors concurrently
                        workers = self.max_workers or len(due_sensors)
                        pool = ThreadPoolExecutor(max_workers=workers)
                        try:
                            futures = {pool.submit(s.read): s for s in due_sensors}
                            for future in futures:
                                sensor = futures[future]
                                timeout = self._get_timeout(sensor)
                                try:
                                    readings = future.result(timeout=timeout)
                                    validated = [r for r in readings if sensor.validate_reading(r)]
                                    self._handle_sensor_success(sensor)

                                    batch_points = []
                                    batch_timestamp = None
                                    batch_tags = None

                                    for reading in validated:
                                        metric = sensor.get_prefixed_metric(reading.metric)
                                        tags = {**sensor.tags, **reading.tags}
                                        batch_points.append((metric, reading.value))

                                        if batch_timestamp is None:
                                            batch_timestamp = reading.timestamp
                                            batch_tags = tags if tags else None

                                    if batch_points:
                                        client.send_batch(
                                            batch_points,
                                            timestamp=batch_timestamp,
                                            tags=batch_tags,
                                        )

                                    last_read[id(sensor)] = now

                                except FuturesTimeoutError:
                                    logger.warning("Timeout reading %s (%.1fs)", sensor.name, timeout)
                                    sensor._error = f"timeout ({timeout}s)"
                                    future.cancel()
                                    self._handle_sensor_failure(sensor)
                                    last_read[id(sensor)] = now
                                except Exception as e:
                                    sensor._error = str(e)
                                    self._handle_sensor_failure(sensor)
                                    last_read[id(sensor)] = now
                        finally:
                            if sys.version_info >= (3, 9):
                                pool.shutdown(wait=False, cancel_futures=True)
                            else:
                                pool.shutdown(wait=False)

                    # Sleep to maintain timing
                    elapsed = time.time() - loop_start
                    if elapsed < min_interval:
                        time.sleep(min_interval - elapsed)

        finally:
            self.cleanup()

    def stop(self) -> None:
        """Stop the sensor hub."""
        self._running = False

    def get_info(self) -> List[Dict[str, Any]]:
        """Get info about all sensors."""
        return [s.get_info() for s in self.sensors]

    def get_sensor(self, name: str) -> Optional[BaseSensor]:
        """Get a sensor by name."""
        for sensor in self.sensors:
            if sensor.name == name:
                return sensor
        return None


class _nullcontext:
    """Null context manager for Python 3.8 compatibility."""
    def __enter__(self):
        return None
    def __exit__(self, *args):
        return False
