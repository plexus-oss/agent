"""
Tests for CANAdapter

Run with: pytest tests/test_can_adapter.py -v
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import time


class TestCANAdapterImport:
    """Test adapter import and registration."""

    def test_adapter_import(self):
        """Test that CANAdapter can be imported."""
        try:
            from plexus.adapters.can import CANAdapter
            assert CANAdapter is not None
        except ImportError as e:
            pytest.skip(f"CAN dependencies not installed: {e}")

    def test_adapter_registration(self):
        """Test that CANAdapter is registered in the registry."""
        try:
            from plexus.adapters import AdapterRegistry
            from plexus.adapters.can import CANAdapter  # noqa: F401

            assert AdapterRegistry.has("can")
            info = AdapterRegistry.info("can")
            assert info["name"] == "can"
            assert "python-can" in info["requires"]
            assert "cantools" in info["requires"]
        except ImportError:
            pytest.skip("CAN dependencies not installed")


class TestCANAdapterConfig:
    """Test adapter configuration."""

    @pytest.fixture
    def adapter(self):
        """Create adapter without connecting."""
        try:
            from plexus.adapters.can import CANAdapter
            return CANAdapter(
                interface="virtual",
                channel="vcan0",
                bitrate=500000,
            )
        except ImportError:
            pytest.skip("CAN dependencies not installed")

    def test_default_config(self, adapter):
        """Test default configuration values."""
        assert adapter.interface == "virtual"
        assert adapter.channel == "vcan0"
        assert adapter.bitrate == 500000
        assert adapter.dbc_path is None
        assert adapter.emit_raw is True
        assert adapter.emit_decoded is True
        assert adapter.raw_prefix == "can.raw"

    def test_custom_config(self):
        """Test custom configuration."""
        try:
            from plexus.adapters.can import CANAdapter
            adapter = CANAdapter(
                interface="socketcan",
                channel="can0",
                bitrate=250000,
                emit_raw=False,
                emit_decoded=True,
                raw_prefix="vehicle.can",
                source_id="test-device",
            )
            assert adapter.interface == "socketcan"
            assert adapter.bitrate == 250000
            assert adapter.emit_raw is False
            assert adapter.raw_prefix == "vehicle.can"
            assert adapter._source_id == "test-device"
        except ImportError:
            pytest.skip("CAN dependencies not installed")

    def test_validate_config_valid(self, adapter):
        """Test validation passes for valid config."""
        assert adapter.validate_config() is True

    def test_validate_config_no_channel(self):
        """Test validation fails without channel."""
        try:
            from plexus.adapters.can import CANAdapter
            adapter = CANAdapter(interface="virtual", channel="")
            with pytest.raises(ValueError, match="channel is required"):
                adapter.validate_config()
        except ImportError:
            pytest.skip("CAN dependencies not installed")


class TestCANAdapterMetrics:
    """Test metric creation."""

    @pytest.fixture
    def adapter(self):
        """Create adapter without connecting."""
        try:
            from plexus.adapters.can import CANAdapter
            return CANAdapter(interface="virtual", channel="vcan0")
        except ImportError:
            pytest.skip("CAN dependencies not installed")

    def test_create_raw_metric(self, adapter):
        """Test raw frame metric creation."""
        # Create a mock CAN message
        mock_message = Mock()
        mock_message.arbitration_id = 0x123
        mock_message.data = bytes([0xDE, 0xAD, 0xBE, 0xEF])
        mock_message.dlc = 4
        mock_message.is_extended_id = False
        mock_message.is_error_frame = False
        mock_message.is_remote_frame = False

        timestamp = time.time()
        metric = adapter._create_raw_metric(mock_message, timestamp)

        assert metric.name == "can.raw.0x123"
        assert metric.value == "DEADBEEF"
        assert metric.timestamp == timestamp
        assert metric.tags["arbitration_id"] == "291"  # 0x123 in decimal
        assert metric.tags["dlc"] == "4"
        assert metric.tags["is_extended"] == "false"

    def test_create_raw_metric_extended_id(self, adapter):
        """Test raw metric with extended ID."""
        mock_message = Mock()
        mock_message.arbitration_id = 0x18FEF100
        mock_message.data = bytes([0x01, 0x02])
        mock_message.dlc = 2
        mock_message.is_extended_id = True
        mock_message.is_error_frame = False
        mock_message.is_remote_frame = False

        metric = adapter._create_raw_metric(mock_message, time.time())

        assert "0x18FEF100" in metric.name
        assert metric.tags["is_extended"] == "true"


class TestCANAdapterConnection:
    """Test connection handling."""

    @patch("plexus.adapters.can.can")
    def test_connect_success(self, mock_can):
        """Test successful connection."""
        try:
            from plexus.adapters.can import CANAdapter
            from plexus.adapters.base import AdapterState

            mock_bus = MagicMock()
            mock_can.Bus.return_value = mock_bus

            adapter = CANAdapter(interface="virtual", channel="vcan0")
            result = adapter.connect()

            assert result is True
            assert adapter.state == AdapterState.CONNECTED
            mock_can.Bus.assert_called_once()
        except ImportError:
            pytest.skip("CAN dependencies not installed")

    @patch("plexus.adapters.can.can")
    def test_connect_failure(self, mock_can):
        """Test connection failure."""
        try:
            from plexus.adapters.can import CANAdapter
            from plexus.adapters.base import ConnectionError

            mock_can.Bus.side_effect = Exception("Connection failed")

            adapter = CANAdapter(interface="virtual", channel="vcan0")

            with pytest.raises(ConnectionError):
                adapter.connect()
        except ImportError:
            pytest.skip("CAN dependencies not installed")

    @patch("plexus.adapters.can.can")
    def test_disconnect(self, mock_can):
        """Test disconnection."""
        try:
            from plexus.adapters.can import CANAdapter
            from plexus.adapters.base import AdapterState

            mock_bus = MagicMock()
            mock_can.Bus.return_value = mock_bus

            adapter = CANAdapter(interface="virtual", channel="vcan0")
            adapter.connect()
            adapter.disconnect()

            mock_bus.shutdown.assert_called_once()
            assert adapter.state == AdapterState.DISCONNECTED
        except ImportError:
            pytest.skip("CAN dependencies not installed")


class TestCANAdapterDBCDecoding:
    """Test DBC file decoding."""

    @pytest.fixture
    def mock_dbc_message(self):
        """Create a mock DBC message."""
        mock_signal = Mock()
        mock_signal.unit = "rpm"

        mock_message = Mock()
        mock_message.name = "EngineStatus"
        mock_message.frame_id = 0x123
        mock_message.decode.return_value = {"engine_rpm": 2500, "coolant_temp": 85}
        mock_message.get_signal_by_name.return_value = mock_signal

        return mock_message

    @patch("plexus.adapters.can.cantools")
    @patch("plexus.adapters.can.can")
    def test_decode_message(self, mock_can, mock_cantools, mock_dbc_message):
        """Test DBC message decoding."""
        try:
            from plexus.adapters.can import CANAdapter

            # Setup mock database
            mock_db = Mock()
            mock_db.messages = [mock_dbc_message]
            mock_cantools.database.load_file.return_value = mock_db

            # Setup mock bus
            mock_bus = MagicMock()
            mock_can.Bus.return_value = mock_bus

            adapter = CANAdapter(
                interface="virtual",
                channel="vcan0",
                dbc_path="test.dbc",
            )
            adapter.connect()

            # Manually add message to cache (simulating DBC load)
            adapter._message_cache[0x123] = mock_dbc_message
            adapter._db = mock_db

            # Create mock CAN message
            mock_message = Mock()
            mock_message.arbitration_id = 0x123
            mock_message.data = bytes([0x00] * 8)

            metrics = adapter._decode_message(mock_message, time.time())

            assert len(metrics) == 2
            metric_names = [m.name for m in metrics]
            assert "engine_rpm" in metric_names
            assert "coolant_temp" in metric_names

            # Check values
            rpm_metric = next(m for m in metrics if m.name == "engine_rpm")
            assert rpm_metric.value == 2500
            assert rpm_metric.tags["can_id"] == "0x123"
            assert rpm_metric.tags["dbc_message"] == "EngineStatus"
        except ImportError:
            pytest.skip("CAN dependencies not installed")


class TestCANAdapterSend:
    """Test CAN frame transmission."""

    @patch("plexus.adapters.can.can")
    def test_send_frame(self, mock_can):
        """Test sending a CAN frame."""
        try:
            from plexus.adapters.can import CANAdapter

            mock_bus = MagicMock()
            mock_can.Bus.return_value = mock_bus

            adapter = CANAdapter(interface="virtual", channel="vcan0")
            adapter.connect()

            result = adapter.send(0x100, bytes([0x01, 0x02, 0x03, 0x04]))

            assert result is True
            mock_bus.send.assert_called_once()
        except ImportError:
            pytest.skip("CAN dependencies not installed")

    @patch("plexus.adapters.can.can")
    def test_send_not_connected(self, mock_can):
        """Test sending when not connected."""
        try:
            from plexus.adapters.can import CANAdapter
            from plexus.adapters.base import ProtocolError

            adapter = CANAdapter(interface="virtual", channel="vcan0")

            with pytest.raises(ProtocolError, match="Not connected"):
                adapter.send(0x100, bytes([0x01]))
        except ImportError:
            pytest.skip("CAN dependencies not installed")


class TestCANAdapterStats:
    """Test adapter statistics."""

    @patch("plexus.adapters.can.can")
    def test_stats_no_dbc(self, mock_can):
        """Test stats without DBC file."""
        try:
            from plexus.adapters.can import CANAdapter

            mock_bus = MagicMock()
            mock_can.Bus.return_value = mock_bus

            adapter = CANAdapter(interface="virtual", channel="vcan0")
            adapter.connect()

            stats = adapter.stats

            assert stats["interface"] == "virtual"
            assert stats["channel"] == "vcan0"
            assert stats["bitrate"] == 500000
            assert stats["dbc_loaded"] is False
            assert stats["dbc_messages"] == 0
        except ImportError:
            pytest.skip("CAN dependencies not installed")
