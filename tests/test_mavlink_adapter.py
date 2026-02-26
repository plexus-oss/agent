"""
Tests for MAVLinkAdapter

Run with: pytest tests/test_mavlink_adapter.py -v
"""

import math
import pytest
from unittest.mock import Mock, patch, MagicMock, PropertyMock
import time


class TestMAVLinkAdapterImport:
    """Test adapter import and registration."""

    def test_adapter_import(self):
        """Test that MAVLinkAdapter can be imported."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter
            assert MAVLinkAdapter is not None
        except ImportError as e:
            pytest.skip(f"MAVLink dependencies not installed: {e}")

    def test_adapter_registration(self):
        """Test that MAVLinkAdapter is registered in the registry."""
        try:
            from plexus.adapters import AdapterRegistry
            from plexus.adapters.mavlink import MAVLinkAdapter  # noqa: F401

            assert AdapterRegistry.has("mavlink")
            info = AdapterRegistry.info("mavlink")
            assert info["name"] == "mavlink"
            assert "pymavlink" in info["requires"]
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")


class TestMAVLinkAdapterConfig:
    """Test adapter configuration."""

    @pytest.fixture
    def adapter(self):
        """Create adapter without connecting."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter
            return MAVLinkAdapter(
                connection_string="udpin:0.0.0.0:14550",
            )
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    def test_default_config(self, adapter):
        """Test default configuration values."""
        assert adapter.connection_string == "udpin:0.0.0.0:14550"
        assert adapter.baud == 57600
        assert adapter.source_system == 255
        assert adapter.source_component == 0
        assert adapter.dialect == "ardupilotmega"
        assert adapter.emit_raw is False
        assert adapter.emit_decoded is True
        assert adapter.raw_prefix == "mavlink.raw"
        assert adapter.request_streams is True
        assert adapter.stream_rate_hz == 4

    def test_custom_config(self):
        """Test custom configuration."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter
            adapter = MAVLinkAdapter(
                connection_string="tcp:192.168.1.10:5760",
                baud=115200,
                source_system=1,
                source_component=1,
                dialect="common",
                include_messages=["ATTITUDE", "GPS_RAW_INT"],
                exclude_messages=["HEARTBEAT"],
                emit_raw=True,
                emit_decoded=False,
                raw_prefix="drone.raw",
                request_streams=False,
                stream_rate_hz=10,
                source_id="drone-001",
            )
            assert adapter.connection_string == "tcp:192.168.1.10:5760"
            assert adapter.baud == 115200
            assert adapter.source_system == 1
            assert adapter.include_messages == {"ATTITUDE", "GPS_RAW_INT"}
            assert adapter.exclude_messages == {"HEARTBEAT"}
            assert adapter.emit_raw is True
            assert adapter.emit_decoded is False
            assert adapter.raw_prefix == "drone.raw"
            assert adapter.request_streams is False
            assert adapter.stream_rate_hz == 10
            assert adapter._source_id == "drone-001"
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    def test_validate_config_valid_udp(self, adapter):
        """Test validation passes for valid UDP config."""
        assert adapter.validate_config() is True

    def test_validate_config_valid_tcp(self):
        """Test validation passes for valid TCP config."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter
            adapter = MAVLinkAdapter(connection_string="tcp:localhost:5760")
            assert adapter.validate_config() is True
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    def test_validate_config_valid_serial(self):
        """Test validation passes for valid serial config."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter
            adapter = MAVLinkAdapter(connection_string="/dev/ttyACM0")
            assert adapter.validate_config() is True
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    def test_validate_config_empty(self):
        """Test validation fails without connection string."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter
            adapter = MAVLinkAdapter(connection_string="")
            with pytest.raises(ValueError, match="connection_string is required"):
                adapter.validate_config()
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    def test_validate_config_invalid(self):
        """Test validation fails for invalid connection string."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter
            adapter = MAVLinkAdapter(connection_string="invalid:string")
            with pytest.raises(ValueError, match="Invalid connection string"):
                adapter.validate_config()
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")


class TestMAVLinkAdapterConnection:
    """Test connection handling."""

    @patch("plexus.adapters.mavlink.mavutil")
    def test_connect_success(self, mock_mavutil):
        """Test successful connection."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter
            from plexus.adapters.base import AdapterState

            mock_conn = MagicMock()
            mock_heartbeat = MagicMock()
            mock_heartbeat.get_srcSystem.return_value = 1
            mock_heartbeat.get_srcComponent.return_value = 1
            mock_conn.wait_heartbeat.return_value = mock_heartbeat
            mock_conn.target_system = 1
            mock_conn.target_component = 1
            mock_mavutil.mavlink_connection.return_value = mock_conn

            adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")
            result = adapter.connect()

            assert result is True
            assert adapter.state == AdapterState.CONNECTED
            assert adapter._vehicle_sysid == 1
            mock_mavutil.mavlink_connection.assert_called_once()
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    @patch("plexus.adapters.mavlink.mavutil")
    def test_connect_no_heartbeat(self, mock_mavutil):
        """Test connection failure when no heartbeat."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter
            from plexus.adapters.base import ConnectionError

            mock_conn = MagicMock()
            mock_conn.wait_heartbeat.return_value = None
            mock_mavutil.mavlink_connection.return_value = mock_conn

            adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")

            with pytest.raises(ConnectionError, match="No heartbeat"):
                adapter.connect()
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    @patch("plexus.adapters.mavlink.mavutil")
    def test_connect_failure(self, mock_mavutil):
        """Test connection failure."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter
            from plexus.adapters.base import ConnectionError

            mock_mavutil.mavlink_connection.side_effect = Exception("Connection refused")

            adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")

            with pytest.raises(ConnectionError):
                adapter.connect()
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    @patch("plexus.adapters.mavlink.mavutil")
    def test_disconnect(self, mock_mavutil):
        """Test disconnection."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter
            from plexus.adapters.base import AdapterState

            mock_conn = MagicMock()
            mock_heartbeat = MagicMock()
            mock_heartbeat.get_srcSystem.return_value = 1
            mock_heartbeat.get_srcComponent.return_value = 1
            mock_conn.wait_heartbeat.return_value = mock_heartbeat
            mock_conn.target_system = 1
            mock_conn.target_component = 1
            mock_mavutil.mavlink_connection.return_value = mock_conn

            adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")
            adapter.connect()
            adapter.disconnect()

            mock_conn.close.assert_called_once()
            assert adapter.state == AdapterState.DISCONNECTED
            assert adapter._vehicle_sysid is None
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    @patch("plexus.adapters.mavlink.mavutil")
    def test_disconnect_idempotent(self, mock_mavutil):
        """Test that disconnect can be called multiple times safely."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter

            adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")
            # Disconnect without connecting — should not raise
            adapter.disconnect()
            adapter.disconnect()
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    @patch("plexus.adapters.mavlink.mavutil")
    def test_request_streams_on_connect(self, mock_mavutil):
        """Test that data streams are requested on connect."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter

            mock_conn = MagicMock()
            mock_heartbeat = MagicMock()
            mock_heartbeat.get_srcSystem.return_value = 1
            mock_heartbeat.get_srcComponent.return_value = 1
            mock_conn.wait_heartbeat.return_value = mock_heartbeat
            mock_conn.target_system = 1
            mock_conn.target_component = 1
            mock_mavutil.mavlink_connection.return_value = mock_conn

            adapter = MAVLinkAdapter(
                connection_string="udpin:0.0.0.0:14550",
                request_streams=True,
                stream_rate_hz=10,
            )
            adapter.connect()

            mock_conn.mav.request_data_stream_send.assert_called_once()
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")


class TestMAVLinkAdapterMetrics:
    """Test metric creation and decoding."""

    @pytest.fixture
    def adapter(self):
        """Create adapter without connecting."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter
            return MAVLinkAdapter(
                connection_string="udpin:0.0.0.0:14550",
                emit_raw=True,
                emit_decoded=True,
            )
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    def test_create_raw_metric(self, adapter):
        """Test raw message metric creation."""
        mock_msg = MagicMock()
        mock_msg.get_type.return_value = "ATTITUDE"
        mock_msg.get_msgId.return_value = 30
        mock_msg.get_srcSystem.return_value = 1
        mock_msg.get_srcComponent.return_value = 1
        mock_msg.to_dict.return_value = {
            "mavpackettype": "ATTITUDE",
            "roll": 0.1,
            "pitch": 0.2,
            "yaw": 1.5,
        }

        timestamp = time.time()
        tags = adapter._base_tags(mock_msg)
        metric = adapter._create_raw_metric(mock_msg, "ATTITUDE", timestamp, tags)

        assert metric.name == "mavlink.raw.ATTITUDE"
        assert isinstance(metric.value, dict)
        assert "roll" in metric.value
        assert "mavpackettype" not in metric.value  # Should be stripped
        assert metric.timestamp == timestamp
        assert metric.tags["message_type"] == "ATTITUDE"

    def test_decode_attitude(self, adapter):
        """Test ATTITUDE message decoding."""
        mock_msg = MagicMock()
        mock_msg.get_type.return_value = "ATTITUDE"
        mock_msg.get_msgId.return_value = 30
        mock_msg.get_srcSystem.return_value = 1
        mock_msg.get_srcComponent.return_value = 1
        mock_msg.roll = 0.1  # radians
        mock_msg.pitch = -0.05
        mock_msg.yaw = 1.5
        mock_msg.rollspeed = 0.01
        mock_msg.pitchspeed = 0.02
        mock_msg.yawspeed = 0.03

        timestamp = time.time()
        tags = adapter._base_tags(mock_msg)
        metrics = adapter._decode_message(mock_msg, "ATTITUDE", timestamp, tags)

        assert len(metrics) == 6
        names = {m.name for m in metrics}
        assert "attitude.roll" in names
        assert "attitude.pitch" in names
        assert "attitude.yaw" in names

        # Check conversion from radians to degrees
        roll_metric = next(m for m in metrics if m.name == "attitude.roll")
        assert roll_metric.value == round(math.degrees(0.1), 2)
        assert roll_metric.tags["unit"] == "deg"

    def test_decode_gps_raw_int(self, adapter):
        """Test GPS_RAW_INT message decoding with unit scaling."""
        mock_msg = MagicMock()
        mock_msg.get_type.return_value = "GPS_RAW_INT"
        mock_msg.get_msgId.return_value = 24
        mock_msg.get_srcSystem.return_value = 1
        mock_msg.get_srcComponent.return_value = 1
        mock_msg.lat = 473977420  # degE7
        mock_msg.lon = 85455939
        mock_msg.alt = 584070  # mm
        mock_msg.fix_type = 3
        mock_msg.satellites_visible = 12
        mock_msg.eph = 121  # cm
        mock_msg.epv = 200
        mock_msg.vel = 350  # cm/s

        timestamp = time.time()
        tags = adapter._base_tags(mock_msg)
        metrics = adapter._decode_message(mock_msg, "GPS_RAW_INT", timestamp, tags)

        assert len(metrics) == 8
        lat_metric = next(m for m in metrics if m.name == "gps.lat")
        assert abs(lat_metric.value - 47.397742) < 0.001
        assert lat_metric.tags["unit"] == "deg"

        alt_metric = next(m for m in metrics if m.name == "gps.alt")
        assert abs(alt_metric.value - 584.07) < 0.01
        assert alt_metric.tags["unit"] == "m"

    def test_decode_sys_status(self, adapter):
        """Test SYS_STATUS message decoding."""
        mock_msg = MagicMock()
        mock_msg.get_type.return_value = "SYS_STATUS"
        mock_msg.get_msgId.return_value = 1
        mock_msg.get_srcSystem.return_value = 1
        mock_msg.get_srcComponent.return_value = 1
        mock_msg.voltage_battery = 12600  # mV
        mock_msg.current_battery = 1500  # cA
        mock_msg.battery_remaining = 75
        mock_msg.drop_rate_comm = 50  # centipercent
        mock_msg.errors_comm = 0

        timestamp = time.time()
        tags = adapter._base_tags(mock_msg)
        metrics = adapter._decode_message(mock_msg, "SYS_STATUS", timestamp, tags)

        assert len(metrics) == 5
        voltage = next(m for m in metrics if m.name == "sys.voltage")
        assert abs(voltage.value - 12.6) < 0.001
        assert voltage.tags["unit"] == "V"

        current = next(m for m in metrics if m.name == "sys.current")
        assert abs(current.value - 15.0) < 0.001

    def test_decode_vfr_hud(self, adapter):
        """Test VFR_HUD message decoding."""
        mock_msg = MagicMock()
        mock_msg.get_type.return_value = "VFR_HUD"
        mock_msg.get_msgId.return_value = 74
        mock_msg.get_srcSystem.return_value = 1
        mock_msg.get_srcComponent.return_value = 1
        mock_msg.airspeed = 15.5
        mock_msg.groundspeed = 14.2
        mock_msg.heading = 270
        mock_msg.throttle = 45
        mock_msg.alt = 100.5
        mock_msg.climb = 1.2

        timestamp = time.time()
        tags = adapter._base_tags(mock_msg)
        metrics = adapter._decode_message(mock_msg, "VFR_HUD", timestamp, tags)

        assert len(metrics) == 6
        names = {m.name for m in metrics}
        assert "hud.airspeed" in names
        assert "hud.groundspeed" in names
        assert "hud.heading" in names
        assert "hud.throttle" in names

    def test_decode_battery_status(self, adapter):
        """Test BATTERY_STATUS message decoding."""
        mock_msg = MagicMock()
        mock_msg.get_type.return_value = "BATTERY_STATUS"
        mock_msg.get_msgId.return_value = 147
        mock_msg.get_srcSystem.return_value = 1
        mock_msg.get_srcComponent.return_value = 1
        mock_msg.current_battery = 1500  # cA
        mock_msg.current_consumed = 1200  # mAh
        mock_msg.battery_remaining = 65
        mock_msg.temperature = 3500  # cdegC
        mock_msg.voltages = [4200, 4180, 4190, 65535, 65535, 65535, 65535, 65535, 65535, 65535]

        timestamp = time.time()
        tags = adapter._base_tags(mock_msg)
        metrics = adapter._decode_message(mock_msg, "BATTERY_STATUS", timestamp, tags)

        voltage = next(m for m in metrics if m.name == "battery.voltage")
        expected_v = (4200 + 4180 + 4190) / 1000.0
        assert abs(voltage.value - expected_v) < 0.001

    def test_decode_statustext(self, adapter):
        """Test STATUSTEXT message decoding."""
        mock_msg = MagicMock()
        mock_msg.get_type.return_value = "STATUSTEXT"
        mock_msg.get_msgId.return_value = 253
        mock_msg.get_srcSystem.return_value = 1
        mock_msg.get_srcComponent.return_value = 1
        mock_msg.text = "PreArm: GPS not healthy\x00\x00"
        mock_msg.severity = 4

        timestamp = time.time()
        tags = adapter._base_tags(mock_msg)
        metrics = adapter._decode_message(mock_msg, "STATUSTEXT", timestamp, tags)

        assert len(metrics) == 2
        text_metric = next(m for m in metrics if m.name == "status.text")
        assert text_metric.value == "PreArm: GPS not healthy"
        assert "\x00" not in text_metric.value

    def test_unknown_message_returns_empty(self, adapter):
        """Test that unknown message types return no decoded metrics."""
        mock_msg = MagicMock()
        mock_msg.get_type.return_value = "UNKNOWN_MSG"
        mock_msg.get_msgId.return_value = 999
        mock_msg.get_srcSystem.return_value = 1
        mock_msg.get_srcComponent.return_value = 1

        tags = adapter._base_tags(mock_msg)
        metrics = adapter._decode_message(mock_msg, "UNKNOWN_MSG", time.time(), tags)
        assert metrics == []


