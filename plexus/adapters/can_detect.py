"""
CAN bus interface auto-detection.

Scans for SocketCAN interfaces on Linux and common USB-serial CAN adapters.

Usage:
    from plexus.adapters.can_detect import scan_can

    interfaces = scan_can()
    for iface in interfaces:
        print(f"{iface.interface} ({'up' if iface.is_up else 'down'})")
"""

import glob
import logging
import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

# ARPHRD_CAN - Linux ARP hardware type for CAN interfaces
ARPHRD_CAN = 280

# Default bitrate suggestion when interface is down
DEFAULT_BITRATE = 500000


@dataclass
class DetectedCAN:
    """Information about a detected CAN interface."""
    interface: str
    channel: str
    is_up: bool
    bitrate: Optional[int]


def _read_sysfs(path: str) -> Optional[str]:
    """Read a sysfs file, returning None on failure."""
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except (OSError, IOError):
        return None


def _scan_socketcan() -> List[DetectedCAN]:
    """Scan for SocketCAN interfaces via /sys/class/net/."""
    detected = []
    net_dir = "/sys/class/net"

    if not os.path.isdir(net_dir):
        return detected

    try:
        interfaces = os.listdir(net_dir)
    except OSError:
        return detected

    for iface in sorted(interfaces):
        # Check if this is a CAN interface (type == 280)
        iface_type = _read_sysfs(os.path.join(net_dir, iface, "type"))
        if iface_type != str(ARPHRD_CAN):
            continue

        # Check operational state
        operstate = _read_sysfs(os.path.join(net_dir, iface, "operstate"))
        is_up = operstate in ("up", "unknown")

        # Try to read bitrate from /sys/class/net/{iface}/can_bittiming/bitrate
        bitrate = None
        if is_up:
            bitrate_str = _read_sysfs(
                os.path.join(net_dir, iface, "can_bittiming", "bitrate")
            )
            if bitrate_str and bitrate_str.isdigit():
                bitrate = int(bitrate_str)

        detected.append(DetectedCAN(
            interface="socketcan",
            channel=iface,
            is_up=is_up,
            bitrate=bitrate,
        ))

    return detected


def _scan_usb_serial() -> List[DetectedCAN]:
    """Scan for USB-serial CAN adapters (slcan devices)."""
    detected = []

    # Common paths for USB-serial devices that may be slcan adapters
    patterns = ["/dev/ttyUSB*", "/dev/ttyACM*"]

    for pattern in patterns:
        for device_path in sorted(glob.glob(pattern)):
            detected.append(DetectedCAN(
                interface="slcan",
                channel=device_path,
                is_up=False,  # slcan needs manual setup
                bitrate=None,
            ))

    return detected


def setup_can(iface: DetectedCAN, bitrate: int = DEFAULT_BITRATE) -> bool:
    """
    Bring up a SocketCAN interface.

    Runs: sudo ip link set {channel} up type can bitrate {bitrate}

    Args:
        iface: Detected CAN interface to configure
        bitrate: CAN bitrate in bps (default: 500000)

    Returns:
        True if the interface was brought up successfully
    """
    if iface.interface != "socketcan":
        logger.warning(f"Auto-setup not supported for {iface.interface} interfaces")
        return False

    try:
        result = subprocess.run(
            ["sudo", "ip", "link", "set", iface.channel, "up",
             "type", "can", "bitrate", str(bitrate)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            logger.info(f"Brought up {iface.channel} at {bitrate} bps")
            return True
        else:
            logger.error(f"Failed to bring up {iface.channel}: {result.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"Timed out configuring {iface.channel}")
        return False
    except Exception as e:
        logger.error(f"Error configuring {iface.channel}: {e}")
        return False


def scan_can() -> List[DetectedCAN]:
    """
    Scan for CAN interfaces on the system.

    Checks:
    1. SocketCAN interfaces via /sys/class/net/ (e.g. can0, vcan0)
    2. USB-serial devices that may be slcan adapters (e.g. /dev/ttyUSB0)

    Returns:
        List of detected CAN interfaces
    """
    detected = []

    # Primary: SocketCAN interfaces
    try:
        detected.extend(_scan_socketcan())
    except Exception as e:
        logger.debug(f"Error scanning SocketCAN interfaces: {e}")

    # Secondary: USB-serial CAN adapters (only if no socketcan found)
    if not detected:
        try:
            detected.extend(_scan_usb_serial())
        except Exception as e:
            logger.debug(f"Error scanning USB-serial CAN adapters: {e}")

    return detected
