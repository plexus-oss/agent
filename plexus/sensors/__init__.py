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
"""

from .base import BaseSensor, SensorReading, SensorHub
from .mpu6050 import MPU6050, MPU9250
from .bme280 import BME280
from .auto import scan_sensors, auto_sensors, scan_i2c, DetectedSensor, get_sensor_info

__all__ = [
    # Base classes
    "BaseSensor",
    "SensorReading",
    "SensorHub",
    # IMU sensors
    "MPU6050",
    "MPU9250",
    # Environmental sensors
    "BME280",
    # Auto-detection
    "scan_sensors",
    "auto_sensors",
    "scan_i2c",
    "DetectedSensor",
    "get_sensor_info",
]
