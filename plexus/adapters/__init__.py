"""
Protocol Adapters - Extensible protocol support for Plexus

This module provides a plugin system for protocol adapters, enabling
Plexus to ingest data from any protocol without modifying core code.

Built-in adapters:
    - MQTTAdapter: Bridge MQTT brokers to Plexus
    - CANAdapter: CAN bus with DBC signal decoding
    - MAVLinkAdapter: MAVLink for drones and autonomous vehicles
    - OPCUAAdapter: OPC-UA client for industrial automation servers
    - SerialAdapter: Serial port (USB/UART/RS-232/RS-485) reader

Usage:
    from plexus.adapters import MQTTAdapter, CANAdapter, MAVLinkAdapter, AdapterRegistry

    # Use built-in adapter
    adapter = MQTTAdapter(broker="localhost", topic="sensors/#")
    adapter.connect()
    adapter.run(on_data=my_callback)

    # CAN bus adapter
    adapter = CANAdapter(interface="socketcan", channel="can0", dbc_path="vehicle.dbc")
    with adapter:
        for metric in adapter.poll():
            print(f"{metric.name}: {metric.value}")

    # MAVLink adapter
    adapter = MAVLinkAdapter(connection_string="udpin:0.0.0.0:14550")
    with adapter:
        for metric in adapter.poll():
            print(f"{metric.name}: {metric.value}")

    # Create custom adapter
    from plexus.adapters import ProtocolAdapter, Metric

    class MyProtocolAdapter(ProtocolAdapter):
        def connect(self) -> bool:
            # Connect to your protocol
            return True

        def poll(self) -> "List[Metric]":
            # Read data and return metrics
            return [Metric("sensor.temp", 72.5)]

    # Register custom adapter
    AdapterRegistry.register("my-protocol", MyProtocolAdapter)
"""

from plexus.adapters.base import (
    ProtocolAdapter,
    Metric,
    AdapterConfig,
    AdapterState,
    AdapterError,
)
from plexus.adapters.registry import AdapterRegistry
from plexus.adapters.mqtt import MQTTAdapter

# Import CANAdapter (requires optional [can] extra)
try:
    from plexus.adapters.can import CANAdapter
    _HAS_CAN = True
except ImportError:
    CANAdapter = None  # type: ignore
    _HAS_CAN = False

# Import ModbusAdapter (requires optional [modbus] extra)
try:
    from plexus.adapters.modbus import ModbusAdapter
    _HAS_MODBUS = True
except ImportError:
    ModbusAdapter = None  # type: ignore
    _HAS_MODBUS = False

# Import MAVLinkAdapter (requires optional [mavlink] extra)
try:
    from plexus.adapters.mavlink import MAVLinkAdapter
    _HAS_MAVLINK = True
except ImportError:
    MAVLinkAdapter = None  # type: ignore
    _HAS_MAVLINK = False

# Import OPCUAAdapter (requires optional [opcua] extra)
try:
    from plexus.adapters.opcua import OPCUAAdapter
    _HAS_OPCUA = True
except ImportError:
    OPCUAAdapter = None  # type: ignore
    _HAS_OPCUA = False

# Import SerialAdapter (requires optional [serial] extra)
try:
    from plexus.adapters.serial_adapter import SerialAdapter
    _HAS_SERIAL = True
except ImportError:
    SerialAdapter = None  # type: ignore
    _HAS_SERIAL = False

__all__ = [
    "ProtocolAdapter",
    "Metric",
    "AdapterConfig",
    "AdapterState",
    "AdapterError",
    "AdapterRegistry",
    "MQTTAdapter",
    "CANAdapter",
    "ModbusAdapter",
    "MAVLinkAdapter",
    "OPCUAAdapter",
    "SerialAdapter",
]
