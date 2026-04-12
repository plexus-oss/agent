"""
Modbus Adapter - Modbus TCP/RTU protocol support for industrial devices

This adapter reads Modbus registers (holding, input, coil, discrete) from
a Modbus slave device and emits scaled, typed metric values.

Requirements:
    pip install plexus-python[modbus]
    # or
    pip install pymodbus

Usage:
    from plexus.adapters import ModbusAdapter

    # TCP connection to a PLC
    adapter = ModbusAdapter(
        host="192.168.1.100",
        port=502,
        unit_id=1,
        registers=[
            {"address": 0, "name": "temperature", "type": "holding",
             "data_type": "float32", "scale": 0.1, "unit": "°C"},
            {"address": 2, "name": "pressure", "type": "input",
             "data_type": "uint16", "scale": 0.01, "unit": "bar"},
            {"address": 10, "name": "pump_running", "type": "coil"},
        ],
        poll_interval=1.0,
    )
    adapter.connect()
    for metric in adapter.poll():
        print(f"{metric.name}: {metric.value}")

    # RTU connection over serial
    adapter = ModbusAdapter(
        host="/dev/ttyUSB0",
        mode="rtu",
        baudrate=9600,
        unit_id=1,
        registers=[
            {"address": 100, "name": "flow_rate", "type": "holding",
             "data_type": "int32", "scale": 0.001, "unit": "m³/h"},
        ],
    )

Supported register types:
    - holding: Holding registers (function code 3)
    - input: Input registers (function code 4)
    - coil: Coils / discrete outputs (function code 1)
    - discrete: Discrete inputs (function code 2)

Supported data types (for holding/input registers):
    - uint16: Unsigned 16-bit integer (1 register)
    - int16: Signed 16-bit integer (1 register)
    - uint32: Unsigned 32-bit integer (2 registers)
    - int32: Signed 32-bit integer (2 registers)
    - float32: IEEE 754 32-bit float (2 registers)

Emitted metrics:
    - {prefix}{name} - Scaled register value (e.g., "modbus.temperature")
"""

from typing import Any, Dict, List, Optional
import struct
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
# mocked in tests with @patch("plexus.adapters.modbus.pymodbus_client")
try:
    from pymodbus.client import ModbusTcpClient, ModbusSerialClient
    pymodbus_client = True
except ImportError:
    pymodbus_client = None  # type: ignore[assignment]
    ModbusTcpClient = None  # type: ignore[assignment,misc]
    ModbusSerialClient = None  # type: ignore[assignment,misc]

# Data type definitions: (struct format, register count)
_DATA_TYPES = {
    "uint16": (">H", 1),
    "int16": (">h", 1),
    "uint32": (">I", 2),
    "int32": (">i", 2),
    "float32": (">f", 2),
}

# Valid register types
_REGISTER_TYPES = {"holding", "input", "coil", "discrete"}


