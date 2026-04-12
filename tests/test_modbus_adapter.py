"""
Tests for ModbusAdapter

Run with: pytest tests/test_modbus_adapter.py -v
"""

import struct
import pytest
from unittest.mock import Mock, MagicMock, patch


class TestModbusAdapterImport:
    """Test adapter import and registration."""

    def test_adapter_import(self):
        from plexus.adapters.modbus import ModbusAdapter
        assert ModbusAdapter is not None

    def test_adapter_registration(self):
        from plexus.adapters import AdapterRegistry
        from plexus.adapters.modbus import ModbusAdapter  # noqa: F401

        assert AdapterRegistry.has("modbus")
        info = AdapterRegistry.info("modbus")
        assert info["name"] == "modbus"
        assert "pymodbus" in info["requires"]


class TestModbusAdapterConfig:
    """Test adapter configuration and register validation."""

    def test_default_config(self):
        from plexus.adapters.modbus import ModbusAdapter

        adapter = ModbusAdapter()
        assert adapter.host == "127.0.0.1"
        assert adapter.port == 502
        assert adapter.mode == "tcp"
        assert adapter.unit_id == 1
        assert adapter.prefix == "modbus."
        assert adapter.poll_interval == 1.0

    def test_custom_config(self):
        from plexus.adapters.modbus import ModbusAdapter

        adapter = ModbusAdapter(
            host="192.168.1.100",
            port=5020,
            mode="rtu",
            unit_id=3,
            baudrate=19200,
            prefix="plc.",
            poll_interval=0.5,
            source_id="plc-01",
        )
        assert adapter.host == "192.168.1.100"
        assert adapter.port == 5020
        assert adapter.mode == "rtu"
        assert adapter.unit_id == 3
        assert adapter.baudrate == 19200
        assert adapter.prefix == "plc."
        assert adapter._source_id == "plc-01"

    def test_validate_config_invalid_mode(self):
        from plexus.adapters.modbus import ModbusAdapter

        adapter = ModbusAdapter(mode="udp")
        with pytest.raises(ValueError, match="Invalid mode"):
            adapter.validate_config()

    def test_validate_config_no_host(self):
        from plexus.adapters.modbus import ModbusAdapter

        adapter = ModbusAdapter(host="")
        with pytest.raises(ValueError, match="Host is required"):
            adapter.validate_config()

    def test_parse_registers_valid(self):
        from plexus.adapters.modbus import ModbusAdapter

        regs = ModbusAdapter._parse_registers([
            {"address": 0, "name": "temp", "type": "holding",
             "data_type": "float32", "scale": 0.1, "unit": "°C"},
            {"address": 10, "name": "pump", "type": "coil"},
        ])

        assert len(regs) == 2
        assert regs[0]["name"] == "temp"
        assert regs[0]["data_type"] == "float32"
        assert regs[0]["count"] == 2  # float32 = 2 registers
        assert regs[0]["scale"] == 0.1
        assert regs[0]["unit"] == "°C"

        assert regs[1]["name"] == "pump"
        assert regs[1]["data_type"] == "bool"
        assert regs[1]["count"] == 1

    def test_parse_registers_missing_address(self):
        from plexus.adapters.modbus import ModbusAdapter

        with pytest.raises(ValueError, match="'address' is required"):
            ModbusAdapter._parse_registers([{"name": "temp"}])

    def test_parse_registers_missing_name(self):
        from plexus.adapters.modbus import ModbusAdapter

        with pytest.raises(ValueError, match="'name' is required"):
            ModbusAdapter._parse_registers([{"address": 0}])

    def test_parse_registers_invalid_type(self):
        from plexus.adapters.modbus import ModbusAdapter

        with pytest.raises(ValueError, match="invalid type"):
            ModbusAdapter._parse_registers([
                {"address": 0, "name": "x", "type": "invalid"}
            ])

    def test_parse_registers_invalid_data_type(self):
        from plexus.adapters.modbus import ModbusAdapter

        with pytest.raises(ValueError, match="invalid data_type"):
            ModbusAdapter._parse_registers([
                {"address": 0, "name": "x", "data_type": "float64"}
            ])


