"""
MAVLink Adapter - MAVLink protocol support for drones and autonomous vehicles

This adapter connects to MAVLink-speaking vehicles (ArduPilot, PX4, etc.)
and emits decoded telemetry as Plexus metrics.

Requirements:
    pip install plexus-agent[mavlink]
    # or
    pip install pymavlink

Usage:
    from plexus.adapters import MAVLinkAdapter

    # Connect to SITL or companion computer (UDP)
    adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")
    adapter.connect()
    for metric in adapter.poll():
        print(f"{metric.name}: {metric.value}")

    # Connect to flight controller over serial
    adapter = MAVLinkAdapter(
        connection_string="/dev/ttyACM0",
        baud=57600,
    )

Supported connections:
    - udpin:host:port  (listen for UDP, e.g. from SITL or GCS)
    - udpout:host:port (send UDP to a target)
    - tcp:host:port    (TCP client)
    - tcpin:host:port  (TCP server)
    - /dev/ttyXXX      (serial port)

Emitted metrics:
    - attitude.roll, attitude.pitch, attitude.yaw (degrees)
    - gps.lat, gps.lon, gps.alt (degrees, meters)
    - battery.voltage, battery.current, battery.remaining
    - hud.airspeed, hud.groundspeed, hud.heading, hud.throttle, hud.climb
    - mavlink.raw.{MSG_TYPE} - Raw message dict (when emit_raw=True)
"""

from typing import Any, Dict, List, Optional, Set
import re
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

# Optional dependency — imported at module level so it can be
# mocked in tests with @patch("plexus.adapters.mavlink.mavutil")
try:
    from pymavlink import mavutil
except ImportError:
    mavutil = None  # type: ignore[assignment]

# Valid connection string patterns
_CONNECTION_PATTERNS = [
    re.compile(r"^udpin:"),
    re.compile(r"^udpout:"),
    re.compile(r"^udp:"),
    re.compile(r"^tcp:"),
    re.compile(r"^tcpin:"),
    re.compile(r"^/dev/"),
    re.compile(r"^COM\d+$", re.IGNORECASE),
]


