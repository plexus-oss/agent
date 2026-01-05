"""
Base sensor class and utilities for Plexus sensor drivers.

All sensor drivers inherit from BaseSensor and implement the read() method.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
import time


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

    def __init__(self):
        self.sensors: List[BaseSensor] = []
        self._running = False
        self._on_reading: Optional[Callable[[SensorReading], None]] = None
        self._on_error: Optional[Callable[[BaseSensor, Exception], None]] = None

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
                sensor._error = str(e)
                if self._on_error:
                    self._on_error(sensor, e)

    def cleanup(self) -> None:
        """Clean up all sensors."""
        for sensor in self.sensors:
            try:
                sensor.cleanup()
            except Exception:
                pass

    def read_all(self) -> List[SensorReading]:
        """Read from all sensors once."""
        readings = []
        for sensor in self.sensors:
            try:
                sensor_readings = sensor.read()
                readings.extend(sensor_readings)
            except Exception as e:
                sensor._error = str(e)
                if self._on_error:
                    self._on_error(sensor, e)
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

                    for sensor in self.sensors:
                        sensor_id = id(sensor)
                        interval = 1.0 / sensor.sample_rate

                        if now - last_read[sensor_id] >= interval:
                            try:
                                readings = sensor.read()

                                # Batch all readings from this sensor together
                                # This sends one HTTP request with all metrics, enabling
                                # instant streaming updates in the frontend
                                batch_points = []
                                batch_timestamp = None
                                batch_tags = None

                                for reading in readings:
                                    metric = sensor.get_prefixed_metric(reading.metric)
                                    tags = {**sensor.tags, **reading.tags}
                                    batch_points.append((metric, reading.value))

                                    # Use first reading's timestamp and merge tags
                                    if batch_timestamp is None:
                                        batch_timestamp = reading.timestamp
                                        batch_tags = tags if tags else None

                                    if self._on_reading:
                                        self._on_reading(reading)

                                # Send all readings in one request
                                if batch_points:
                                    client.send_batch(
                                        batch_points,
                                        timestamp=batch_timestamp,
                                        tags=batch_tags,
                                    )

                                last_read[sensor_id] = now

                            except Exception as e:
                                sensor._error = str(e)
                                if self._on_error:
                                    self._on_error(sensor, e)

                    # Sleep to maintain timing
                    elapsed = time.time() - loop_start
                    if elapsed < min_interval:
                        time.sleep(min_interval - elapsed)

        finally:
            self.cleanup()

    def stop(self) -> None:
        """Stop the sensor hub."""
        self._running = False

    def on_reading(self, callback: Callable[[SensorReading], None]) -> "SensorHub":
        """Set callback for each reading."""
        self._on_reading = callback
        return self

    def on_error(self, callback: Callable[[BaseSensor, Exception], None]) -> "SensorHub":
        """Set callback for sensor errors."""
        self._on_error = callback
        return self

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
