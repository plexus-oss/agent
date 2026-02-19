"""
INA219 current/power monitor sensor driver.

The INA219 measures bus voltage, shunt voltage, current, and power.
Communicates via I2C at address 0x40–0x4F (configurable via A0/A1 pins).

Usage:
    from plexus.sensors import INA219

    sensor = INA219()
    for reading in sensor.read():
        print(f"{reading.metric}: {reading.value}")
"""

from typing import List, Optional
from .base import BaseSensor, SensorReading

# Default I2C address
INA219_ADDR = 0x40

# Register addresses
REG_CONFIG = 0x00
REG_SHUNT_VOLTAGE = 0x01
REG_BUS_VOLTAGE = 0x02
REG_POWER = 0x03
REG_CURRENT = 0x04
REG_CALIBRATION = 0x05

# Configuration: 32V range, 320mV shunt range, 12-bit, continuous
CONFIG_DEFAULT = 0x399F

# Calibration for 0.1 ohm shunt resistor, max expected current 3.2A
# Cal = trunc(0.04096 / (current_lsb * r_shunt))
# current_lsb = max_expected / 2^15 = 3.2 / 32768 ≈ 0.0001
SHUNT_RESISTOR_OHMS = 0.1
CURRENT_LSB = 0.0001  # 100uA per bit
CAL_VALUE = int(0.04096 / (CURRENT_LSB * SHUNT_RESISTOR_OHMS))


class INA219(BaseSensor):
    """
    INA219 current/power monitor driver.

    Provides:
    - bus_voltage: Bus voltage in volts
    - shunt_voltage: Shunt voltage in millivolts
    - current_ma: Current in milliamps
    - power_mw: Power in milliwatts
    """

    name = "INA219"
    description = "Current/power monitor (voltage, current, power)"
    metrics = ["bus_voltage", "shunt_voltage", "current_ma", "power_mw"]
    i2c_addresses = [0x40, 0x41, 0x44, 0x45]

    def __init__(
        self,
        address: int = INA219_ADDR,
        bus: int = 1,
        shunt_ohms: float = SHUNT_RESISTOR_OHMS,
        max_current: float = 3.2,
        sample_rate: float = 10.0,
        prefix: str = "",
        tags: Optional[dict] = None,
    ):
        super().__init__(sample_rate=sample_rate, prefix=prefix, tags=tags)
        self.address = address
        self.bus_num = bus
        self.shunt_ohms = shunt_ohms
        self.max_current = max_current
        self._bus = None
        self._current_lsb = max_current / 32768.0
        self._cal_value = int(0.04096 / (self._current_lsb * shunt_ohms))

    def setup(self) -> None:
        try:
            from smbus2 import SMBus
        except ImportError:
            raise ImportError(
                "smbus2 is required for INA219. Install with: pip install smbus2"
            )

        self._bus = SMBus(self.bus_num)

        # Write configuration
        self._write_register(REG_CONFIG, CONFIG_DEFAULT)
        # Write calibration
        self._write_register(REG_CALIBRATION, self._cal_value)

    def cleanup(self) -> None:
        if self._bus:
            self._bus.close()
            self._bus = None

    def _write_register(self, reg: int, value: int) -> None:
        high = (value >> 8) & 0xFF
        low = value & 0xFF
        self._bus.write_i2c_block_data(self.address, reg, [high, low])

    def _read_register(self, reg: int) -> int:
        data = self._bus.read_i2c_block_data(self.address, reg, 2)
        value = (data[0] << 8) | data[1]
        return value

    def _read_signed(self, reg: int) -> int:
        value = self._read_register(reg)
        if value > 32767:
            value -= 65536
        return value

    def read(self) -> List[SensorReading]:
        if self._bus is None:
            self.setup()

        # Bus voltage: bits [15:3] * 4mV, bit 1 = conversion ready
        raw_bus = self._read_register(REG_BUS_VOLTAGE)
        bus_voltage = (raw_bus >> 3) * 0.004  # 4mV per LSB

        # Shunt voltage: signed 16-bit, 10uV per LSB
        raw_shunt = self._read_signed(REG_SHUNT_VOLTAGE)
        shunt_voltage_mv = raw_shunt * 0.01  # 10uV = 0.01mV

        # Current: signed 16-bit * current_lsb
        raw_current = self._read_signed(REG_CURRENT)
        current_ma = raw_current * self._current_lsb * 1000.0

        # Power: unsigned 16-bit * 20 * current_lsb
        raw_power = self._read_register(REG_POWER)
        power_mw = raw_power * 20.0 * self._current_lsb * 1000.0

        return [
            SensorReading("bus_voltage", round(bus_voltage, 3)),
            SensorReading("shunt_voltage", round(shunt_voltage_mv, 3)),
            SensorReading("current_ma", round(current_ma, 2)),
            SensorReading("power_mw", round(power_mw, 2)),
        ]

    def is_available(self) -> bool:
        try:
            from smbus2 import SMBus

            bus = SMBus(self.bus_num)
            # Read config register — should return non-zero default
            data = bus.read_i2c_block_data(self.address, REG_CONFIG, 2)
            bus.close()
            config = (data[0] << 8) | data[1]
            return config != 0x0000 and config != 0xFFFF
        except Exception:
            return False