@register_adapter(
    "mavlink",
    description="MAVLink adapter for drones and autonomous vehicles",
    author="Plexus",
    version="1.0.0",
    requires=["pymavlink"],
)
class MAVLinkAdapter(ProtocolAdapter):
    """
    MAVLink protocol adapter.

    Connects to MAVLink-speaking vehicles and emits telemetry as metrics.

    Args:
        connection_string: MAVLink connection string (default: "udpin:0.0.0.0:14550")
        baud: Serial baud rate (default: 57600)
        source_system: MAVLink system ID for this adapter (default: 255)
        source_component: MAVLink component ID (default: 0)
        dialect: MAVLink dialect (default: "ardupilotmega")
        dialect_path: Custom dialect XML path (optional)
        include_messages: Only process these message types (optional)
        exclude_messages: Skip these message types (optional)
        emit_raw: Whether to emit raw message dicts (default: False)
        emit_decoded: Whether to emit decoded metrics (default: True)
        raw_prefix: Prefix for raw metrics (default: "mavlink.raw")
        request_streams: Whether to request data streams on connect (default: True)
        stream_rate_hz: Requested data stream rate (default: 4)
        source_id: Source ID for metrics (optional)

    Example:
        adapter = MAVLinkAdapter(
            connection_string="udpin:0.0.0.0:14550",
            include_messages=["ATTITUDE", "GPS_RAW_INT"],
        )

        with adapter:
            while True:
                for metric in adapter.poll():
                    print(f"{metric.name} = {metric.value}")
    """

    def __init__(
        self,
        connection_string: str = "udpin:0.0.0.0:14550",
        baud: int = 57600,
        source_system: int = 255,
        source_component: int = 0,
        dialect: str = "ardupilotmega",
        dialect_path: Optional[str] = None,
        include_messages: Optional[List[str]] = None,
        exclude_messages: Optional[List[str]] = None,
        emit_raw: bool = False,
        emit_decoded: bool = True,
        raw_prefix: str = "mavlink.raw",
        request_streams: bool = True,
        stream_rate_hz: int = 4,
        source_id: Optional[str] = None,
        **kwargs,
    ):
        config = AdapterConfig(
            name="mavlink",
            params={
                "connection_string": connection_string,
                "baud": baud,
                "source_system": source_system,
                "source_component": source_component,
                "dialect": dialect,
                **kwargs,
            },
        )
        super().__init__(config)

        self.connection_string = connection_string
        self.baud = baud
        self.source_system = source_system
        self.source_component = source_component
        self.dialect = dialect
        self.dialect_path = dialect_path
        self.include_messages: Optional[Set[str]] = (
            set(include_messages) if include_messages else None
        )
        self.exclude_messages: Set[str] = set(exclude_messages) if exclude_messages else set()
        self.emit_raw = emit_raw
        self.emit_decoded = emit_decoded
        self.raw_prefix = raw_prefix
        self.request_streams = request_streams
        self.stream_rate_hz = stream_rate_hz
        self._source_id = source_id

        self._conn: Optional[Any] = None  # mavutil.mavlink_connection instance
        self._vehicle_sysid: Optional[int] = None
        self._vehicle_compid: Optional[int] = None

    def validate_config(self) -> bool:
        """Validate adapter configuration."""
        if not self.connection_string:
            raise ValueError("MAVLink connection_string is required")

        if not any(p.match(self.connection_string) for p in _CONNECTION_PATTERNS):
            raise ValueError(
                f"Invalid connection string '{self.connection_string}'. "
                f"Expected format: udpin:host:port, tcp:host:port, /dev/ttyXXX, or COMx"
            )

        return True

    def connect(self) -> bool:
        """Connect to MAVLink vehicle."""
        if mavutil is None:
            self._set_state(AdapterState.ERROR, "pymavlink not installed")
            raise ConnectionError(
                "pymavlink is required. Install with: pip install plexus-agent[mavlink]"
            )

        try:
            self._set_state(AdapterState.CONNECTING)
            logger.info(f"Connecting to MAVLink: {self.connection_string}")

            # Set dialect if custom path provided
            if self.dialect_path:
                import os
                os.environ["MAVLINK20"] = "1"
                mavutil.set_dialect(self.dialect)

            # Create connection
            self._conn = mavutil.mavlink_connection(
                self.connection_string,
                baud=self.baud,
                source_system=self.source_system,
                source_component=self.source_component,
                dialect=self.dialect,
            )

            # Wait for heartbeat (confirms vehicle is alive)
            logger.info("Waiting for heartbeat...")
            msg = self._conn.wait_heartbeat(timeout=10)
            if msg is None:
                self._set_state(AdapterState.ERROR, "No heartbeat received")
                raise ConnectionError(
                    "No heartbeat received within 10s. Is the vehicle connected?"
                )

            self._vehicle_sysid = msg.get_srcSystem()
            self._vehicle_compid = msg.get_srcComponent()
            logger.info(
                f"Heartbeat from system {self._vehicle_sysid}, "
                f"component {self._vehicle_compid}"
            )

            # Request data streams
            if self.request_streams:
                self._request_data_streams()

            self._set_state(AdapterState.CONNECTED)
            logger.info(f"Connected to MAVLink vehicle (sysid={self._vehicle_sysid})")
            return True

        except ConnectionError:
            raise
        except Exception as e:
            self._set_state(AdapterState.ERROR, str(e))
            logger.error(f"Failed to connect to MAVLink: {e}")
            raise ConnectionError(f"MAVLink connection failed: {e}")

    def _request_data_streams(self) -> None:
        """Request data streams from the vehicle."""
        if not self._conn:
            return

        try:
            self._conn.mav.request_data_stream_send(
                self._conn.target_system,
                self._conn.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL,
                self.stream_rate_hz,
                1,  # start sending
            )
            logger.debug(f"Requested all data streams at {self.stream_rate_hz} Hz")
        except Exception as e:
            logger.warning(f"Failed to request data streams: {e}")

    def disconnect(self) -> None:
        """Disconnect from MAVLink vehicle."""
        if self._conn:
            try:
                self._conn.close()
                logger.info("Disconnected from MAVLink")
            except Exception as e:
                logger.warning(f"Error closing MAVLink connection: {e}")
            finally:
                self._conn = None

        self._vehicle_sysid = None
        self._vehicle_compid = None
        self._set_state(AdapterState.DISCONNECTED)

    def poll(self) -> List[Metric]:
        """
        Poll for MAVLink messages and return metrics.

        Returns:
            List of Metric objects for raw messages and/or decoded telemetry.
        """
        if not self._conn:
            return []

        metrics: List[Metric] = []

        try:
            msg = self._conn.recv_match(blocking=False)

            if msg is None:
                return []

            msg_type = msg.get_type()

            # Skip bad data
            if msg_type == "BAD_DATA":
                return []

            # Apply filters
            if self.include_messages and msg_type not in self.include_messages:
                return []
            if msg_type in self.exclude_messages:
                return []

            timestamp = time.time()
            tags = self._base_tags(msg)

            # Emit raw message metric
            if self.emit_raw:
                raw_metric = self._create_raw_metric(msg, msg_type, timestamp, tags)
                metrics.append(raw_metric)

            # Emit decoded metrics
            if self.emit_decoded:
                decoded_metrics = self._decode_message(msg, msg_type, timestamp, tags)
                metrics.extend(decoded_metrics)

        except Exception as e:
            logger.error(f"Error reading MAVLink message: {e}")
            raise ProtocolError(f"MAVLink read error: {e}")

        return metrics

    def _base_tags(self, msg: Any) -> Dict[str, str]:
        """Create base tags for a message."""
        tags: Dict[str, str] = {
            "message_type": msg.get_type(),
        }
        try:
            tags["message_id"] = str(msg.get_msgId())
            tags["system_id"] = str(msg.get_srcSystem())
            tags["component_id"] = str(msg.get_srcComponent())
        except Exception:
            pass
        return tags

    def _create_raw_metric(
        self, msg: Any, msg_type: str, timestamp: float, tags: Dict[str, str]
    ) -> Metric:
        """Create a raw message metric containing the full message dict."""
        metric_name = f"{self.raw_prefix}.{msg_type}"

        # Convert message to dict
        msg_dict = msg.to_dict()
        msg_dict.pop("mavpackettype", None)

        return Metric(
            name=metric_name,
            value=msg_dict,
            timestamp=timestamp,
            tags=tags,
            source_id=self._source_id,
        )

    def _decode_message(
        self, msg: Any, msg_type: str, timestamp: float, tags: Dict[str, str]
    ) -> List[Metric]:
        """Decode a MAVLink message into human-readable metrics."""
        decoder = _MESSAGE_DECODERS.get(msg_type)
        if not decoder:
            return []

        try:
            return decoder(self, msg, timestamp, tags)
        except Exception as e:
            logger.debug(f"Could not decode {msg_type}: {e}")
            return []

    def _metric(
        self,
        name: str,
        value: Any,
        timestamp: float,
        tags: Dict[str, str],
        unit: Optional[str] = None,
    ) -> Metric:
        """Helper to create a metric with optional unit tag."""
        metric_tags = dict(tags)
        if unit:
            metric_tags["unit"] = unit
        return Metric(
            name=name,
            value=value,
            timestamp=timestamp,
            tags=metric_tags,
            source_id=self._source_id,
        )

    # =========================================================================
    # Command sending
    # =========================================================================

    def send_command(
        self,
        command: int,
        param1: float = 0,
        param2: float = 0,
        param3: float = 0,
        param4: float = 0,
        param5: float = 0,
        param6: float = 0,
        param7: float = 0,
    ) -> bool:
        """
        Send a MAVLink command (COMMAND_LONG).

        Args:
            command: MAVLink command ID (MAV_CMD_*)
            param1-param7: Command parameters

        Returns:
            True if sent successfully
        """
        if not self._conn:
            raise ProtocolError("Not connected to MAVLink vehicle")

        try:
            self._conn.mav.command_long_send(
                self._conn.target_system,
                self._conn.target_component,
                command,
                0,  # confirmation
                param1, param2, param3, param4, param5, param6, param7,
            )
            logger.debug(f"Sent command {command}")
            return True
        except Exception as e:
            logger.error(f"Failed to send command: {e}")
            raise ProtocolError(f"MAVLink command error: {e}")

    def arm(self, force: bool = False) -> bool:
        """Arm the vehicle."""
        if mavutil is None:
            raise ProtocolError("pymavlink not installed")
        return self.send_command(
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            param1=1,
            param2=21196 if force else 0,
        )

    def disarm(self, force: bool = False) -> bool:
        """Disarm the vehicle."""
        if mavutil is None:
            raise ProtocolError("pymavlink not installed")
        return self.send_command(
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            param1=0,
            param2=21196 if force else 0,
        )

    def set_mode(self, mode: str) -> bool:
        """
        Set the vehicle flight mode by name.

        Args:
            mode: Mode name (e.g., "STABILIZE", "GUIDED", "AUTO", "LOITER")

        Returns:
            True if sent successfully
        """
        if not self._conn:
            raise ProtocolError("Not connected to MAVLink vehicle")

        try:
            mode_id = self._conn.mode_mapping().get(mode.upper())
            if mode_id is None:
                available = list(self._conn.mode_mapping().keys())
                raise ValueError(
                    f"Unknown mode '{mode}'. Available: {', '.join(available)}"
                )

            self._conn.set_mode(mode_id)
            logger.debug(f"Set mode to {mode} (id={mode_id})")
            return True
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to set mode: {e}")
            raise ProtocolError(f"MAVLink set_mode error: {e}")

    @property
    def stats(self) -> Dict[str, Any]:
        """Get adapter statistics including MAVLink-specific info."""
        base_stats = super().stats
        base_stats.update({
            "connection_string": self.connection_string,
            "baud": self.baud,
            "vehicle_sysid": self._vehicle_sysid,
            "vehicle_compid": self._vehicle_compid,
            "dialect": self.dialect,
        })
        return base_stats


