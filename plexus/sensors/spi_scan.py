"""
SPI bus scanning for sensor auto-detection.

Scans available SPI buses for known sensors by reading WHO_AM_I registers.

Usage:
    from plexus.sensors.spi_scan import scan_spi, scan_spi_buses

    # List available SPI buses
    buses = scan_spi_buses()

    # Scan for known sensors
    sensors = scan_spi()
    for s in sensors:
        print(f"{s.name} on SPI bus {s.bus} CS {s.cs}")
"""

import glob
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple, Type

from .base import BaseSensor

logger = logging.getLogger(__name__)


@dataclass
class SPISensorMatch:
    """A sensor detected on an SPI bus."""
    name: str
    bus: int
    cs: int
    driver: Type[BaseSensor]
    description: str


@dataclass
class SPISensorInfo:
    """Registration info for an SPI-detectable sensor."""
    driver: Type[BaseSensor]
    who_am_i_reg: int
    expected_id: int
    spi_mode: int
    spi_speed: int
    description: str


# Registry of known SPI sensors
KNOWN_SPI_SENSORS: List[SPISensorInfo] = []


def register_spi_sensor(info: SPISensorInfo):
    """Register a sensor for SPI auto-detection."""
    KNOWN_SPI_SENSORS.append(info)


def _init_known_spi_sensors():
    """Initialize the SPI sensor registry."""
    if KNOWN_SPI_SENSORS:
        return

    try:
        from .adxl345 import ADXL345
        register_spi_sensor(SPISensorInfo(
            driver=ADXL345,
            who_am_i_reg=0x00,
            expected_id=0xE5,
            spi_mode=3,
            spi_speed=1000000,
            description="3-axis accelerometer",
        ))
    except ImportError:
        pass


def scan_spi_buses() -> List[Tuple[int, int]]:
    """
    Enumerate available SPI bus/CS combinations.

    Scans /dev/spidev* for available SPI devices.

    Returns:
        List of (bus, cs) tuples
    """
    devices = sorted(glob.glob("/dev/spidev*"))
    buses = []
    for dev in devices:
        match = re.search(r"spidev(\d+)\.(\d+)", dev)
        if match:
            bus = int(match.group(1))
            cs = int(match.group(2))
            buses.append((bus, cs))
    return buses


def _spi_read_register(bus: int, cs: int, reg: int, mode: int, speed: int) -> Optional[int]:
    """Read a single register over SPI. Returns None on failure."""
    try:
        import spidev
    except ImportError:
        raise ImportError(
            "spidev is required for SPI scanning. Install with: pip install spidev"
        )

    try:
        spi = spidev.SpiDev()
        spi.open(bus, cs)
        spi.mode = mode
        spi.max_speed_hz = speed
        # SPI read: set bit 7 of register address
        resp = spi.xfer2([reg | 0x80, 0x00])
        spi.close()
        return resp[1]
    except PermissionError:
        logger.warning(
            "Permission denied opening SPI bus %d CS %d. "
            "Try: sudo usermod -aG spi $USER && reboot",
            bus, cs,
        )
        return None
    except Exception as e:
        logger.debug("SPI read failed on bus %d cs %d reg 0x%02X: %s", bus, cs, reg, e)
        return None


def scan_spi(buses: Optional[List[Tuple[int, int]]] = None) -> List[SPISensorMatch]:
    """
    Scan SPI buses for known sensors.

    Args:
        buses: List of (bus, cs) tuples to scan. None = auto-detect available buses.

    Returns:
        List of detected sensors
    """
    _init_known_spi_sensors()

    if buses is None:
        buses = scan_spi_buses()

    if not buses:
        return []

    detected = []

    for bus, cs in buses:
        for info in KNOWN_SPI_SENSORS:
            value = _spi_read_register(bus, cs, info.who_am_i_reg, info.spi_mode, info.spi_speed)
            if value == info.expected_id:
                detected.append(SPISensorMatch(
                    name=info.driver.name,
                    bus=bus,
                    cs=cs,
                    driver=info.driver,
                    description=info.description,
                ))
                logger.info(
                    "Found %s on SPI bus %d CS %d", info.driver.name, bus, cs
                )
                break  # One sensor per CS line

    return detected