class TestMAVLinkAdapterFiltering:
    """Test message filtering."""

    @patch("plexus.adapters.mavlink.mavutil")
    def test_include_filter(self, mock_mavutil):
        """Test that include filter only passes matching messages."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter

            adapter = MAVLinkAdapter(
                connection_string="udpin:0.0.0.0:14550",
                include_messages=["ATTITUDE"],
                emit_decoded=True,
            )

            # Simulate a connected adapter
            mock_conn = MagicMock()
            adapter._conn = mock_conn

            # Message that should be filtered out
            mock_msg = MagicMock()
            mock_msg.get_type.return_value = "HEARTBEAT"
            mock_conn.recv_match.return_value = mock_msg

            metrics = adapter.poll()
            assert len(metrics) == 0
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    @patch("plexus.adapters.mavlink.mavutil")
    def test_exclude_filter(self, mock_mavutil):
        """Test that exclude filter blocks matching messages."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter

            adapter = MAVLinkAdapter(
                connection_string="udpin:0.0.0.0:14550",
                exclude_messages=["HEARTBEAT"],
                emit_decoded=True,
            )

            mock_conn = MagicMock()
            adapter._conn = mock_conn

            mock_msg = MagicMock()
            mock_msg.get_type.return_value = "HEARTBEAT"
            mock_conn.recv_match.return_value = mock_msg

            metrics = adapter.poll()
            assert len(metrics) == 0
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    @patch("plexus.adapters.mavlink.mavutil")
    def test_bad_data_skipped(self, mock_mavutil):
        """Test that BAD_DATA messages are skipped."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter

            adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")
            mock_conn = MagicMock()
            adapter._conn = mock_conn

            mock_msg = MagicMock()
            mock_msg.get_type.return_value = "BAD_DATA"
            mock_conn.recv_match.return_value = mock_msg

            metrics = adapter.poll()
            assert len(metrics) == 0
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    @patch("plexus.adapters.mavlink.mavutil")
    def test_no_message_returns_empty(self, mock_mavutil):
        """Test that poll returns empty list when no messages."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter

            adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")
            mock_conn = MagicMock()
            adapter._conn = mock_conn
            mock_conn.recv_match.return_value = None

            metrics = adapter.poll()
            assert metrics == []
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")


