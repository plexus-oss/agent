"""
Plexus Sensor Drivers

Pre-built drivers for common sensors that stream data to Plexus.

Quick Start:
    from plexus import Plexus
    from plexus.sensors import MPU6050

    px = Plexus()
    imu = MPU6050()

    while True:
        for reading in imu.read():
            px.send(reading.metric, reading.value)

With SensorHub (recommended):
    from plexus import Plexus
    from plexus.sensors import SensorHub, MPU6050, BME280

    hub = SensorHub()
    hub.add(MPU6050(sample_rate=100))
    hub.add(BME280(sample_rate=1))
    hub.run(Plexus())

Auto-detection:
    from plexus import Plexus
    from plexus.sensors import auto_sensors

    hub = auto_sensors()  # Finds all connected sensors
    hub.run(Plexus())

Supported Sensors:
    - MPU6050: 6-axis IMU (accelerometer + gyroscope)
    - MPU9250: 9-axis IMU (accelerometer + gyroscope + magnetometer)
    - BME280: Environmental (temperature, humidity, pressure)
    - INA219: Current/power monitor (voltage, current, power)
    - SHT3x: Precision temperature + humidity (with CRC verification)
    - BH1750: Ambient light sensor (1-65535 lux)
    - VL53L0X: Time-of-flight distance sensor (30-2000mm)
    - ADS1115: 16-bit ADC (4 channels, programmable gain)
    - QMC5883L: 3-axis magnetometer / compass
    - HMC5883L: 3-axis magnetometer / compass
    - GPSSensor: GPS receiver (lat, lon, altitude, speed via NMEA serial)
    - SystemSensor: System health (CPU temp, memory, disk, load)
"""

from .base import BaseSensor, SensorReading, SensorHub
from .mpu6050 import MPU6050, MPU9250
from .bme280 import BME280
from .ina219 import INA219
from .sht3x import SHT3x
from .bh1750 import BH1750
from .vl53l0x import VL53L0X
from .ads1115 import ADS1115
from .magnetometer import QMC5883L, HMC5883L
from .gps import GPSSensor
from .auto import scan_sensors, auto_sensors, scan_i2c, DetectedSensor
from .system import SystemSensor

__all__ = [
    # Base classes
    "BaseSensor",
    "SensorReading",
    "SensorHub",
    # I2C sensors
    "MPU6050",
    "MPU9250",
    "BME280",
    "INA219",
    "SHT3x",
    "BH1750",
    "VL53L0X",
    "ADS1115",
    "QMC5883L",
    "HMC5883L",
    # Serial sensors
    "GPSSensor",
    # System
    "SystemSensor",
    # Auto-detection
    "scan_sensors",
    "auto_sensors",
    "scan_i2c",
    "DetectedSensor",
    # Registry
    "SENSOR_REGISTRY",
]

# Maps CLI --sensor names to driver classes
SENSOR_REGISTRY = {
    "system": SystemSensor,
    "gps": GPSSensor,
}