class TestModbusAdapterConnection:
    """Test connection handling."""

    @patch("plexus.adapters.modbus.pymodbus_client", True)
    @patch("plexus.adapters.modbus.ModbusTcpClient")
    def test_connect_tcp_success(self, MockTcpClient):
        from plexus.adapters.modbus import ModbusAdapter
        from plexus.adapters.base import AdapterState

        mock_client = MagicMock()
        mock_client.connect.return_value = True
        MockTcpClient.return_value = mock_client

        adapter = ModbusAdapter(host="192.168.1.100", port=502)
        result = adapter.connect()

        assert result is True
        assert adapter.state == AdapterState.CONNECTED
        MockTcpClient.assert_called_once_with(host="192.168.1.100", port=502)

    @patch("plexus.adapters.modbus.pymodbus_client", True)
    @patch("plexus.adapters.modbus.ModbusSerialClient")
    def test_connect_rtu_success(self, MockSerialClient):
        from plexus.adapters.modbus import ModbusAdapter
        from plexus.adapters.base import AdapterState

        mock_client = MagicMock()
        mock_client.connect.return_value = True
        MockSerialClient.return_value = mock_client

        adapter = ModbusAdapter(
            host="/dev/ttyUSB0", mode="rtu", baudrate=9600
        )
        result = adapter.connect()

        assert result is True
        assert adapter.state == AdapterState.CONNECTED
        MockSerialClient.assert_called_once_with(
            port="/dev/ttyUSB0", baudrate=9600
        )

    @patch("plexus.adapters.modbus.pymodbus_client", True)
    @patch("plexus.adapters.modbus.ModbusTcpClient")
    def test_connect_failure(self, MockTcpClient):
        from plexus.adapters.modbus import ModbusAdapter
        from plexus.adapters.base import ConnectionError

        mock_client = MagicMock()
        mock_client.connect.return_value = False
        MockTcpClient.return_value = mock_client

        adapter = ModbusAdapter(host="192.168.1.100")

        with pytest.raises(ConnectionError, match="Modbus connection failed"):
            adapter.connect()

    def test_connect_no_pymodbus(self):
        from plexus.adapters.modbus import ModbusAdapter
        from plexus.adapters.base import ConnectionError

        with patch("plexus.adapters.modbus.pymodbus_client", None):
            adapter = ModbusAdapter()
            with pytest.raises(ConnectionError, match="pymodbus is required"):
                adapter.connect()

    @patch("plexus.adapters.modbus.pymodbus_client", True)
    @patch("plexus.adapters.modbus.ModbusTcpClient")
    def test_disconnect(self, MockTcpClient):
        from plexus.adapters.modbus import ModbusAdapter
        from plexus.adapters.base import AdapterState

        mock_client = MagicMock()
        mock_client.connect.return_value = True
        MockTcpClient.return_value = mock_client

        adapter = ModbusAdapter()
        adapter.connect()
        adapter.disconnect()

        mock_client.close.assert_called_once()
        assert adapter.state == AdapterState.DISCONNECTED


class TestModbusAdapterDataConversion:
    """Test register data conversion."""

    def test_convert_uint16(self):
        from plexus.adapters.modbus import ModbusAdapter

        value = ModbusAdapter._convert_registers([1000], "uint16")
        assert value == 1000.0

    def test_convert_int16(self):
        from plexus.adapters.modbus import ModbusAdapter

        # -100 in signed 16-bit = 0xFF9C = 65436 unsigned
        value = ModbusAdapter._convert_registers([65436], "int16")
        assert value == -100.0

    def test_convert_uint32(self):
        from plexus.adapters.modbus import ModbusAdapter

        # 100000 = 0x000186A0 → high=0x0001, low=0x86A0
        value = ModbusAdapter._convert_registers([0x0001, 0x86A0], "uint32")
        assert value == 100000.0

    def test_convert_int32(self):
        from plexus.adapters.modbus import ModbusAdapter

        # -1 in 32-bit = 0xFFFFFFFF → high=0xFFFF, low=0xFFFF
        value = ModbusAdapter._convert_registers([0xFFFF, 0xFFFF], "int32")
        assert value == -1.0

    def test_convert_float32(self):
        from plexus.adapters.modbus import ModbusAdapter

        # Pack 22.5 as big-endian float32
        raw_bytes = struct.pack(">f", 22.5)
        high = struct.unpack(">H", raw_bytes[0:2])[0]
        low = struct.unpack(">H", raw_bytes[2:4])[0]

        value = ModbusAdapter._convert_registers([high, low], "float32")
        assert abs(value - 22.5) < 0.001

    def test_convert_not_enough_registers(self):
        from plexus.adapters.modbus import ModbusAdapter
        from plexus.adapters.base import ProtocolError

        with pytest.raises(ProtocolError, match="Expected 2"):
            ModbusAdapter._convert_registers([100], "float32")


