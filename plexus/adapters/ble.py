"""
BLE Relay Adapter - Bluetooth Low Energy gateway support

This adapter scans for BLE peripherals and reads GATT characteristics,
acting as a relay/gateway for BLE-only devices that can't reach the
internet directly. Ideal for RPi or Linux gateways.

Requirements:
    pip install plexus-python[ble]
    # or
    pip install bleak

Usage:
    from plexus.adapters import BLERelayAdapter

    adapter = BLERelayAdapter(
        service_uuids=["181A"],  # Environmental Sensing
        scan_duration=5.0,
    )
    adapter.connect()
    for metric in adapter.poll():
        print(f"{metric.name}: {metric.value}")

Emitted metrics:
    - {source_prefix}.{device_name}.{char_uuid} - Raw characteristic values
    - {source_prefix}.{device_name}.rssi - Signal strength
"""

from typing import Any, Dict, List, Optional
import asyncio
import logging
import struct
import time

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

try:
    import bleak
    from bleak import BleakScanner, BleakClient
except ImportError:
    bleak = None  # type: ignore[assignment]
    BleakScanner = None  # type: ignore[assignment,misc]
    BleakClient = None  # type: ignore[assignment,misc]

# Well-known GATT characteristic UUIDs → human-readable names
_KNOWN_CHARACTERISTICS: Dict[str, str] = {
    "00002a6e-0000-1000-8000-00805f9b34fb": "temperature",
    "00002a6f-0000-1000-8000-00805f9b34fb": "humidity",
    "00002a6d-0000-1000-8000-00805f9b34fb": "pressure",
    "00002a19-0000-1000-8000-00805f9b34fb": "battery_level",
    "00002a1c-0000-1000-8000-00805f9b34fb": "temperature_measurement",
}


def _slugify(name: str) -> str:
    """Convert a BLE device name to a metric-safe slug."""
    return name.lower().replace(" ", "_").replace("-", "_")[:32]


def _try_decode_value(data: bytes, char_uuid: str) -> Any:
    """Attempt to decode a characteristic value to a numeric type."""
    uuid_lower = char_uuid.lower()

    # Battery level is a single uint8 percentage
    if uuid_lower == "00002a19-0000-1000-8000-00805f9b34fb":
        return data[0] if len(data) >= 1 else None

    # Temperature (sint16, 0.01 degC resolution per BLE SIG)
    if uuid_lower == "00002a6e-0000-1000-8000-00805f9b34fb":
        if len(data) >= 2:
            raw = struct.unpack("<h", data[:2])[0]
            return raw / 100.0
        return None

    # Humidity (uint16, 0.01% resolution)
    if uuid_lower == "00002a6f-0000-1000-8000-00805f9b34fb":
        if len(data) >= 2:
            raw = struct.unpack("<H", data[:2])[0]
            return raw / 100.0
        return None

    # Pressure (uint32, 0.1 Pa resolution)
    if uuid_lower == "00002a6d-0000-1000-8000-00805f9b34fb":
        if len(data) >= 4:
            raw = struct.unpack("<I", data[:4])[0]
            return raw / 10.0
        return None

    # Generic: try to interpret as a number
    if len(data) == 1:
        return data[0]
    if len(data) == 2:
        return struct.unpack("<h", data[:2])[0]
    if len(data) == 4:
        return struct.unpack("<f", data[:4])[0]

    # Fall back to hex string
    return data.hex()