# =============================================================================
# Message Decoders
# =============================================================================
# Each decoder takes (adapter, msg, timestamp, tags) and returns List[Metric].

def _decode_heartbeat(adapter: MAVLinkAdapter, msg, ts, tags) -> List[Metric]:
    return [
        adapter._metric("heartbeat.type", msg.type, ts, tags),
        adapter._metric("heartbeat.autopilot", msg.autopilot, ts, tags),
        adapter._metric("heartbeat.base_mode", msg.base_mode, ts, tags),
        adapter._metric("heartbeat.custom_mode", msg.custom_mode, ts, tags),
        adapter._metric("heartbeat.system_status", msg.system_status, ts, tags),
    ]


def _decode_attitude(adapter: MAVLinkAdapter, msg, ts, tags) -> List[Metric]:
    import math
    return [
        adapter._metric("attitude.roll", round(math.degrees(msg.roll), 2), ts, tags, "deg"),
        adapter._metric("attitude.pitch", round(math.degrees(msg.pitch), 2), ts, tags, "deg"),
        adapter._metric("attitude.yaw", round(math.degrees(msg.yaw), 2), ts, tags, "deg"),
        adapter._metric("attitude.rollspeed", round(math.degrees(msg.rollspeed), 2), ts, tags, "deg/s"),
        adapter._metric("attitude.pitchspeed", round(math.degrees(msg.pitchspeed), 2), ts, tags, "deg/s"),
        adapter._metric("attitude.yawspeed", round(math.degrees(msg.yawspeed), 2), ts, tags, "deg/s"),
    ]


