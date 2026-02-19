"""
SHT3x precision temperature and humidity sensor driver.

The SHT31/SHT35 provides high-accuracy temperature (±0.2°C) and
humidity (±2%) readings. Communicates via I2C at address 0x44 or 0x45.

Usage:
    from plexus.sensors import SHT3x

    sensor = SHT3x()
    for reading in sensor.read():
        print(f"{reading.metric}: {reading.value}")
"""

import time
from typing import List, Optional
from .base import BaseSensor, SensorReading

SHT3X_ADDR = 0x44
SHT3X_ADDR_ALT = 0x45

# Single-shot measurement commands (clock stretching disabled)
CMD_MEAS_HIGH = [0x24, 0x00]    # High repeatability
CMD_MEAS_MEDIUM = [0x24, 0x0B]  # Medium repeatability
CMD_MEAS_LOW = [0x24, 0x16]     # Low repeatability

# Status register
CMD_STATUS = [0xF3, 0x2D]

# Soft reset
CMD_RESET = [0x30, 0xA2]


def _crc8(data: bytes) -> int:
    """CRC-8 check per SHT3x datasheet (polynomial 0x31)."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x31
            else:
                crc = crc << 1
            crc &= 0xFF
    return crc


class SHT3x(BaseSensor):
    """
    SHT3x (SHT31/SHT35) precision humidity and temperature sensor.

    Provides:
    - sht_temperature: Temperature in °C (±0.2°C accuracy)
    - sht_humidity: Relative humidity in % (±2% accuracy)
    """

    name = "SHT3x"
    description = "Precision temperature and humidity (±0.2°C, ±2% RH)"
    metrics = ["sht_temperature", "sht_humidity"]
    i2c_addresses = [SHT3X_ADDR, SHT3X_ADDR_ALT]

    def __init__(
        self,
        address: int = SHT3X_ADDR,
        bus: int = 1,
        sample_rate: float = 1.0,
        prefix: str = "",
        tags: Optional[dict] = None,
    ):
        super().__init__(sample_rate=sample_rate, prefix=prefix, tags=tags)
        self.address = address
        self.bus_num = bus
        self._bus = None

    def setup(self) -> None:
        try:
            from smbus2 import SMBus, i2c_msg
        except ImportError:
            raise ImportError(
                "smbus2 is required for SHT3x. Install with: pip install smbus2"
            )

        self._bus = SMBus(self.bus_num)

        # Soft reset
        self._bus.write_i2c_block_data(self.address, CMD_RESET[0], [CMD_RESET[1]])
        time.sleep(0.002)  # 1.5ms reset time

    def cleanup(self) -> None:
        if self._bus:
            self._bus.close()
            self._bus = None

    def read(self) -> List[SensorReading]:
        if self._bus is None:
            self.setup()

        # Trigger single-shot measurement (high repeatability)
        self._bus.write_i2c_block_data(self.address, CMD_MEAS_HIGH[0], [CMD_MEAS_HIGH[1]])
        time.sleep(0.016)  # 15.5ms max for high repeatability

        # Read 6 bytes: temp_msb, temp_lsb, temp_crc, hum_msb, hum_lsb, hum_crc
        data = self._bus.read_i2c_block_data(self.address, 0x00, 6)

        # Verify CRCs
        if _crc8(bytes(data[0:2])) != data[2]:
            return []  # Temperature CRC mismatch
        if _crc8(bytes(data[3:5])) != data[5]:
            return []  # Humidity CRC mismatch

        # Convert raw values
        raw_temp = (data[0] << 8) | data[1]
        raw_hum = (data[3] << 8) | data[4]

        temperature = -45.0 + 175.0 * (raw_temp / 65535.0)
        humidity = 100.0 * (raw_hum / 65535.0)

        # Clamp humidity to valid range
        humidity = max(0.0, min(100.0, humidity))

        return [
            SensorReading("sht_temperature", round(temperature, 2)),
            SensorReading("sht_humidity", round(humidity, 1)),
        ]

    def is_available(self) -> bool:
        try:
            from smbus2 import SMBus

            bus = SMBus(self.bus_num)
            # Read status register
            bus.write_i2c_block_data(self.address, CMD_STATUS[0], [CMD_STATUS[1]])
            time.sleep(0.001)
            data = bus.read_i2c_block_data(self.address, 0x00, 3)
            bus.close()
            # Check CRC of status
            return _crc8(bytes(data[0:2])) == data[2]
        except Exception:
            return False
