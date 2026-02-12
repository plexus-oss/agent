"""
Hardware detection for Plexus devices.

Detects sensors, cameras, and CAN interfaces. Used by the CLI
for both `plexus run` and `plexus scan`.
"""

import logging
from typing import Optional, TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from plexus.sensors.base import SensorHub
    from plexus.cameras.base import CameraHub
    from plexus.adapters.can_detect import DetectedCAN


def detect_sensors(bus: int = 1) -> tuple[Optional["SensorHub"], list]:
    """Detect I2C sensors and create a SensorHub.

    Returns:
        (sensor_hub or None, list of detected sensor info objects)
    """
    try:
        from plexus.sensors import scan_sensors, auto_sensors
        sensors = scan_sensors(bus)
        if sensors:
            hub = auto_sensors(bus=bus)
            return hub, sensors
        return None, []
    except ImportError:
        return None, []
    except Exception as e:
        logger.debug(f"Sensor detection failed: {e}")
        return None, []


def detect_cameras() -> tuple[Optional["CameraHub"], list]:
    """Detect connected cameras and create a CameraHub.

    Returns:
        (camera_hub or None, list of detected camera info objects)
    """
    try:
        from plexus.cameras import scan_cameras, auto_cameras
        cameras = scan_cameras()
        if cameras:
            hub = auto_cameras()
            return hub, cameras
        return None, []
    except ImportError:
        return None, []
    except Exception as e:
        logger.debug(f"Camera detection failed: {e}")
        return None, []


def detect_can() -> tuple[Optional[list["DetectedCAN"]], list["DetectedCAN"], list["DetectedCAN"]]:
    """Detect CAN interfaces.

    Returns:
        (up_adapters or None, up_list, down_list)
        up_adapters is None if no active interfaces found.
    """
    try:
        from plexus.adapters.can_detect import scan_can
        detected = scan_can()
        up = [c for c in detected if c.is_up]
        down = [c for c in detected if not c.is_up]
        return (up if up else None), up, down
    except Exception as e:
        logger.debug(f"CAN detection failed: {e}")
        return None, [], []