def _decode_gps_raw_int(adapter: MAVLinkAdapter, msg, ts, tags) -> List[Metric]:
    return [
        adapter._metric("gps.lat", msg.lat / 1e7, ts, tags, "deg"),
        adapter._metric("gps.lon", msg.lon / 1e7, ts, tags, "deg"),
        adapter._metric("gps.alt", msg.alt / 1000.0, ts, tags, "m"),
        adapter._metric("gps.fix_type", msg.fix_type, ts, tags),
        adapter._metric("gps.satellites", msg.satellites_visible, ts, tags),
        adapter._metric("gps.eph", msg.eph / 100.0, ts, tags, "m"),
        adapter._metric("gps.epv", msg.epv / 100.0, ts, tags, "m"),
        adapter._metric("gps.vel", msg.vel / 100.0, ts, tags, "m/s"),
    ]


def _decode_sys_status(adapter: MAVLinkAdapter, msg, ts, tags) -> List[Metric]:
    return [
        adapter._metric("sys.voltage", msg.voltage_battery / 1000.0, ts, tags, "V"),
        adapter._metric("sys.current", msg.current_battery / 100.0, ts, tags, "A"),
        adapter._metric("sys.battery_remaining", msg.battery_remaining, ts, tags, "%"),
        adapter._metric("sys.drop_rate", msg.drop_rate_comm / 100.0, ts, tags, "%"),
        adapter._metric("sys.errors_comm", msg.errors_comm, ts, tags),
    ]


def _decode_vfr_hud(adapter: MAVLinkAdapter, msg, ts, tags) -> List[Metric]:
    return [
        adapter._metric("hud.airspeed", round(msg.airspeed, 2), ts, tags, "m/s"),
        adapter._metric("hud.groundspeed", round(msg.groundspeed, 2), ts, tags, "m/s"),
        adapter._metric("hud.heading", msg.heading, ts, tags, "deg"),
        adapter._metric("hud.throttle", msg.throttle, ts, tags, "%"),
        adapter._metric("hud.alt", round(msg.alt, 2), ts, tags, "m"),
        adapter._metric("hud.climb", round(msg.climb, 2), ts, tags, "m/s"),
    ]


