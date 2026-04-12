"""
ADXL345 3-axis accelerometer driver.

Supports both I2C and SPI interfaces for the ADXL345 digital accelerometer.
Measures acceleration on X, Y, Z axes in units of g (9.81 m/s²).

Usage (I2C):
    from plexus.sensors import ADXL345

    accel = ADXL345(bus_type="i2c", address=0x53)
    accel.setup()
    for reading in accel.read():
        print(f"{reading.metric}: {reading.value}")

Usage (SPI):
    accel = ADXL345(bus_type="spi", spi_bus=0, spi_cs=0)
    accel.setup()
    for reading in accel.read():
        print(f"{reading.metric}: {reading.value}")

With SensorHub:
    from plexus.sensors import SensorHub, ADXL345
    hub = SensorHub()
    hub.add(ADXL345())
    hub.run(Plexus())
"""

import struct
import time
from typing import Dict, List, Optional

from .base import BaseSensor, SensorReading


# ADXL345 registers
_REG_DEVID = 0x00
_REG_BW_RATE = 0x2C
_REG_POWER_CTL = 0x2D
_REG_DATA_FORMAT = 0x31
_REG_DATAX0 = 0x32

# Expected chip ID
_CHIP_ID = 0xE5

# Scale factor: 4mg/LSB in full resolution mode
_SCALE_FACTOR = 0.004


class ADXL345(BaseSensor):
    """ADXL345 3-axis accelerometer (I2C or SPI)."""

    name = "ADXL345"
    description = "3-axis accelerometer (±2g/±4g/±8g/±16g)"
    metrics = ["accel_x", "accel_y", "accel_z"]
    i2c_addresses = [0x53, 0x1D]
    spi_devices = [(0, 0)]

    def __init__(
        self,
        bus_type: str = "i2c",
        address: int = 0x53,
        bus: int = 1,
        spi_bus: int = 0,
        spi_cs: int = 0,
        sample_rate: float = 100.0,
        prefix: str = "",
        tags: Optional[Dict[str, str]] = None,
    ):
        """
        Args:
            bus_type: "i2c" or "spi"
            address: I2C address (0x53 or 0x1D)
            bus: I2C bus number (usually 1 on Raspberry Pi)
            spi_bus: SPI bus number
            spi_cs: SPI chip select
            sample_rate: Readings per second (Hz)
            prefix: Prefix for metric names
            tags: Tags to add to all readings
        """
        super().__init__(sample_rate=sample_rate, prefix=prefix, tags=tags)
        self.bus_type = bus_type
        self.address = address
        self.bus_num = bus
        self.spi_bus = spi_bus
        self.spi_cs = spi_cs
        self._dev = None

    def _read_reg(self, reg: int, length: int = 1) -> bytes:
        """Read register(s) via I2C or SPI."""
        if self.bus_type == "spi":
            # SPI read: bit 7 = read, bit 6 = multi-byte
            cmd = reg | 0x80
            if length > 1:
                cmd |= 0x40
            tx = [cmd] + [0x00] * length
            rx = self._dev.xfer2(tx)
            return bytes(rx[1:])
        else:
            if length == 1:
                return bytes([self._dev.read_byte_data(self.address, reg)])
            return bytes(self._dev.read_i2c_block_data(self.address, reg, length))

    def _write_reg(self, reg: int, value: int):
        """Write a register via I2C or SPI."""
        if self.bus_type == "spi":
            self._dev.xfer2([reg, value])
        else:
            self._dev.write_byte_data(self.address, reg, value)

    def setup(self) -> None:
        """Initialize the ADXL345."""
        if self.bus_type == "spi":
            import spidev
            self._dev = spidev.SpiDev()
            self._dev.open(self.spi_bus, self.spi_cs)
            self._dev.mode = 3
            self._dev.max_speed_hz = 1000000
        else:
            from smbus2 import SMBus
            self._dev = SMBus(self.bus_num)

        # Verify chip ID
        chip_id = self._read_reg(_REG_DEVID)[0]
        if chip_id != _CHIP_ID:
            raise RuntimeError(
                f"ADXL345 not found (got chip ID 0x{chip_id:02X}, expected 0x{_CHIP_ID:02X})"
            )

        # Configure: 100Hz output rate
        self._write_reg(_REG_BW_RATE, 0x0A)
        # Full resolution, ±2g range
        self._write_reg(_REG_DATA_FORMAT, 0x08)
        # Start measurement
        self._write_reg(_REG_POWER_CTL, 0x08)

    def cleanup(self) -> None:
        """Clean up resources."""
        if self._dev:
            if self.bus_type == "spi":
                self._dev.close()
            else:
                self._dev.close()
            self._dev = None

    def read(self) -> List[SensorReading]:
        """Read acceleration on all three axes."""
        if not self._dev:
            return []

        now = time.time()
        raw = self._read_reg(_REG_DATAX0, 6)
        x, y, z = struct.unpack("<hhh", raw)

        return [
            SensorReading("accel_x", round(x * _SCALE_FACTOR, 4), now),
            SensorReading("accel_y", round(y * _SCALE_FACTOR, 4), now),
            SensorReading("accel_z", round(z * _SCALE_FACTOR, 4), now),
        ]

    def is_available(self) -> bool:
        """Check if ADXL345 is connected."""
        try:
            if self.bus_type == "spi":
                import spidev
                dev = spidev.SpiDev()
                dev.open(self.spi_bus, self.spi_cs)
                dev.mode = 3
                dev.max_speed_hz = 1000000
                resp = dev.xfer2([_REG_DEVID | 0x80, 0x00])
                dev.close()
                return resp[1] == _CHIP_ID
            else:
                from smbus2 import SMBus
                bus = SMBus(self.bus_num)
                chip_id = bus.read_byte_data(self.address, _REG_DEVID)
                bus.close()
                return chip_id == _CHIP_ID
        except Exception:
            return False
