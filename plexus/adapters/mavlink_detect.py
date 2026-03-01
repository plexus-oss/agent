"""
MAVLink auto-detection.

Scans for MAVLink-capable connections: UDP ports, serial flight controllers,
and TCP endpoints.

Usage:
    from plexus.adapters.mavlink_detect import scan_mavlink

    connections = scan_mavlink()
    for conn in connections:
        print(f"{conn.connection_string} ({conn.transport})")
"""

import glob
import logging
import os
import socket
from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)

# Common MAVLink UDP ports
MAVLINK_UDP_PORTS = [14550, 14551]

# Common MAVLink TCP ports (SITL, companion computers)
MAVLINK_TCP_PORTS = [5760, 5762]

# Known flight controller USB vendor IDs
FC_USB_VIDS = {
    "2dae": "Holybro (Pixhawk)",
    "1209": "ArduPilot / Generic FC",
    "26ac": "3DR (Pixhawk 1)",
    "0483": "STMicro (many FCs)",
    "1fc9": "NXP (FMUK66)",
    "3162": "CubePilot",
}


@dataclass
class DetectedMAVLink:
    """Information about a detected MAVLink connection."""
    connection_string: str
    transport: str       # "udp", "tcp", "serial"
    description: str
    is_available: bool   # True if the connection was verified


def _scan_udp() -> List[DetectedMAVLink]:
    """Scan for MAVLink UDP listeners on standard ports."""
    detected = []

    for port in MAVLINK_UDP_PORTS:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.1)
            sock.bind(("0.0.0.0", port))
            # Port is free — no one is sending here yet, but it's available
            detected.append(DetectedMAVLink(
                connection_string=f"udpin:0.0.0.0:{port}",
                transport="udp",
                description=f"UDP port {port} (available to listen)",
                is_available=True,
            ))
        except OSError:
            # Port in use — something may already be sending MAVLink here
            detected.append(DetectedMAVLink(
                connection_string=f"udpin:0.0.0.0:{port}",
                transport="udp",
                description=f"UDP port {port} (in use — possible MAVLink source)",
                is_available=True,
            ))
        finally:
            if sock:
                sock.close()

    return detected


def _scan_tcp() -> List[DetectedMAVLink]:
    """Scan for MAVLink TCP endpoints on localhost."""
    detected = []

    for port in MAVLINK_TCP_PORTS:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            result = sock.connect_ex(("127.0.0.1", port))
            if result == 0:
                detected.append(DetectedMAVLink(
                    connection_string=f"tcp:127.0.0.1:{port}",
                    transport="tcp",
                    description=f"TCP port {port} (SITL or companion)",
                    is_available=True,
                ))
        except (OSError, socket.error):
            pass
        finally:
            if sock:
                sock.close()

    return detected


def _scan_serial() -> List[DetectedMAVLink]:
    """Scan serial ports for known flight controller USB VIDs."""
    detected = []

    # Check /sys/bus/usb for known FC vendor IDs (Linux)
    usb_path = "/sys/bus/usb/devices"
    fc_serial_paths = set()

    if os.path.isdir(usb_path):
        try:
            for entry in os.listdir(usb_path):
                vid_path = os.path.join(usb_path, entry, "idVendor")
                try:
                    with open(vid_path, "r") as f:
                        vid = f.read().strip().lower()
                except (OSError, IOError):
                    continue

                if vid in FC_USB_VIDS:
                    # Look for tty sub-device
                    dev_dir = os.path.join(usb_path, entry)
                    for tty in glob.glob(os.path.join(dev_dir, "**", "tty", "tty*"), recursive=True):
                        tty_name = os.path.basename(tty)
                        dev_path = f"/dev/{tty_name}"
                        if os.path.exists(dev_path):
                            fc_serial_paths.add((dev_path, FC_USB_VIDS[vid]))
        except OSError:
            pass

    for dev_path, fc_name in sorted(fc_serial_paths):
        detected.append(DetectedMAVLink(
            connection_string=dev_path,
            transport="serial",
            description=f"Flight controller: {fc_name}",
            is_available=True,
        ))

    # Fallback: check common serial paths for FC-like devices
    if not detected:
        fc_patterns = ["/dev/ttyACM*", "/dev/ttyUSB*"]
        for pattern in fc_patterns:
            for device_path in sorted(glob.glob(pattern)):
                detected.append(DetectedMAVLink(
                    connection_string=device_path,
                    transport="serial",
                    description="Serial port (possible flight controller)",
                    is_available=False,  # Not verified
                ))

    return detected


def scan_mavlink() -> List[DetectedMAVLink]:
    """
    Scan for MAVLink connections on the system.

    Checks:
    1. UDP ports 14550, 14551
    2. TCP ports 5760, 5762 on localhost
    3. Serial ports with known flight controller USB VIDs

    Returns:
        List of detected MAVLink connections
    """
    detected = []

    # UDP ports
    try:
        detected.extend(_scan_udp())
    except Exception as e:
        logger.debug(f"Error scanning MAVLink UDP ports: {e}")

    # TCP ports
    try:
        detected.extend(_scan_tcp())
    except Exception as e:
        logger.debug(f"Error scanning MAVLink TCP ports: {e}")

    # Serial ports
    try:
        detected.extend(_scan_serial())
    except Exception as e:
        logger.debug(f"Error scanning MAVLink serial ports: {e}")

    return detected
