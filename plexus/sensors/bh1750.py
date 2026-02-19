"""
BH1750 ambient light sensor driver.

The BH1750 measures ambient light intensity in lux.
Communicates via I2C at address 0x23 (ADDR pin low) or 0x5C (ADDR pin high).

Usage:
    from plexus.sensors import BH1750

    sensor = BH1750()
    for reading in sensor.read():
        print(f"{reading.metric}: {reading.value}")
"""

import time
from typing import List, Optional
from .base import BaseSensor, SensorReading

BH1750_ADDR = 0x23
BH1750_ADDR_ALT = 0x5C

# Commands
CMD_POWER_ON = 0x01
CMD_RESET = 0x07
CMD_CONT_HRES = 0x10   # Continuous high-resolution mode (1 lux, 120ms)
CMD_CONT_HRES2 = 0x11  # Continuous high-resolution mode 2 (0.5 lux, 120ms)
CMD_ONCE_HRES = 0x20   # One-time high-resolution mode (1 lux, 120ms)


class BH1750(BaseSensor):
    """
    BH1750 ambient light sensor driver.

    Provides:
    - light_lux: Ambient light intensity in lux (1-65535 lux range)
    """

    name = "BH1750"
    description = "Ambient light sensor (1-65535 lux)"
    metrics = ["light_lux"]
    i2c_addresses = [BH1750_ADDR, BH1750_ADDR_ALT]

    def __init__(
        self,
        address: int = BH1750_ADDR,
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
            from smbus2 import SMBus
        except ImportError:
            raise ImportError(
                "smbus2 is required for BH1750. Install with: pip install smbus2"
            )

        self._bus = SMBus(self.bus_num)

        # Power on and set continuous high-res mode
        self._bus.write_byte(self.address, CMD_POWER_ON)
        time.sleep(0.01)
        self._bus.write_byte(self.address, CMD_CONT_HRES)
        time.sleep(0.180)  # First measurement takes up to 180ms

    def cleanup(self) -> None:
        if self._bus:
            self._bus.close()
            self._bus = None

    def read(self) -> List[SensorReading]:
        if self._bus is None:
            self.setup()

        # Read 2 bytes of light data
        data = self._bus.read_i2c_block_data(self.address, CMD_CONT_HRES, 2)
        raw = (data[0] << 8) | data[1]

        # Convert to lux (divide by 1.2 per datasheet)
        lux = raw / 1.2

        return [
            SensorReading("light_lux", round(lux, 1)),
        ]

    def is_available(self) -> bool:
        try:
            from smbus2 import SMBus

            bus = SMBus(self.bus_num)
            # Power on command — if device ACKs, it's there
            bus.write_byte(self.address, CMD_POWER_ON)
            bus.close()
            return True
        except Exception:
            return False
