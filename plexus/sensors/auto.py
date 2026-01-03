"""
Auto-detection of connected sensors.

Scans I2C buses to find known sensors and automatically creates driver instances.

Usage:
    from plexus.sensors import scan_sensors, auto_sensors

    # Scan and list available sensors
    sensors = scan_sensors()
    for s in sensors:
        print(f"{s.name} at 0x{s.address:02X}")

    # Auto-create sensor instances
    hub = auto_sensors()
    hub.run(Plexus())
"""

from typing import List, Dict, Type, Optional, Tuple
from dataclasses import dataclass

from .base import BaseSensor, SensorHub


@dataclass
class DetectedSensor:
    """Information about a detected sensor."""
    name: str
    address: int
    bus: int
    driver: Type[BaseSensor]
    description: str


# Registry of known sensors and their I2C addresses
KNOWN_SENSORS: List[Tuple[Type[BaseSensor], int, str]] = []


def register_sensor(driver: Type[BaseSensor], address: int, chip_id_check=None):
    """Register a sensor for auto-detection."""
    KNOWN_SENSORS.append((driver, address, chip_id_check))


def _init_known_sensors():
    """Initialize the registry with known sensors."""
    global KNOWN_SENSORS

    if KNOWN_SENSORS:
        return  # Already initialized

    # Import drivers
    from .mpu6050 import MPU6050, MPU9250
    from .bme280 import BME280

    # Register known sensors: (driver_class, i2c_address, chip_id_check)
    KNOWN_SENSORS = [
        # IMU sensors
        (MPU6050, 0x68, None),
        (MPU6050, 0x69, None),
        (MPU9250, 0x68, None),  # Same address as MPU6050
        # Environmental sensors
        (BME280, 0x76, None),
        (BME280, 0x77, None),
    ]


def scan_i2c(bus: int = 1) -> List[int]:
    """
    Scan I2C bus for connected devices.

    Args:
        bus: I2C bus number (usually 1 on Raspberry Pi)

    Returns:
        List of detected I2C addresses
    """
    try:
        from smbus2 import SMBus
    except ImportError:
        raise ImportError(
            "smbus2 is required for I2C scanning. Install with: pip install smbus2"
        )

    addresses = []
    i2c = SMBus(bus)

    for addr in range(0x03, 0x78):  # Valid I2C address range
        try:
            i2c.read_byte(addr)
            addresses.append(addr)
        except OSError:
            pass  # No device at this address

    i2c.close()
    return addresses


def scan_sensors(bus: int = 1) -> List[DetectedSensor]:
    """
    Scan for known sensors on the I2C bus.

    Args:
        bus: I2C bus number

    Returns:
        List of detected sensors with their drivers
    """
    _init_known_sensors()

    # First, scan for all I2C devices
    try:
        addresses = scan_i2c(bus)
    except ImportError:
        return []
    except Exception:
        return []

    detected = []

    for address in addresses:
        # Try to match against known sensors
        for driver, known_addr, _ in KNOWN_SENSORS:
            if address == known_addr:
                # Check if this sensor is already detected (avoid duplicates)
                already_found = any(
                    d.address == address and d.driver == driver
                    for d in detected
                )
                if not already_found:
                    # Try to verify the sensor
                    try:
                        sensor = driver(address=address, bus=bus)
                        if sensor.is_available():
                            detected.append(DetectedSensor(
                                name=driver.name,
                                address=address,
                                bus=bus,
                                driver=driver,
                                description=driver.description,
                            ))
                            break  # Don't try other drivers for same address
                    except Exception:
                        pass

    return detected


def auto_sensors(
    bus: int = 1,
    sample_rate: Optional[float] = None,
    prefix: str = "",
) -> SensorHub:
    """
    Auto-detect sensors and create a SensorHub.

    Args:
        bus: I2C bus number
        sample_rate: Override sample rate for all sensors (None = use defaults)
        prefix: Prefix for all metric names

    Returns:
        SensorHub with all detected sensors added
    """
    hub = SensorHub()

    detected = scan_sensors(bus)

    for info in detected:
        kwargs = {"address": info.address, "bus": info.bus}

        if sample_rate is not None:
            kwargs["sample_rate"] = sample_rate

        if prefix:
            kwargs["prefix"] = prefix

        sensor = info.driver(**kwargs)
        hub.add(sensor)

    return hub


def get_sensor_info() -> Dict[str, dict]:
    """
    Get information about all supported sensors.

    Returns:
        Dict mapping sensor names to their info
    """
    _init_known_sensors()

    info = {}
    seen_drivers = set()

    for driver, addr, _ in KNOWN_SENSORS:
        if driver not in seen_drivers:
            info[driver.name] = {
                "name": driver.name,
                "description": driver.description,
                "metrics": driver.metrics,
                "i2c_addresses": [
                    f"0x{a:02X}" for d, a, _ in KNOWN_SENSORS if d == driver
                ],
            }
            seen_drivers.add(driver)

    return info
