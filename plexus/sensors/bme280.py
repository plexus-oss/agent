"""
BME280 environmental sensor driver.

The BME280 provides temperature, humidity, and pressure readings.
Communicates via I2C at address 0x76 or 0x77.

Usage:
    from plexus import Plexus
    from plexus.sensors import BME280

    px = Plexus()
    env = BME280()

    while True:
        for reading in env.read():
            px.send(reading.metric, reading.value)
        time.sleep(1)  # 1 Hz is typical for environmental

Or with SensorHub:
    from plexus.sensors import SensorHub, BME280

    hub = SensorHub()
    hub.add(BME280(sample_rate=1))
    hub.run(Plexus())
"""

from typing import List, Optional
from .base import BaseSensor, SensorReading

# I2C addresses
BME280_ADDR = 0x76
BME280_ADDR_ALT = 0x77

# Register addresses
BME280_CHIP_ID_REG = 0xD0
BME280_CTRL_HUM = 0xF2
BME280_CTRL_MEAS = 0xF4
BME280_CONFIG = 0xF5
BME280_DATA_START = 0xF7
BME280_CALIB_START = 0x88
BME280_CALIB_HUM_START = 0xE1


class BME280(BaseSensor):
    """
    BME280 environmental sensor driver.

    Provides:
    - temperature: Temperature in Celsius
    - humidity: Relative humidity in %
    - pressure: Atmospheric pressure in hPa
    """

    name = "BME280"
    description = "Environmental sensor (temperature, humidity, pressure)"
    metrics = ["temperature", "humidity", "pressure"]
    i2c_addresses = [BME280_ADDR, BME280_ADDR_ALT]

    def __init__(
        self,
        address: int = BME280_ADDR,
        bus: int = 1,
        sample_rate: float = 1.0,
        prefix: str = "",
        tags: Optional[dict] = None,
    ):
        """
        Initialize BME280 driver.

        Args:
            address: I2C address (0x76 or 0x77)
            bus: I2C bus number (usually 1 on Raspberry Pi)
            sample_rate: Readings per second (1 Hz typical)
            prefix: Prefix for metric names
            tags: Tags to add to all readings
        """
        super().__init__(sample_rate=sample_rate, prefix=prefix, tags=tags)
        self.address = address
        self.bus_num = bus
        self._bus = None
        self._calib = None

    def setup(self) -> None:
        """Initialize the BME280 and read calibration data."""
        try:
            from smbus2 import SMBus
        except ImportError:
            raise ImportError(
                "smbus2 is required for BME280. Install with: pip install smbus2"
            )

        self._bus = SMBus(self.bus_num)

        # Read calibration data
        self._read_calibration()

        # Configure sensor
        # Humidity oversampling x1
        self._bus.write_byte_data(self.address, BME280_CTRL_HUM, 0x01)
        # Temperature and pressure oversampling x1, normal mode
        self._bus.write_byte_data(self.address, BME280_CTRL_MEAS, 0x27)
        # Standby 1000ms, filter off
        self._bus.write_byte_data(self.address, BME280_CONFIG, 0xA0)

    def cleanup(self) -> None:
        """Close I2C bus."""
        if self._bus:
            self._bus.close()
            self._bus = None

    def _read_calibration(self) -> None:
        """Read factory calibration data."""
        # Read temperature and pressure calibration (26 bytes at 0x88)
        calib1 = self._bus.read_i2c_block_data(self.address, BME280_CALIB_START, 26)

        # Read humidity calibration (7 bytes at 0xE1)
        calib2 = self._bus.read_i2c_block_data(self.address, BME280_CALIB_HUM_START, 7)

        # Parse calibration data
        self._calib = {
            # Temperature
            "T1": calib1[0] | (calib1[1] << 8),
            "T2": self._signed16(calib1[2] | (calib1[3] << 8)),
            "T3": self._signed16(calib1[4] | (calib1[5] << 8)),
            # Pressure
            "P1": calib1[6] | (calib1[7] << 8),
            "P2": self._signed16(calib1[8] | (calib1[9] << 8)),
            "P3": self._signed16(calib1[10] | (calib1[11] << 8)),
            "P4": self._signed16(calib1[12] | (calib1[13] << 8)),
            "P5": self._signed16(calib1[14] | (calib1[15] << 8)),
            "P6": self._signed16(calib1[16] | (calib1[17] << 8)),
            "P7": self._signed16(calib1[18] | (calib1[19] << 8)),
            "P8": self._signed16(calib1[20] | (calib1[21] << 8)),
            "P9": self._signed16(calib1[22] | (calib1[23] << 8)),
            # Humidity
            "H1": calib1[25],
            "H2": self._signed16(calib2[0] | (calib2[1] << 8)),
            "H3": calib2[2],
            "H4": (calib2[3] << 4) | (calib2[4] & 0x0F),
            "H5": (calib2[5] << 4) | ((calib2[4] >> 4) & 0x0F),
            "H6": self._signed8(calib2[6]),
        }

    def _signed16(self, value: int) -> int:
        """Convert unsigned 16-bit to signed."""
        if value > 32767:
            return value - 65536
        return value

    def _signed8(self, value: int) -> int:
        """Convert unsigned 8-bit to signed."""
        if value > 127:
            return value - 256
        return value

    def read(self) -> List[SensorReading]:
        """Read temperature, humidity, and pressure."""
        if self._bus is None:
            self.setup()

        # Read raw data (8 bytes starting at 0xF7)
        data = self._bus.read_i2c_block_data(self.address, BME280_DATA_START, 8)

        # Parse raw values (20-bit for pressure/temp, 16-bit for humidity)
        raw_press = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
        raw_temp = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
        raw_hum = (data[6] << 8) | data[7]

        # Compensate temperature
        temperature, t_fine = self._compensate_temperature(raw_temp)

        # Compensate pressure
        pressure = self._compensate_pressure(raw_press, t_fine)

        # Compensate humidity
        humidity = self._compensate_humidity(raw_hum, t_fine)

        return [
            SensorReading("temperature", round(temperature, 2)),
            SensorReading("humidity", round(humidity, 1)),
            SensorReading("pressure", round(pressure, 2)),
        ]

    def _compensate_temperature(self, raw: int) -> tuple:
        """Compensate raw temperature value. Returns (temp_C, t_fine)."""
        c = self._calib
        var1 = ((raw / 16384.0) - (c["T1"] / 1024.0)) * c["T2"]
        var2 = (((raw / 131072.0) - (c["T1"] / 8192.0)) ** 2) * c["T3"]
        t_fine = var1 + var2
        temperature = t_fine / 5120.0
        return temperature, t_fine

    def _compensate_pressure(self, raw: int, t_fine: float) -> float:
        """Compensate raw pressure value. Returns pressure in hPa."""
        c = self._calib
        var1 = t_fine / 2.0 - 64000.0
        var2 = var1 * var1 * c["P6"] / 32768.0
        var2 = var2 + var1 * c["P5"] * 2.0
        var2 = var2 / 4.0 + c["P4"] * 65536.0
        var1 = (c["P3"] * var1 * var1 / 524288.0 + c["P2"] * var1) / 524288.0
        var1 = (1.0 + var1 / 32768.0) * c["P1"]

        if var1 == 0:
            return 0

        pressure = 1048576.0 - raw
        pressure = ((pressure - var2 / 4096.0) * 6250.0) / var1
        var1 = c["P9"] * pressure * pressure / 2147483648.0
        var2 = pressure * c["P8"] / 32768.0
        pressure = pressure + (var1 + var2 + c["P7"]) / 16.0

        return pressure / 100.0  # Convert Pa to hPa

    def _compensate_humidity(self, raw: int, t_fine: float) -> float:
        """Compensate raw humidity value. Returns relative humidity in %."""
        c = self._calib
        humidity = t_fine - 76800.0
        humidity = (raw - (c["H4"] * 64.0 + c["H5"] / 16384.0 * humidity)) * (
            c["H2"] / 65536.0 * (1.0 + c["H6"] / 67108864.0 * humidity *
            (1.0 + c["H3"] / 67108864.0 * humidity))
        )
        humidity = humidity * (1.0 - c["H1"] * humidity / 524288.0)

        if humidity < 0:
            humidity = 0
        elif humidity > 100:
            humidity = 100

        return humidity

    def is_available(self) -> bool:
        """Check if BME280 is connected."""
        try:
            from smbus2 import SMBus

            bus = SMBus(self.bus_num)
            chip_id = bus.read_byte_data(self.address, BME280_CHIP_ID_REG)
            bus.close()
            return chip_id == 0x60  # BME280 chip ID
        except Exception:
            return False
