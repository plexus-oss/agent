"""
Plexus Agent - Send sensor data to Plexus in one line of code.

Basic Usage:
    from plexus import Plexus

    px = Plexus()
    px.send("temperature", 72.5)

With Sensors (pip install plexus-agent[sensors]):
    from plexus import Plexus
    from plexus.sensors import SensorHub, MPU6050, BME280

    hub = SensorHub()
    hub.add(MPU6050(sample_rate=100))
    hub.add(BME280(sample_rate=1))
    hub.run(Plexus())

Auto-Detection:
    from plexus import Plexus
    from plexus.sensors import auto_sensors

    hub = auto_sensors()  # Finds all connected sensors
    hub.run(Plexus())
"""

from plexus.client import Plexus
from plexus.config import load_config, save_config

__version__ = "0.3.7"
__all__ = ["Plexus", "load_config", "save_config"]