def _decode_rc_channels(adapter: MAVLinkAdapter, msg, ts, tags) -> List[Metric]:
    metrics = [
        adapter._metric("rc.rssi", msg.rssi, ts, tags),
        adapter._metric("rc.chancount", msg.chancount, ts, tags),
    ]
    # Emit first 8 channels (most common)
    for i in range(1, min(9, msg.chancount + 1)):
        val = getattr(msg, f"chan{i}_raw", 65535)
        if val != 65535:  # 65535 = unused
            metrics.append(
                adapter._metric(f"rc.ch{i}", val, ts, tags, "us")
            )
    return metrics


def _decode_battery_status(adapter: MAVLinkAdapter, msg, ts, tags) -> List[Metric]:
    metrics = [
        adapter._metric("battery.current", msg.current_battery / 100.0, ts, tags, "A"),
        adapter._metric("battery.consumed", msg.current_consumed, ts, tags, "mAh"),
        adapter._metric("battery.remaining", msg.battery_remaining, ts, tags, "%"),
        adapter._metric("battery.temperature", msg.temperature / 100.0, ts, tags, "degC"),
    ]
    # First cell voltage (if present)
    if msg.voltages[0] != 65535:
        total_mv = sum(v for v in msg.voltages if v != 65535)
        metrics.append(
            adapter._metric("battery.voltage", total_mv / 1000.0, ts, tags, "V")
        )
    return metrics


def _decode_global_position_int(adapter: MAVLinkAdapter, msg, ts, tags) -> List[Metric]:
    return [
        adapter._metric("position.lat", msg.lat / 1e7, ts, tags, "deg"),
        adapter._metric("position.lon", msg.lon / 1e7, ts, tags, "deg"),
        adapter._metric("position.alt", msg.alt / 1000.0, ts, tags, "m"),
        adapter._metric("position.relative_alt", msg.relative_alt / 1000.0, ts, tags, "m"),
        adapter._metric("position.vx", msg.vx / 100.0, ts, tags, "m/s"),
        adapter._metric("position.vy", msg.vy / 100.0, ts, tags, "m/s"),
        adapter._metric("position.vz", msg.vz / 100.0, ts, tags, "m/s"),
        adapter._metric("position.heading", msg.hdg / 100.0, ts, tags, "deg"),
    ]


def _decode_local_position_ned(adapter: MAVLinkAdapter, msg, ts, tags) -> List[Metric]:
    return [
        adapter._metric("local.x", round(msg.x, 3), ts, tags, "m"),
        adapter._metric("local.y", round(msg.y, 3), ts, tags, "m"),
        adapter._metric("local.z", round(msg.z, 3), ts, tags, "m"),
        adapter._metric("local.vx", round(msg.vx, 3), ts, tags, "m/s"),
        adapter._metric("local.vy", round(msg.vy, 3), ts, tags, "m/s"),
        adapter._metric("local.vz", round(msg.vz, 3), ts, tags, "m/s"),
    ]


def _decode_servo_output_raw(adapter: MAVLinkAdapter, msg, ts, tags) -> List[Metric]:
    metrics = []
    for i in range(1, 9):
        val = getattr(msg, f"servo{i}_raw", 0)
        if val > 0:
            metrics.append(
                adapter._metric(f"servo.ch{i}", val, ts, tags, "us")
            )
    return metrics


def _decode_statustext(adapter: MAVLinkAdapter, msg, ts, tags) -> List[Metric]:
    text = msg.text.rstrip("\x00") if hasattr(msg.text, "rstrip") else str(msg.text)
    return [
        adapter._metric("status.text", text, ts, tags),
        adapter._metric("status.severity", msg.severity, ts, tags),
    ]


# Decoder lookup table
_MESSAGE_DECODERS = {
    "HEARTBEAT": _decode_heartbeat,
    "ATTITUDE": _decode_attitude,
    "GPS_RAW_INT": _decode_gps_raw_int,
    "SYS_STATUS": _decode_sys_status,
    "VFR_HUD": _decode_vfr_hud,
    "RC_CHANNELS": _decode_rc_channels,
    "BATTERY_STATUS": _decode_battery_status,
    "GLOBAL_POSITION_INT": _decode_global_position_int,
    "LOCAL_POSITION_NED": _decode_local_position_ned,
    "SERVO_OUTPUT_RAW": _decode_servo_output_raw,
    "STATUSTEXT": _decode_statustext,
}
