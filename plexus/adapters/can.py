"""
CAN Bus Adapter - CAN protocol support with DBC decoding

This adapter reads CAN bus data and emits both raw frames and decoded
signals when a DBC file is provided.

Requirements:
    pip install plexus-agent[can]
    # or
    pip install python-can cantools

Usage:
    from plexus.adapters import CANAdapter

    # Basic usage with virtual CAN
    adapter = CANAdapter(interface="socketcan", channel="vcan0")
    adapter.connect()
    for metric in adapter.poll():
        print(f"{metric.name}: {metric.value}")

    # With DBC file for signal decoding
    adapter = CANAdapter(
        interface="socketcan",
        channel="can0",
        dbc_path="/path/to/vehicle.dbc"
    )

Supported interfaces:
    - socketcan (Linux)
    - pcan (Peak CAN)
    - vector (Vector CANalyzer)
    - kvaser (Kvaser)
    - slcan (Serial CAN)
    - virtual (Testing)

Emitted metrics:
    - can.raw.0x{id} - Raw frame data as hex string
    - {signal_name} - Decoded signals from DBC (e.g., "engine_rpm", "coolant_temp")
"""

from typing import Any, Dict, List, Optional
import time
import logging

from plexus.adapters.base import (
    ProtocolAdapter,
    AdapterConfig,
    AdapterState,
    Metric,
    ConnectionError,
    ProtocolError,
)
from plexus.adapters.registry import register_adapter

logger = logging.getLogger(__name__)

# Optional dependencies — imported at module level so they can be
# mocked in tests with @patch("plexus.adapters.can.can") etc.
try:
    import can
except ImportError:
    can = None  # type: ignore[assignment]

try:
    import cantools
except ImportError:
    cantools = None  # type: ignore[assignment]


