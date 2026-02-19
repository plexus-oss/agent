"""
ADS1115 16-bit ADC sensor driver.

The ADS1115 is a precision 16-bit ADC with 4 single-ended or 2 differential
channels. Programmable gain amplifier (PGA) and data rate.
Communicates via I2C at address 0x48–0x4B (configurable via ADDR pin).

Usage:
    from plexus.sensors import ADS1115

    sensor = ADS1115()
    for reading in sensor.read():
        print(f"{reading.metric}: {reading.value}")
"""

import time
from typing import List, Optional
from .base import BaseSensor, SensorReading

ADS1115_ADDR = 0x48

# Register addresses
REG_CONVERSION = 0x00
REG_CONFIG = 0x01

# Config register bits
# OS: Start single conversion
OS_SINGLE = 0x8000
# MUX: Input multiplexer
MUX_AIN0 = 0x4000  # AIN0 vs GND
MUX_AIN1 = 0x5000  # AIN1 vs GND
MUX_AIN2 = 0x6000  # AIN2 vs GND
MUX_AIN3 = 0x7000  # AIN3 vs GND
# PGA: Programmable gain (full-scale voltage)
PGA_6144 = 0x0000   # ±6.144V (LSB = 187.5µV)
PGA_4096 = 0x0200   # ±4.096V (LSB = 125µV)
PGA_2048 = 0x0400   # ±2.048V (LSB = 62.5µV) — default
PGA_1024 = 0x0600   # ±1.024V
PGA_0512 = 0x0800   # ±0.512V
PGA_0256 = 0x0A00   # ±0.256V
# MODE: Operating mode
MODE_SINGLE = 0x0100  # Single-shot
# DR: Data rate
DR_128SPS = 0x0080  # 128 samples per second
# COMP: Disable comparator
COMP_DISABLE = 0x0003

MUX_CHANNELS = [MUX_AIN0, MUX_AIN1, MUX_AIN2, MUX_AIN3]

# Voltage per LSB for each PGA setting
PGA_LSB = {
    PGA_6144: 0.0001875,
    PGA_4096: 0.000125,
    PGA_2048: 0.0000625,
    PGA_1024: 0.00003125,
    PGA_0512: 0.000015625,
    PGA_0256: 0.0000078125,
}


class ADS1115(BaseSensor):
    """
    ADS1115 16-bit ADC driver.

    Provides:
    - adc_ch0 through adc_ch3: Voltage readings in volts (single-ended)

    Default gain: ±4.096V (suitable for 3.3V/5V systems)
    """

    name = "ADS1115"
    description = "16-bit ADC (4 channels, programmable gain)"
    metrics = ["adc_ch0", "adc_ch1", "adc_ch2", "adc_ch3"]
    i2c_addresses = [0x48, 0x49, 0x4A, 0x4B]

    def __init__(
        self,
        address: int = ADS1115_ADDR,
        bus: int = 1,
        gain: int = PGA_4096,
        channels: Optional[List[int]] = None,
        sample_rate: float = 10.0,
        prefix: str = "",
        tags: Optional[dict] = None,
    ):
        super().__init__(sample_rate=sample_rate, prefix=prefix, tags=tags)
        self.address = address
        self.bus_num = bus
        self.gain = gain
        self.channels = channels if channels is not None else [0, 1, 2, 3]
        self._bus = None
        self._lsb = PGA_LSB.get(gain, 0.000125)

    def setup(self) -> None:
        try:
            from smbus2 import SMBus
        except ImportError:
            raise ImportError(
                "smbus2 is required for ADS1115. Install with: pip install smbus2"
            )

        self._bus = SMBus(self.bus_num)

    def cleanup(self) -> None:
        if self._bus:
            self._bus.close()
            self._bus = None

    def _read_channel(self, channel: int) -> float:
        """Read a single ADC channel and return voltage."""
        config = (
            OS_SINGLE |
            MUX_CHANNELS[channel] |
            self.gain |
            MODE_SINGLE |
            DR_128SPS |
            COMP_DISABLE
        )

        # Write config to start conversion
        high = (config >> 8) & 0xFF
        low = config & 0xFF
        self._bus.write_i2c_block_data(self.address, REG_CONFIG, [high, low])

        # Wait for conversion (128 SPS = ~8ms per sample)
        time.sleep(0.009)

        # Read conversion result
        data = self._bus.read_i2c_block_data(self.address, REG_CONVERSION, 2)
        raw = (data[0] << 8) | data[1]

        # Convert to signed
        if raw > 32767:
            raw -= 65536

        return raw * self._lsb

    def read(self) -> List[SensorReading]:
        if self._bus is None:
            self.setup()

        readings = []
        for ch in self.channels:
            if 0 <= ch <= 3:
                voltage = self._read_channel(ch)
                readings.append(
                    SensorReading(f"adc_ch{ch}", round(voltage, 4))
                )

        return readings

    def is_available(self) -> bool:
        try:
            from smbus2 import SMBus

            bus = SMBus(self.bus_num)
            # Read config register — default is 0x8583
            data = bus.read_i2c_block_data(self.address, REG_CONFIG, 2)
            bus.close()
            config = (data[0] << 8) | data[1]
            # Check OS bit is set (no conversion in progress)
            return (config & 0x8000) != 0
        except Exception:
            return False