class TestModbusAdapterPoll:
    """Test polling for metrics."""

    @patch("plexus.adapters.modbus.pymodbus_client", True)
    @patch("plexus.adapters.modbus.ModbusTcpClient")
    def test_poll_holding_register(self, MockTcpClient):
        from plexus.adapters.modbus import ModbusAdapter

        mock_client = MagicMock()
        mock_client.connect.return_value = True

        mock_result = Mock()
        mock_result.isError.return_value = False
        mock_result.registers = [1000]
        mock_client.read_holding_registers.return_value = mock_result

        MockTcpClient.return_value = mock_client

        adapter = ModbusAdapter(
            registers=[
                {"address": 0, "name": "temp", "scale": 0.1, "unit": "°C"}
            ]
        )
        adapter.connect()

        metrics = adapter.poll()

        assert len(metrics) == 1
        assert metrics[0].name == "modbus.temp"
        assert abs(metrics[0].value - 100.0) < 0.001  # 1000 * 0.1
        assert metrics[0].tags["unit"] == "°C"

    @patch("plexus.adapters.modbus.pymodbus_client", True)
    @patch("plexus.adapters.modbus.ModbusTcpClient")
    def test_poll_coil(self, MockTcpClient):
        from plexus.adapters.modbus import ModbusAdapter

        mock_client = MagicMock()
        mock_client.connect.return_value = True

        mock_result = Mock()
        mock_result.isError.return_value = False
        mock_result.bits = [True]
        mock_client.read_coils.return_value = mock_result

        MockTcpClient.return_value = mock_client

        adapter = ModbusAdapter(
            registers=[
                {"address": 10, "name": "pump_on", "type": "coil"}
            ]
        )
        adapter.connect()

        metrics = adapter.poll()

        assert len(metrics) == 1
        assert metrics[0].name == "modbus.pump_on"
        assert metrics[0].value is True

    def test_poll_not_connected(self):
        from plexus.adapters.modbus import ModbusAdapter

        adapter = ModbusAdapter()
        assert adapter.poll() == []

    @patch("plexus.adapters.modbus.pymodbus_client", True)
    @patch("plexus.adapters.modbus.ModbusTcpClient")
    def test_poll_oserror_propagates(self, MockTcpClient):
        """OSError in poll propagates for reconnect handling."""
        from plexus.adapters.modbus import ModbusAdapter

        mock_client = MagicMock()
        mock_client.connect.return_value = True
        mock_client.read_holding_registers.side_effect = OSError("Connection lost")
        MockTcpClient.return_value = mock_client

        adapter = ModbusAdapter(
            registers=[{"address": 0, "name": "temp"}]
        )
        adapter.connect()

        with pytest.raises(OSError):
            adapter.poll()


class TestModbusAdapterWrite:
    """Test register write operations."""

    @patch("plexus.adapters.modbus.pymodbus_client", True)
    @patch("plexus.adapters.modbus.ModbusTcpClient")
    def test_write_holding_register(self, MockTcpClient):
        from plexus.adapters.modbus import ModbusAdapter

        mock_client = MagicMock()
        mock_client.connect.return_value = True
        mock_result = Mock()
        mock_result.isError.return_value = False
        mock_client.write_register.return_value = mock_result
        MockTcpClient.return_value = mock_client

        adapter = ModbusAdapter()
        adapter.connect()

        result = adapter.write_register(0, 100)
        assert result is True

    @patch("plexus.adapters.modbus.pymodbus_client", True)
    @patch("plexus.adapters.modbus.ModbusTcpClient")
    def test_write_coil(self, MockTcpClient):
        from plexus.adapters.modbus import ModbusAdapter

        mock_client = MagicMock()
        mock_client.connect.return_value = True
        mock_result = Mock()
        mock_result.isError.return_value = False
        mock_client.write_coil.return_value = mock_result
        MockTcpClient.return_value = mock_client

        adapter = ModbusAdapter()
        adapter.connect()

        result = adapter.write_register(10, True, register_type="coil")
        assert result is True

    def test_write_not_connected(self):
        from plexus.adapters.modbus import ModbusAdapter
        from plexus.adapters.base import ProtocolError

        adapter = ModbusAdapter()
        with pytest.raises(ProtocolError, match="Not connected"):
            adapter.write_register(0, 100)


class TestModbusAdapterStats:
    """Test adapter statistics."""

    def test_stats(self):
        from plexus.adapters.modbus import ModbusAdapter

        adapter = ModbusAdapter(
            host="192.168.1.100",
            port=502,
            mode="tcp",
            unit_id=2,
            registers=[{"address": 0, "name": "temp"}],
        )
        stats = adapter.stats

        assert stats["host"] == "192.168.1.100"
        assert stats["port"] == 502
        assert stats["mode"] == "tcp"
        assert stats["unit_id"] == 2
        assert stats["register_count"] == 1
