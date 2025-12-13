"""
Base Protocol Adapter - Abstract interface for all protocol adapters

This module defines the interface that all protocol adapters must implement.
By following this interface, new protocols can be added without modifying
the core Plexus codebase.

Example custom adapter:

    from plexus.adapters import ProtocolAdapter, Metric, AdapterConfig

    class SerialAdapter(ProtocolAdapter):
        '''Adapter for serial port communication'''

        def __init__(self, port: str, baudrate: int = 9600, **kwargs):
            config = AdapterConfig(
                name="serial",
                params={"port": port, "baudrate": baudrate, **kwargs}
            )
            super().__init__(config)
            self.port = port
            self.baudrate = baudrate
            self._serial = None

        def connect(self) -> bool:
            import serial
            self._serial = serial.Serial(self.port, self.baudrate)
            return self._serial.is_open

        def disconnect(self) -> None:
            if self._serial:
                self._serial.close()

        def poll(self) -> list[Metric]:
            line = self._serial.readline().decode().strip()
            # Parse your protocol format
            metric, value = line.split(":")
            return [Metric(metric, float(value))]
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union
import time


class AdapterState(Enum):
    """Adapter connection state"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    RECONNECTING = "reconnecting"


class AdapterError(Exception):
    """Base exception for adapter errors"""
    pass


class ConnectionError(AdapterError):
    """Raised when connection fails"""
    pass


class ProtocolError(AdapterError):
    """Raised when protocol-specific error occurs"""
    pass


@dataclass
class AdapterConfig:
    """Configuration for a protocol adapter"""
    name: str
    params: Dict[str, Any] = field(default_factory=dict)

    # Connection settings
    auto_reconnect: bool = True
    reconnect_interval: float = 5.0
    max_reconnect_attempts: int = 10

    # Data settings
    batch_size: int = 100
    flush_interval: float = 1.0


@dataclass
class Metric:
    """
    A single metric data point.

    This is the universal format that all adapters produce.
    The Plexus client will convert these to the ingest API format.

    Args:
        name: Metric name (e.g., "temperature", "motor.rpm", "robot.state")
        value: The value - can be number, string, bool, dict, or list
        timestamp: Unix timestamp (seconds). If None, current time is used.
        tags: Optional key-value metadata
        device_id: Optional device identifier

    Examples:
        Metric("temperature", 72.5)
        Metric("robot.state", "MOVING")
        Metric("position", {"x": 1.5, "y": 2.3, "z": 0.0})
        Metric("joint_angles", [0.5, 1.2, -0.3, 0.0])
    """
    name: str
    value: Union[int, float, str, bool, Dict[str, Any], List[Any]]
    timestamp: Optional[float] = None
    tags: Optional[Dict[str, str]] = None
    device_id: Optional[str] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API submission"""
        result = {
            "metric": self.name,
            "value": self.value,
            "timestamp": self.timestamp,
        }
        if self.tags:
            result["tags"] = self.tags
        if self.device_id:
            result["device_id"] = self.device_id
        return result


# Type alias for data callback
DataCallback = Callable[[List[Metric]], None]


class ProtocolAdapter(ABC):
    """
    Abstract base class for protocol adapters.

    All protocol adapters must inherit from this class and implement
    the required abstract methods.

    Lifecycle:
        1. __init__() - Configure the adapter
        2. connect() - Establish connection
        3. run() or poll() - Receive data
        4. disconnect() - Clean up

    Two modes of operation:
        - Push mode: Override on_data() or pass callback to run()
        - Pull mode: Call poll() periodically to get data
    """

    def __init__(self, config: AdapterConfig):
        self.config = config
        self._state = AdapterState.DISCONNECTED
        self._error: Optional[str] = None
        self._metrics_received = 0
        self._last_data_time: Optional[float] = None
        self._on_data_callback: Optional[DataCallback] = None
        self._on_state_change: Optional[Callable[[AdapterState], None]] = None

    @property
    def state(self) -> AdapterState:
        """Current adapter state"""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Whether adapter is connected"""
        return self._state == AdapterState.CONNECTED

    @property
    def error(self) -> Optional[str]:
        """Last error message, if any"""
        return self._error

    @property
    def stats(self) -> Dict[str, Any]:
        """Adapter statistics"""
        return {
            "state": self._state.value,
            "metrics_received": self._metrics_received,
            "last_data_time": self._last_data_time,
            "error": self._error,
        }

    def _set_state(self, state: AdapterState, error: Optional[str] = None):
        """Update adapter state and notify listeners"""
        self._state = state
        self._error = error
        if self._on_state_change:
            self._on_state_change(state)

    def _emit_data(self, metrics: List[Metric]):
        """Emit data to callback"""
        if metrics:
            self._metrics_received += len(metrics)
            self._last_data_time = time.time()
            if self._on_data_callback:
                self._on_data_callback(metrics)

    # =========================================================================
    # Abstract methods - MUST be implemented by subclasses
    # =========================================================================

    @abstractmethod
    def connect(self) -> bool:
        """
        Establish connection to the data source.

        Returns:
            True if connection successful, False otherwise.

        Raises:
            ConnectionError: If connection fails with an error.
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """
        Close connection and clean up resources.

        This should be idempotent - calling it multiple times should be safe.
        """
        pass

    @abstractmethod
    def poll(self) -> List[Metric]:
        """
        Poll for new data (pull mode).

        Returns:
            List of Metric objects. Empty list if no new data.

        Raises:
            ProtocolError: If reading data fails.

        Note:
            For push-based protocols (like MQTT), this may return an empty
            list and data will arrive via the callback instead.
        """
        pass

    # =========================================================================
    # Optional methods - MAY be overridden by subclasses
    # =========================================================================

    def validate_config(self) -> bool:
        """
        Validate adapter configuration.

        Override this to add protocol-specific validation.

        Returns:
            True if config is valid.

        Raises:
            ValueError: If config is invalid.
        """
        return True

    def on_data(self, metrics: List[Metric]) -> None:
        """
        Handle incoming data (push mode).

        Override this for custom data handling, or pass a callback to run().

        Args:
            metrics: List of received metrics.
        """
        pass

    def run(
        self,
        on_data: Optional[DataCallback] = None,
        on_state_change: Optional[Callable[[AdapterState], None]] = None,
        blocking: bool = True,
    ) -> None:
        """
        Run the adapter (main loop for push-based protocols).

        Args:
            on_data: Callback for received data. If None, on_data() method is used.
            on_state_change: Callback for state changes.
            blocking: If True, blocks until stopped. If False, returns immediately.

        For pull-based protocols, this starts a polling loop.
        For push-based protocols, this starts listening for events.
        """
        self._on_data_callback = on_data
        self._on_state_change = on_state_change

        if not self.is_connected:
            if not self.connect():
                raise ConnectionError(f"Failed to connect: {self._error}")

        if blocking:
            self._run_loop()

    def _run_loop(self) -> None:
        """
        Default run loop implementation (polling mode).

        Override this for push-based protocols like MQTT.
        """
        try:
            while self.is_connected:
                metrics = self.poll()
                if metrics:
                    self._emit_data(metrics)
                    self.on_data(metrics)
                time.sleep(0.01)  # 100 Hz max
        except KeyboardInterrupt:
            pass
        finally:
            self.disconnect()

    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.disconnect()
        return False