@register_adapter(
    "can",
    description="CAN bus adapter with DBC signal decoding",
    author="Plexus",
    version="1.0.0",
    requires=["python-can", "cantools"],
)
class CANAdapter(ProtocolAdapter):
    """
    CAN Bus protocol adapter.

    Reads CAN frames from a CAN interface and optionally decodes them
    using a DBC file.

    Args:
        interface: CAN interface type (socketcan, pcan, vector, etc.)
        channel: CAN channel (can0, vcan0, PCAN_USBBUS1, etc.)
        bitrate: CAN bitrate in bps (default: 500000)
        dbc_path: Path to DBC file for signal decoding (optional)
        emit_raw: Whether to emit raw frame metrics (default: True)
        emit_decoded: Whether to emit decoded signals (default: True)
        raw_prefix: Prefix for raw frame metrics (default: "can.raw")
        filters: List of CAN ID filters as dicts with 'can_id' and 'can_mask'
        receive_own_messages: Whether to receive own transmitted messages
        source_id: Source ID for metrics (optional)

    Example:
        adapter = CANAdapter(
            interface="socketcan",
            channel="can0",
            dbc_path="vehicle.dbc",
            bitrate=500000
        )

        with adapter:
            while True:
                for metric in adapter.poll():
                    print(f"{metric.name} = {metric.value}")
    """

    def __init__(
        self,
        interface: str = "socketcan",
        channel: str = "can0",
        bitrate: int = 500000,
        dbc_path: Optional[str] = None,
        emit_raw: bool = True,
        emit_decoded: bool = True,
        raw_prefix: str = "can.raw",
        filters: Optional[List[Dict[str, int]]] = None,
        receive_own_messages: bool = False,
        source_id: Optional[str] = None,
        **kwargs,
    ):
        config = AdapterConfig(
            name="can",
            params={
                "interface": interface,
                "channel": channel,
                "bitrate": bitrate,
                "dbc_path": dbc_path,
                **kwargs,
            },
        )
        super().__init__(config)

        self.interface = interface
        self.channel = channel
        self.bitrate = bitrate
        self.dbc_path = dbc_path
        self.emit_raw = emit_raw
        self.emit_decoded = emit_decoded
        self.raw_prefix = raw_prefix
        self.filters = filters
        self.receive_own_messages = receive_own_messages
        self._source_id = source_id

        self._bus: Optional[Any] = None  # can.Bus instance
        self._db: Optional[Any] = None   # cantools.Database instance
        self._message_cache: Dict[int, Any] = {}  # Cache DBC message lookups

    def validate_config(self) -> bool:
        """Validate adapter configuration."""
        if not self.channel:
            raise ValueError("CAN channel is required")

        valid_interfaces = [
            "socketcan", "pcan", "vector", "kvaser", "slcan",
            "virtual", "ixxat", "neovi", "nican", "iscan"
        ]
        if self.interface not in valid_interfaces:
            logger.warning(
                f"Unknown interface '{self.interface}'. "
                f"Valid interfaces: {', '.join(valid_interfaces)}"
            )

        return True

    def connect(self) -> bool:
        """Connect to CAN bus interface."""
        if can is None:
            self._set_state(AdapterState.ERROR, "python-can not installed")
            raise ConnectionError(
                "python-can is required. Install with: pip install plexus-agent[can]"
            )

        try:
            self._set_state(AdapterState.CONNECTING)
            logger.info(
                f"Connecting to CAN bus: {self.interface}:{self.channel} "
                f"at {self.bitrate} bps"
            )

            # Configure bus
            bus_kwargs = {
                "interface": self.interface,
                "channel": self.channel,
                "bitrate": self.bitrate,
                "receive_own_messages": self.receive_own_messages,
            }

            # Add filters if specified
            if self.filters:
                bus_kwargs["can_filters"] = self.filters

            self._bus = can.Bus(**bus_kwargs)

            # Load DBC file if provided
            if self.dbc_path:
                self._load_dbc(self.dbc_path)

            self._set_state(AdapterState.CONNECTED)
            logger.info(f"Connected to CAN bus: {self.channel}")
            return True

        except Exception as e:
            self._set_state(AdapterState.ERROR, str(e))
            logger.error(f"Failed to connect to CAN bus: {e}")
            raise ConnectionError(f"CAN connection failed: {e}")

    def _load_dbc(self, dbc_path: str) -> None:
        """Load a DBC file for signal decoding."""
        if cantools is None:
            logger.warning(
                "cantools not installed. DBC decoding disabled. "
                "Install with: pip install cantools"
            )
            self._db = None
            return

        try:
            logger.info(f"Loading DBC file: {dbc_path}")
            self._db = cantools.database.load_file(dbc_path)
            logger.info(
                f"Loaded DBC with {len(self._db.messages)} messages"
            )

            # Pre-cache message lookups by arbitration ID
            for msg in self._db.messages:
                self._message_cache[msg.frame_id] = msg

        except FileNotFoundError:
            logger.error(f"DBC file not found: {dbc_path}")
            self._db = None
        except Exception as e:
            logger.error(f"Failed to load DBC file: {e}")
            self._db = None

    def disconnect(self) -> None:
        """Disconnect from CAN bus."""
        if self._bus:
            try:
                self._bus.shutdown()
                logger.info("Disconnected from CAN bus")
            except Exception as e:
                logger.warning(f"Error shutting down CAN bus: {e}")
            finally:
                self._bus = None

        self._set_state(AdapterState.DISCONNECTED)

    def poll(self) -> List[Metric]:
        """
        Poll for CAN frames and return metrics.

        Returns:
            List of Metric objects for raw frames and/or decoded signals.
        """
        if not self._bus:
            return []

        metrics: List[Metric] = []

        try:
            # Non-blocking receive with short timeout
            message = self._bus.recv(timeout=0.1)

            if message is None:
                return []

            timestamp = message.timestamp if message.timestamp else time.time()

            # Emit raw frame metric
            if self.emit_raw:
                raw_metric = self._create_raw_metric(message, timestamp)
                metrics.append(raw_metric)

            # Decode and emit signal metrics
            if self.emit_decoded and self._db:
                decoded_metrics = self._decode_message(message, timestamp)
                metrics.extend(decoded_metrics)

        except Exception as e:
            logger.error(f"Error reading CAN frame: {e}")
            raise ProtocolError(f"CAN read error: {e}")

        return metrics

    def _create_raw_metric(self, message: Any, timestamp: float) -> Metric:
        """Create a raw frame metric."""
        # Format arbitration ID as hex
        arb_id = f"0x{message.arbitration_id:03X}"
        metric_name = f"{self.raw_prefix}.{arb_id}"

        # Format data as hex string
        data_hex = message.data.hex().upper()

        # Include metadata as tags
        tags = {
            "arbitration_id": str(message.arbitration_id),
            "dlc": str(message.dlc),
            "is_extended": str(message.is_extended_id).lower(),
        }

        if message.is_error_frame:
            tags["error_frame"] = "true"
        if message.is_remote_frame:
            tags["remote_frame"] = "true"

        return Metric(
            name=metric_name,
            value=data_hex,
            timestamp=timestamp,
            tags=tags,
            source_id=self._source_id,
        )

    def _decode_message(self, message: Any, timestamp: float) -> List[Metric]:
        """Decode CAN message using DBC and return signal metrics."""
        metrics: List[Metric] = []

        # Look up message in DBC
        dbc_message = self._message_cache.get(message.arbitration_id)
        if not dbc_message:
            return []

        try:
            # Decode all signals in the message
            decoded = dbc_message.decode(message.data)

            for signal_name, value in decoded.items():
                # Get signal info for units
                signal = dbc_message.get_signal_by_name(signal_name)
                tags = {
                    "can_id": f"0x{message.arbitration_id:03X}",
                    "dbc_message": dbc_message.name,
                }

                # Add unit if available
                if signal and signal.unit:
                    tags["unit"] = signal.unit

                metrics.append(
                    Metric(
                        name=signal_name,
                        value=value,
                        timestamp=timestamp,
                        tags=tags,
                        source_id=self._source_id,
                    )
                )

        except Exception as e:
            logger.debug(
                f"Could not decode message 0x{message.arbitration_id:03X}: {e}"
            )

        return metrics

    def send(
        self,
        arbitration_id: int,
        data: bytes,
        is_extended_id: bool = False,
    ) -> bool:
        """
        Send a CAN frame.

        Args:
            arbitration_id: CAN arbitration ID
            data: Frame data (1-8 bytes)
            is_extended_id: Whether to use extended (29-bit) ID

        Returns:
            True if sent successfully
        """
        if not self._bus:
            raise ProtocolError("Not connected to CAN bus")

        try:
            message = can.Message(
                arbitration_id=arbitration_id,
                data=data,
                is_extended_id=is_extended_id,
            )
            self._bus.send(message)
            logger.debug(f"Sent CAN frame: 0x{arbitration_id:03X} {data.hex()}")
            return True

        except Exception as e:
            logger.error(f"Failed to send CAN frame: {e}")
            raise ProtocolError(f"CAN send error: {e}")

    def send_signal(
        self,
        message_name: str,
        signals: Dict[str, float],
    ) -> bool:
        """
        Send a CAN message with encoded signals (requires DBC).

        Args:
            message_name: DBC message name
            signals: Dict of signal names to values

        Returns:
            True if sent successfully
        """
        if not self._db:
            raise ProtocolError("DBC file required for signal encoding")

        try:
            # Find message in DBC
            dbc_message = self._db.get_message_by_name(message_name)
            data = dbc_message.encode(signals)

            return self.send(dbc_message.frame_id, data)

        except Exception as e:
            logger.error(f"Failed to send CAN signal: {e}")
            raise ProtocolError(f"Signal encoding error: {e}")

    @property
    def stats(self) -> Dict[str, Any]:
        """Get adapter statistics including CAN-specific info."""
        base_stats = super().stats
        base_stats.update({
            "interface": self.interface,
            "channel": self.channel,
            "bitrate": self.bitrate,
            "dbc_loaded": self._db is not None,
            "dbc_messages": len(self._message_cache) if self._db else 0,
        })
        return base_stats
