"""
Protocol Adapters - Extensible protocol support for Plexus

This module provides a plugin system for protocol adapters, enabling
Plexus to ingest data from any protocol without modifying core code.

Built-in adapters:
    - MQTTAdapter: Bridge MQTT brokers to Plexus
    - (Coming soon) ModbusAdapter, CANAdapter, SerialAdapter, OPCUAAdapter

Usage:
    from plexus.adapters import MQTTAdapter, AdapterRegistry

    # Use built-in adapter
    adapter = MQTTAdapter(broker="localhost", topic="sensors/#")
    adapter.connect()
    adapter.run(on_data=my_callback)

    # Create custom adapter
    from plexus.adapters import ProtocolAdapter, Metric

    class MyProtocolAdapter(ProtocolAdapter):
        def connect(self) -> bool:
            # Connect to your protocol
            return True

        def poll(self) -> list[Metric]:
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

__all__ = [
    "ProtocolAdapter",
    "Metric",
    "AdapterConfig",
    "AdapterState",
    "AdapterError",
    "AdapterRegistry",
    "MQTTAdapter",
]
