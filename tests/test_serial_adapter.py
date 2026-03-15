"""
Tests for SerialAdapter

Run with: pytest tests/test_serial_adapter.py -v
"""

import json
import pytest
import time
from unittest.mock import Mock, MagicMock, patch


class TestSerialAdapterImport:
    """Test adapter import and registration."""

    def test_adapter_import(self):
        from plexus.adapters.serial_adapter import SerialAdapter
        assert SerialAdapter is not None

    def test_adapter_registration(self):
        from plexus.adapters import AdapterRegistry
        from plexus.adapters.serial_adapter import SerialAdapter  # noqa: F401

        assert AdapterRegistry.has("serial")
        info = AdapterRegistry.info("serial")
        assert info["name"] == "serial"
        assert "pyserial" in info["requires"]


class TestSerialAdapterConfig:
    """Test adapter configuration."""

    def test_default_config(self):
        from plexus.adapters.serial_adapter import SerialAdapter

        adapter = SerialAdapter()
        assert adapter.port == "/dev/ttyUSB0"
        assert adapter.baudrate == 9600
        assert adapter.parser == "line"
        assert adapter.prefix == "serial."
        assert adapter.timeout == 1.0

    def test_custom_config(self):
        from plexus.adapters.serial_adapter import SerialAdapter

        adapter = SerialAdapter(
            port="COM3",
            baudrate=115200,
            parser="json",
            prefix="device.",
            timeout=2.0,
            source_id="test-device",
        )
        assert adapter.port == "COM3"
        assert adapter.baudrate == 115200
        assert adapter.parser == "json"
        assert adapter.prefix == "device."
        assert adapter._source_id == "test-device"

    def test_validate_config_valid(self):
        from plexus.adapters.serial_adapter import SerialAdapter

        adapter = SerialAdapter(port="/dev/ttyUSB0")
        assert adapter.validate_config() is True

    def test_validate_config_no_port(self):
        from plexus.adapters.serial_adapter import SerialAdapter

        adapter = SerialAdapter(port="")
        with pytest.raises(ValueError, match="port is required"):
            adapter.validate_config()

    def test_validate_config_invalid_parser(self):
        from plexus.adapters.serial_adapter import SerialAdapter

        adapter = SerialAdapter(parser="xml")
        with pytest.raises(ValueError, match="Invalid parser"):
            adapter.validate_config()

    def test_validate_config_negative_baudrate(self):
        from plexus.adapters.serial_adapter import SerialAdapter

        adapter = SerialAdapter(baudrate=-1)
        with pytest.raises(ValueError, match="Baud rate must be positive"):
            adapter.validate_config()


class TestSerialAdapterParsing:
    """Test line parsing logic."""

    @pytest.fixture
    def adapter(self):
        from plexus.adapters.serial_adapter import SerialAdapter
        return SerialAdapter(prefix="serial.")

    def test_parse_key_value(self, adapter):
        """Parse 'metric:value' format."""
        metrics = adapter._parse_line("temperature:22.5")

        assert len(metrics) == 1
        assert metrics[0].name == "serial.temperature"
        assert metrics[0].value == 22.5

    def test_parse_key_value_integer(self, adapter):
        """Integer values stay as int."""
        metrics = adapter._parse_line("count:42")

        assert len(metrics) == 1
        assert metrics[0].value == 42
        assert isinstance(metrics[0].value, int)

    def test_parse_key_value_string(self, adapter):
        """Non-numeric values become strings."""
        metrics = adapter._parse_line("status:OK")

        assert len(metrics) == 1
        assert metrics[0].value == "OK"

    def test_parse_key_value_boolean(self, adapter):
        """Boolean-ish strings are coerced."""
        metrics = adapter._parse_line("enabled:true")
        assert metrics[0].value is True

        metrics = adapter._parse_line("enabled:false")
        assert metrics[0].value is False

    def test_parse_key_value_no_colon(self, adapter):
        """Lines without colon are skipped."""
        metrics = adapter._parse_line("just a string")
        assert metrics == []

    def test_parse_key_value_empty_name(self, adapter):
        """Empty name after colon split returns empty."""
        metrics = adapter._parse_line(":42")
        assert metrics == []

    def test_parse_json(self, adapter):
        """Parse JSON object format."""
        adapter.parser = "json"
        metrics = adapter._parse_line('{"temperature": 22.5, "humidity": 45}')

        assert len(metrics) == 2
        names = {m.name for m in metrics}
        assert "serial.temperature" in names
        assert "serial.humidity" in names

    def test_parse_json_invalid(self, adapter):
        """Invalid JSON returns empty."""
        adapter.parser = "json"
        metrics = adapter._parse_line("not json at all")
        assert metrics == []

    def test_parse_json_non_object(self, adapter):
        """Non-object JSON returns empty."""
        adapter.parser = "json"
        metrics = adapter._parse_line("[1, 2, 3]")
        assert metrics == []

    def test_parse_csv_header_then_data(self, adapter):
        """CSV mode: first line is header, second is data."""
        adapter.parser = "csv"

        # First line = headers
        metrics = adapter._parse_line("temp,humidity,pressure")
        assert metrics == []
        assert adapter._csv_headers == ["temp", "humidity", "pressure"]

        # Second line = data
        metrics = adapter._parse_line("22.5,45,1013.2")
        assert len(metrics) == 3
        assert metrics[0].name == "serial.temp"
        assert metrics[0].value == 22.5
        assert metrics[2].value == 1013.2

    def test_parse_csv_column_mismatch(self, adapter):
        """CSV with wrong number of columns returns empty."""
        adapter.parser = "csv"
        adapter._csv_headers = ["a", "b", "c"]
        metrics = adapter._parse_line("1,2")
        assert metrics == []