@register_adapter(
    "ble",
    description="BLE relay adapter for gateway devices",
    author="Plexus",
    version="1.0.0",
    requires=["bleak"],
)
class BLERelayAdapter(ProtocolAdapter):
    """
    BLE Relay protocol adapter.

    Scans for BLE peripherals, connects, and reads GATT characteristics.
    Designed for gateway scenarios where a Linux host (e.g., RPi) relays
    BLE sensor data to Plexus cloud.

    Args:
        service_uuids: List of GATT service UUIDs to filter for (short or full form).
        scan_duration: How long to scan for devices per poll cycle (seconds).
        name_filter: Only connect to devices whose name contains this string.
        source_prefix: Prefix for emitted metric names.
        read_timeout: Timeout for reading each characteristic (seconds).
    """

    def __init__(
        self,
        service_uuids: Optional[List[str]] = None,
        scan_duration: float = 5.0,
        name_filter: Optional[str] = None,
        source_prefix: str = "ble",
        read_timeout: float = 10.0,
        **kwargs: Any,
    ):
        if bleak is None:
            raise ImportError(
                "BLE adapter requires 'bleak'. Install with: pip install plexus-python[ble]"
            )

        config = AdapterConfig(
            name="ble",
            params={
                "service_uuids": service_uuids,
                "scan_duration": scan_duration,
                "name_filter": name_filter,
                "source_prefix": source_prefix,
                **kwargs,
            },
        )
        super().__init__(config)
        self.service_uuids = service_uuids or []
        self.scan_duration = scan_duration
        self.name_filter = name_filter
        self.source_prefix = source_prefix
        self.read_timeout = read_timeout
        self._scanner: Any = None

    def connect(self) -> bool:
        """Initialize the BLE scanner."""
        try:
            self._scanner = BleakScanner(
                service_uuids=self.service_uuids if self.service_uuids else None,
            )
            self._set_state(AdapterState.CONNECTED)
            logger.info("BLE adapter ready (filter: %s)", self.name_filter or "none")
            return True
        except Exception as e:
            self._set_state(AdapterState.ERROR, str(e))
            raise ConnectionError(f"Failed to initialize BLE scanner: {e}") from e

    def disconnect(self) -> None:
        """Stop the BLE scanner."""
        self._scanner = None
        self._set_state(AdapterState.DISCONNECTED)

    def poll(self) -> List[Metric]:
        """Scan for BLE devices and read their characteristics."""
        if not self._scanner:
            raise ProtocolError("BLE adapter not connected")

        try:
            return asyncio.run(self._async_poll())
        except RuntimeError:
            # If there's already an event loop running, create a new one
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._async_poll())
            finally:
                loop.close()

    async def _async_poll(self) -> List[Metric]:
        """Async implementation of the poll cycle."""
        metrics: List[Metric] = []
        now = time.time()

        # Scan for devices
        devices = await BleakScanner.discover(
            timeout=self.scan_duration,
            service_uuids=self.service_uuids if self.service_uuids else None,
        )

        for device in devices:
            # Apply name filter
            if self.name_filter and device.name:
                if self.name_filter.lower() not in device.name.lower():
                    continue
            elif self.name_filter and not device.name:
                continue

            device_name = _slugify(device.name or device.address.replace(":", ""))

            # Emit RSSI as a metric
            if device.rssi is not None:
                metrics.append(Metric(
                    name=f"{self.source_prefix}.{device_name}.rssi",
                    value=device.rssi,
                    timestamp=now,
                ))

            # Connect and read characteristics
            try:
                async with BleakClient(device.address, timeout=self.read_timeout) as client:
                    for service in client.services:
                        for char in service.characteristics:
                            if "read" not in char.properties:
                                continue
                            try:
                                data = await client.read_gatt_char(char.uuid)
                                value = _try_decode_value(data, char.uuid)
                                if value is not None:
                                    char_name = _KNOWN_CHARACTERISTICS.get(
                                        char.uuid.lower(),
                                        char.uuid.split("-")[0],
                                    )
                                    metrics.append(Metric(
                                        name=f"{self.source_prefix}.{device_name}.{char_name}",
                                        value=value,
                                        timestamp=now,
                                    ))
                            except Exception as e:
                                logger.debug(
                                    "Failed to read %s from %s: %s",
                                    char.uuid, device_name, e,
                                )
            except Exception as e:
                logger.warning("Failed to connect to %s: %s", device_name, e)

        logger.debug("BLE poll: %d metrics from %d devices", len(metrics), len(devices))
        return metrics
