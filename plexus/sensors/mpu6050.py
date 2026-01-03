"""
MPU6050 6-axis IMU sensor driver.

The MPU6050 provides 3-axis accelerometer and 3-axis gyroscope data.
Communicates via I2C at address 0x68 (or 0x69 if AD0 pin is high).

Usage:
    from plexus import Plexus
    from plexus.sensors import MPU6050

    px = Plexus()
    imu = MPU6050()

    while True:
        for reading in imu.read():
            px.send(reading.metric, reading.value)
        time.sleep(0.01)  # 100 Hz

Or with SensorHub:
    from plexus.sensors import SensorHub, MPU6050

    hub = SensorHub()
    hub.add(MPU6050(sample_rate=100))
    hub.run(Plexus())
"""

from typing import List, Optional
from .base import BaseSensor, SensorReading

# I2C constants
MPU6050_ADDR = 0x68
MPU6050_ADDR_ALT = 0x69

# Register addresses
PWR_MGMT_1 = 0x6B
ACCEL_XOUT_H = 0x3B
GYRO_XOUT_H = 0x43
WHO_AM_I = 0x75

# Scale factors for default ranges
ACCEL_SCALE_2G = 16384.0  # LSB/g for ±2g
GYRO_SCALE_250 = 131.0    # LSB/(°/s) for ±250°/s


class MPU6050(BaseSensor):
    """
    MPU6050 6-axis IMU sensor driver.

    Provides:
    - accel_x, accel_y, accel_z: Acceleration in g (±2g range)
    - gyro_x, gyro_y, gyro_z: Angular velocity in °/s (±250°/s range)
    """

    name = "MPU6050"
    description = "6-axis IMU (accelerometer + gyroscope)"
    metrics = ["accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z"]
    i2c_addresses = [MPU6050_ADDR, MPU6050_ADDR_ALT]

    def __init__(
        self,
        address: int = MPU6050_ADDR,
        bus: int = 1,
        sample_rate: float = 100.0,
        prefix: str = "",
        tags: Optional[dict] = None,
    ):
        """
        Initialize MPU6050 driver.

        Args:
            address: I2C address (0x68 or 0x69)
            bus: I2C bus number (usually 1 on Raspberry Pi)
            sample_rate: Readings per second
            prefix: Prefix for metric names
            tags: Tags to add to all readings
        """
        super().__init__(sample_rate=sample_rate, prefix=prefix, tags=tags)
        self.address = address
        self.bus_num = bus
        self._bus = None

    def setup(self) -> None:
        """Initialize the MPU6050."""
        try:
            from smbus2 import SMBus
        except ImportError:
            raise ImportError(
                "smbus2 is required for MPU6050. Install with: pip install smbus2"
            )

        self._bus = SMBus(self.bus_num)

        # Wake up the sensor (clear sleep bit)
        self._bus.write_byte_data(self.address, PWR_MGMT_1, 0x00)

    def cleanup(self) -> None:
        """Close I2C bus."""
        if self._bus:
            self._bus.close()
            self._bus = None

    def _read_raw(self, register: int) -> int:
        """Read 16-bit signed value from register pair."""
        high = self._bus.read_byte_data(self.address, register)
        low = self._bus.read_byte_data(self.address, register + 1)
        value = (high << 8) | low
        if value > 32767:
            value -= 65536
        return value

    def read(self) -> List[SensorReading]:
        """Read accelerometer and gyroscope data."""
        if self._bus is None:
            self.setup()

        # Read accelerometer (g)
        accel_x = self._read_raw(ACCEL_XOUT_H) / ACCEL_SCALE_2G
        accel_y = self._read_raw(ACCEL_XOUT_H + 2) / ACCEL_SCALE_2G
        accel_z = self._read_raw(ACCEL_XOUT_H + 4) / ACCEL_SCALE_2G

        # Read gyroscope (°/s)
        gyro_x = self._read_raw(GYRO_XOUT_H) / GYRO_SCALE_250
        gyro_y = self._read_raw(GYRO_XOUT_H + 2) / GYRO_SCALE_250
        gyro_z = self._read_raw(GYRO_XOUT_H + 4) / GYRO_SCALE_250

        return [
            SensorReading("accel_x", round(accel_x, 4)),
            SensorReading("accel_y", round(accel_y, 4)),
            SensorReading("accel_z", round(accel_z, 4)),
            SensorReading("gyro_x", round(gyro_x, 2)),
            SensorReading("gyro_y", round(gyro_y, 2)),
            SensorReading("gyro_z", round(gyro_z, 2)),
        ]

    def is_available(self) -> bool:
        """Check if MPU6050 is connected."""
        try:
            from smbus2 import SMBus

            bus = SMBus(self.bus_num)
            who_am_i = bus.read_byte_data(self.address, WHO_AM_I)
            bus.close()
            # Accept various MPU variants:
            # 0x68 = MPU6050, 0x70 = MPU6500, 0x71 = MPU9250, 0x73 = MPU9255
            return who_am_i in (0x68, 0x70, 0x71, 0x73)
        except Exception:
            return False


class MPU9250(MPU6050):
    """
    MPU9250 9-axis IMU sensor driver.

    Same as MPU6050 but includes magnetometer.
    Magnetometer requires additional setup (AK8963 on auxiliary I2C).

    For now, this provides the same 6-axis data as MPU6050.
    Magnetometer support can be added later.
    """

    name = "MPU9250"
    description = "9-axis IMU (accelerometer + gyroscope + magnetometer)"
    metrics = ["accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z"]
    # TODO: Add mag_x, mag_y, mag_z when magnetometer support is implemented