class TestMAVLinkAdapterCommands:
    """Test command sending."""

    @patch("plexus.adapters.mavlink.mavutil")
    def test_send_command(self, mock_mavutil):
        """Test sending a MAVLink command."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter

            mock_conn = MagicMock()
            mock_conn.target_system = 1
            mock_conn.target_component = 1

            adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")
            adapter._conn = mock_conn

            result = adapter.send_command(400, param1=1)

            assert result is True
            mock_conn.mav.command_long_send.assert_called_once()
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    @patch("plexus.adapters.mavlink.mavutil")
    def test_arm(self, mock_mavutil):
        """Test arming the vehicle."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter

            mock_conn = MagicMock()
            mock_conn.target_system = 1
            mock_conn.target_component = 1
            mock_mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM = 400

            adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")
            adapter._conn = mock_conn

            result = adapter.arm()
            assert result is True

            # Verify the correct command was sent
            call_args = mock_conn.mav.command_long_send.call_args
            assert call_args[0][2] == 400  # command
            assert call_args[0][4] == 1  # param1 = arm
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    @patch("plexus.adapters.mavlink.mavutil")
    def test_disarm(self, mock_mavutil):
        """Test disarming the vehicle."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter

            mock_conn = MagicMock()
            mock_conn.target_system = 1
            mock_conn.target_component = 1
            mock_mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM = 400

            adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")
            adapter._conn = mock_conn

            result = adapter.disarm()
            assert result is True

            call_args = mock_conn.mav.command_long_send.call_args
            assert call_args[0][4] == 0  # param1 = disarm
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    @patch("plexus.adapters.mavlink.mavutil")
    def test_set_mode(self, mock_mavutil):
        """Test setting flight mode."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter

            mock_conn = MagicMock()
            mock_conn.mode_mapping.return_value = {
                "STABILIZE": 0,
                "GUIDED": 4,
                "AUTO": 3,
            }

            adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")
            adapter._conn = mock_conn

            result = adapter.set_mode("GUIDED")
            assert result is True
            mock_conn.set_mode.assert_called_once_with(4)
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    @patch("plexus.adapters.mavlink.mavutil")
    def test_set_mode_invalid(self, mock_mavutil):
        """Test setting an invalid flight mode."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter

            mock_conn = MagicMock()
            mock_conn.mode_mapping.return_value = {"STABILIZE": 0}

            adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")
            adapter._conn = mock_conn

            with pytest.raises(ValueError, match="Unknown mode"):
                adapter.set_mode("NONEXISTENT")
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")

    def test_send_command_not_connected(self):
        """Test sending command when not connected."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter
            from plexus.adapters.base import ProtocolError

            adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")

            with pytest.raises(ProtocolError, match="Not connected"):
                adapter.send_command(400)
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")


class TestMAVLinkAdapterStats:
    """Test adapter statistics."""

    @patch("plexus.adapters.mavlink.mavutil")
    def test_stats(self, mock_mavutil):
        """Test adapter stats include MAVLink-specific info."""
        try:
            from plexus.adapters.mavlink import MAVLinkAdapter

            mock_conn = MagicMock()
            mock_heartbeat = MagicMock()
            mock_heartbeat.get_srcSystem.return_value = 1
            mock_heartbeat.get_srcComponent.return_value = 1
            mock_conn.wait_heartbeat.return_value = mock_heartbeat
            mock_conn.target_system = 1
            mock_conn.target_component = 1
            mock_mavutil.mavlink_connection.return_value = mock_conn

            adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")
            adapter.connect()

            stats = adapter.stats

            assert stats["connection_string"] == "udpin:0.0.0.0:14550"
            assert stats["baud"] == 57600
            assert stats["vehicle_sysid"] == 1
            assert stats["dialect"] == "ardupilotmega"
        except ImportError:
            pytest.skip("MAVLink dependencies not installed")