@register_adapter(
    "modbus",
    description="Modbus TCP/RTU adapter for industrial devices",
    author="Plexus",
    version="1.0.0",
    requires=["pymodbus"],
)
class ModbusAdapter(ProtocolAdapter):
    """
    Modbus protocol adapter for industrial devices.

    Reads holding registers, input registers, coils, and discrete inputs
    from a Modbus slave via TCP or RTU (serial). Register values are
    converted according to their configured data type, then scaled and
    offset before being emitted as Plexus metrics.

    Args:
        host: TCP hostname/IP or serial port path (e.g., "192.168.1.100"
              or "/dev/ttyUSB0")
        port: TCP port number (default: 502, ignored for RTU)
        mode: Connection mode — "tcp" or "rtu" (default: "tcp")
        unit_id: Modbus slave/unit ID (default: 1)
        baudrate: Serial baudrate for RTU mode (default: 9600)
        registers: List of register definitions. Each is a dict with:
            - address (int): Register start address
            - count (int): Number of registers to read (default: 1)
            - name (str): Metric name suffix
            - type (str): "holding", "input", "coil", or "discrete"
                          (default: "holding")
            - data_type (str): "uint16", "int16", "uint32", "int32",
                               or "float32" (default: "uint16")
            - scale (float): Multiply raw value by this (default: 1.0)
            - offset (float): Add to scaled value (default: 0.0)
            - unit (str): Engineering unit string for tags (optional)
        poll_interval: Seconds between polls (default: 1.0)
        prefix: Metric name prefix (default: "modbus.")
        source_id: Source ID for metrics (optional)

    Example:
        adapter = ModbusAdapter(
            host="192.168.1.100",
            unit_id=1,
            registers=[
                {"address": 0, "name": "temperature", "data_type": "float32",
                 "scale": 0.1, "unit": "°C"},
                {"address": 2, "name": "pressure", "data_type": "uint16",
                 "scale": 0.01, "unit": "bar"},
                {"address": 10, "name": "pump_on", "type": "coil"},
            ],
        )

        with adapter:
            while True:
                for metric in adapter.poll():
                    print(f"{metric.name} = {metric.value}")
                time.sleep(adapter.poll_interval)
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 502,
        mode: str = "tcp",
        unit_id: int = 1,
        baudrate: int = 9600,
        registers: Optional[List[Dict[str, Any]]] = None,
        poll_interval: float = 1.0,
        prefix: str = "modbus.",
        source_id: Optional[str] = None,
        **kwargs,
    ):
        config = AdapterConfig(
            name="modbus",
            params={
                "host": host,
                "port": port,
                "mode": mode,
                "unit_id": unit_id,
                "baudrate": baudrate,
                **kwargs,
            },
        )
        super().__init__(config)

        self.host = host
        self.port = port
        self.mode = mode.lower()
        self.unit_id = unit_id
        self.baudrate = baudrate
        self.poll_interval = poll_interval
        self.prefix = prefix
        self._source_id = source_id

        # Parse and validate register definitions
        self._registers = self._parse_registers(registers or [])

        self._client: Optional[Any] = None  # pymodbus client instance

    @staticmethod
    def _parse_registers(
        raw_registers: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Validate and normalise register definitions.

        Each entry must have at least ``address`` (int) and ``name`` (str).
        Missing optional fields are filled with defaults.

        Raises:
            ValueError: On invalid register configuration.
        """
        parsed: List[Dict[str, Any]] = []

        for i, reg in enumerate(raw_registers):
            if "address" not in reg:
                raise ValueError(
                    f"Register {i}: 'address' is required"
                )
            if "name" not in reg:
                raise ValueError(
                    f"Register {i}: 'name' is required"
                )

            reg_type = reg.get("type", "holding")
            if reg_type not in _REGISTER_TYPES:
                raise ValueError(
                    f"Register {i} ({reg['name']}): invalid type "
                    f"'{reg_type}'. Must be one of: "
                    f"{', '.join(sorted(_REGISTER_TYPES))}"
                )

            data_type = reg.get("data_type", "uint16")
            # Coil/discrete are always boolean — ignore data_type for them
            if reg_type in ("coil", "discrete"):
                data_type = "bool"
            elif data_type not in _DATA_TYPES:
                raise ValueError(
                    f"Register {i} ({reg['name']}): invalid data_type "
                    f"'{data_type}'. Must be one of: "
                    f"{', '.join(sorted(_DATA_TYPES))}"
                )

            # For register types, determine count from data_type if not given
            if reg_type in ("holding", "input"):
                default_count = _DATA_TYPES[data_type][1]
            else:
                default_count = reg.get("count", 1)

            parsed.append({
                "address": int(reg["address"]),
                "count": int(reg.get("count", default_count)),
                "name": str(reg["name"]),
                "type": reg_type,
                "data_type": data_type,
                "scale": float(reg.get("scale", 1.0)),
                "offset": float(reg.get("offset", 0.0)),
                "unit": reg.get("unit"),
            })

        return parsed

    def validate_config(self) -> bool:
        """Validate adapter configuration."""
        if self.mode not in ("tcp", "rtu"):
            raise ValueError(
                f"Invalid mode '{self.mode}'. Must be 'tcp' or 'rtu'"
            )
        if not self.host:
            raise ValueError("Host is required")
        if not self._registers:
            logger.warning("No registers configured — poll() will return empty")
        return True

    def connect(self) -> bool:
        """
        Connect to the Modbus device.

        Creates a ``ModbusTcpClient`` (TCP mode) or ``ModbusSerialClient``
        (RTU mode) and opens the connection.

        Returns:
            True if connection successful.

        Raises:
            ConnectionError: If pymodbus is not installed or connection fails.
        """
        if pymodbus_client is None:
            self._set_state(AdapterState.ERROR, "pymodbus not installed")
            raise ConnectionError(
                "pymodbus is required. Install with: "
                "pip install plexus-python[modbus]"
            )

        try:
            self._set_state(AdapterState.CONNECTING)

            if self.mode == "tcp":
                logger.info(
                    f"Connecting to Modbus TCP {self.host}:{self.port} "
                    f"(unit {self.unit_id})"
                )
                self._client = ModbusTcpClient(
                    host=self.host,
                    port=self.port,
                )
            elif self.mode == "rtu":
                logger.info(
                    f"Connecting to Modbus RTU {self.host} "
                    f"at {self.baudrate} baud (unit {self.unit_id})"
                )
                self._client = ModbusSerialClient(
                    port=self.host,
                    baudrate=self.baudrate,
                )
            else:
                raise ValueError(f"Invalid mode: {self.mode}")

            connected = self._client.connect()
            if not connected:
                self._set_state(
                    AdapterState.ERROR,
                    f"Failed to connect to {self.host}",
                )
                raise ConnectionError(
                    f"Modbus connection failed: {self.host}"
                )

            self._set_state(AdapterState.CONNECTED)
            logger.info(f"Connected to Modbus device at {self.host}")
            return True

        except ConnectionError:
            raise
        except Exception as e:
            self._set_state(AdapterState.ERROR, str(e))
            logger.error(f"Failed to connect to Modbus device: {e}")
            raise ConnectionError(f"Modbus connection failed: {e}")

    def disconnect(self) -> None:
        """Close the Modbus connection and release resources."""
        if self._client:
            try:
                self._client.close()
                logger.info("Disconnected from Modbus device")
            except Exception as e:
                logger.warning(f"Error closing Modbus connection: {e}")
            finally:
                self._client = None

        self._set_state(AdapterState.DISCONNECTED)

    def poll(self) -> List[Metric]:
        """
        Read all configured registers and return metrics.

        For each register definition the appropriate Modbus function code
        is used. Raw register values are converted according to
        ``data_type``, then ``scale`` and ``offset`` are applied:

            value = (raw * scale) + offset

        Returns:
            List of Metric objects — one per configured register.

        Raises:
            ConnectionError/OSError: On connection loss (triggers auto-reconnect).
            ProtocolError: If a Modbus read fails.
        """
        if not self._client:
            return []

        metrics: List[Metric] = []
        timestamp = time.time()

        for reg in self._registers:
            try:
                value = self._read_register(reg)
                if value is None:
                    continue

                tags: Dict[str, str] = {
                    "address": str(reg["address"]),
                    "register_type": reg["type"],
                    "unit_id": str(self.unit_id),
                }
                if reg["unit"]:
                    tags["unit"] = reg["unit"]
                if reg["data_type"] != "bool":
                    tags["data_type"] = reg["data_type"]

                metrics.append(
                    Metric(
                        name=f"{self.prefix}{reg['name']}",
                        value=value,
                        timestamp=timestamp,
                        tags=tags,
                        source_id=self._source_id,
                    )
                )

            except OSError:
                raise  # Let run loop handle disconnect/reconnect
            except Exception as e:
                logger.error(
                    f"Error reading register '{reg['name']}' "
                    f"at address {reg['address']}: {e}"
                )
                raise ProtocolError(
                    f"Modbus read error for '{reg['name']}' "
                    f"at address {reg['address']}: {e}"
                )

        return metrics

    def _read_register(
        self, reg: Dict[str, Any]
    ) -> Optional[Any]:
        """
        Read a single register definition and return the converted value.

        Returns:
            The converted, scaled value — or None if the read failed
            with a Modbus exception.

        Raises:
            ProtocolError: On communication errors.
        """
        address = reg["address"]
        count = reg["count"]
        reg_type = reg["type"]

        # --- Coil / discrete reads (boolean) ---
        if reg_type == "coil":
            result = self._client.read_coils(
                address, count=count, slave=self.unit_id,
            )
        elif reg_type == "discrete":
            result = self._client.read_discrete_inputs(
                address, count=count, slave=self.unit_id,
            )
        elif reg_type == "input":
            result = self._client.read_input_registers(
                address, count=count, slave=self.unit_id,
            )
        elif reg_type == "holding":
            result = self._client.read_holding_registers(
                address, count=count, slave=self.unit_id,
            )
        else:
            raise ProtocolError(f"Unknown register type: {reg_type}")

        # Check for errors
        if result.isError():
            logger.warning(
                f"Modbus error reading {reg_type} register "
                f"at address {address}: {result}"
            )
            return None

        # --- Boolean registers ---
        if reg_type in ("coil", "discrete"):
            # Return first bit value as bool
            return bool(result.bits[0])

        # --- Numeric registers ---
        raw_registers = result.registers
        value = self._convert_registers(
            raw_registers, reg["data_type"]
        )

        # Apply scale and offset
        return (value * reg["scale"]) + reg["offset"]

    @staticmethod
    def _convert_registers(
        registers: List[int], data_type: str
    ) -> float:
        """
        Convert raw 16-bit register values to a typed numeric value.

        For 32-bit types, two consecutive registers are combined
        (big-endian / high word first) and unpacked with ``struct``.

        Args:
            registers: List of raw 16-bit register values.
            data_type: One of "uint16", "int16", "uint32", "int32",
                       "float32".

        Returns:
            The numeric value as a float.

        Raises:
            ProtocolError: If there are not enough registers for the
                           requested data type.
        """
        fmt, expected_count = _DATA_TYPES[data_type]

        if len(registers) < expected_count:
            raise ProtocolError(
                f"Expected {expected_count} register(s) for {data_type}, "
                f"got {len(registers)}"
            )

        if expected_count == 1:
            # Pack single 16-bit register as unsigned, then unpack as target type
            raw_bytes = struct.pack(">H", registers[0])
            (value,) = struct.unpack(fmt, raw_bytes)
        else:
            # Pack two 16-bit registers (high word first) into 4 bytes
            raw_bytes = struct.pack(">HH", registers[0], registers[1])
            (value,) = struct.unpack(fmt, raw_bytes)

        return float(value)

    def write_register(
        self,
        address: int,
        value: int,
        register_type: str = "holding",
    ) -> bool:
        """
        Write a value to a Modbus register.

        Args:
            address: Register address.
            value: Value to write (int for registers, bool-ish for coils).
            register_type: "holding" or "coil".

        Returns:
            True if the write succeeded.

        Raises:
            ProtocolError: If not connected or write fails.
        """
        if not self._client:
            raise ProtocolError("Not connected to Modbus device")

        try:
            if register_type == "coil":
                result = self._client.write_coil(
                    address, bool(value), slave=self.unit_id,
                )
            elif register_type == "holding":
                result = self._client.write_register(
                    address, value, slave=self.unit_id,
                )
            else:
                raise ProtocolError(
                    f"Cannot write to '{register_type}' registers"
                )

            if result.isError():
                logger.error(
                    f"Modbus write error at address {address}: {result}"
                )
                return False

            logger.debug(
                f"Wrote {value} to {register_type} register {address}"
            )
            return True

        except ProtocolError:
            raise
        except Exception as e:
            logger.error(f"Failed to write Modbus register: {e}")
            raise ProtocolError(f"Modbus write error: {e}")

    def write_registers(
        self,
        address: int,
        values: List[int],
    ) -> bool:
        """
        Write multiple holding register values starting at an address.

        Args:
            address: Starting register address.
            values: List of 16-bit integer values to write.

        Returns:
            True if the write succeeded.

        Raises:
            ProtocolError: If not connected or write fails.
        """
        if not self._client:
            raise ProtocolError("Not connected to Modbus device")

        try:
            result = self._client.write_registers(
                address, values, slave=self.unit_id,
            )
            if result.isError():
                logger.error(
                    f"Modbus write error at address {address}: {result}"
                )
                return False

            logger.debug(
                f"Wrote {len(values)} registers starting at {address}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to write Modbus registers: {e}")
            raise ProtocolError(f"Modbus write error: {e}")

    @property
    def stats(self) -> Dict[str, Any]:
        """Get adapter statistics including Modbus-specific info."""
        base_stats = super().stats
        base_stats.update({
            "host": self.host,
            "port": self.port,
            "mode": self.mode,
            "unit_id": self.unit_id,
            "register_count": len(self._registers),
            "poll_interval": self.poll_interval,
        })
        return base_stats