class TestSerialAdapterConnection:
    """Test connection handling."""

    @patch("plexus.adapters.serial_adapter.pyserial")
    def test_connect_success(self, mock_pyserial):
        from plexus.adapters.serial_adapter import SerialAdapter
        from plexus.adapters.base import AdapterState

        mock_serial = MagicMock()
        mock_pyserial.Serial.return_value = mock_serial

        adapter = SerialAdapter(port="/dev/ttyUSB0")
        result = adapter.connect()

        assert result is True
        assert adapter.state == AdapterState.CONNECTED
        mock_pyserial.Serial.assert_called_once_with(
            port="/dev/ttyUSB0",
            baudrate=9600,
            timeout=1.0,
        )

    @patch("plexus.adapters.serial_adapter.pyserial")
    def test_connect_failure(self, mock_pyserial):
        from plexus.adapters.serial_adapter import SerialAdapter
        from plexus.adapters.base import ConnectionError

        mock_pyserial.Serial.side_effect = Exception("Port not found")

        adapter = SerialAdapter(port="/dev/nonexistent")

        with pytest.raises(ConnectionError, match="Serial connection failed"):
            adapter.connect()

    def test_connect_no_pyserial(self):
        from plexus.adapters.serial_adapter import SerialAdapter
        from plexus.adapters.base import ConnectionError

        with patch("plexus.adapters.serial_adapter.pyserial", None):
            adapter = SerialAdapter()
            with pytest.raises(ConnectionError, match="pyserial is required"):
                adapter.connect()

    @patch("plexus.adapters.serial_adapter.pyserial")
    def test_disconnect(self, mock_pyserial):
        from plexus.adapters.serial_adapter import SerialAdapter
        from plexus.adapters.base import AdapterState

        mock_serial = MagicMock()
        mock_pyserial.Serial.return_value = mock_serial

        adapter = SerialAdapter()
        adapter.connect()
        adapter.disconnect()

        mock_serial.close.assert_called_once()
        assert adapter.state == AdapterState.DISCONNECTED
        assert adapter._csv_headers is None

    @patch("plexus.adapters.serial_adapter.pyserial")
    def test_poll_when_not_open(self, mock_pyserial):
        """Poll returns empty when port is not open."""
        from plexus.adapters.serial_adapter import SerialAdapter

        adapter = SerialAdapter()
        adapter._serial = None
        assert adapter.poll() == []

    @patch("plexus.adapters.serial_adapter.pyserial")
    def test_poll_oserror_propagates(self, mock_pyserial):
        """OSError in poll propagates for reconnect handling."""
        from plexus.adapters.serial_adapter import SerialAdapter

        mock_serial = MagicMock()
        mock_serial.is_open = True
        mock_serial.in_waiting = 0
        mock_serial.read.side_effect = OSError("Device disconnected")

        adapter = SerialAdapter()
        adapter._serial = mock_serial

        with pytest.raises(OSError):
            adapter.poll()


class TestSerialAdapterWrite:
    """Test serial write."""

    def test_write_success(self):
        from plexus.adapters.serial_adapter import SerialAdapter

        adapter = SerialAdapter()
        adapter._serial = MagicMock()
        adapter._serial.is_open = True

        result = adapter.write("hello\n")

        assert result is True
        adapter._serial.write.assert_called_once_with(b"hello\n")
        adapter._serial.flush.assert_called_once()

    def test_write_not_open(self):
        from plexus.adapters.serial_adapter import SerialAdapter
        from plexus.adapters.base import ProtocolError

        adapter = SerialAdapter()
        adapter._serial = None

        with pytest.raises(ProtocolError, match="not open"):
            adapter.write("hello")


class TestSerialAdapterStats:
    """Test adapter statistics."""

    def test_stats(self):
        from plexus.adapters.serial_adapter import SerialAdapter

        adapter = SerialAdapter(port="COM3", baudrate=115200, parser="json")
        stats = adapter.stats

        assert stats["port"] == "COM3"
        assert stats["baudrate"] == 115200
        assert stats["parser"] == "json"
        assert stats["is_open"] is False
