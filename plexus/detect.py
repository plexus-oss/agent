"""
Hardware detection for Plexus devices.

Detects sensors, cameras, serial ports, USB devices, GPIO, Bluetooth,
network interfaces, and system info. Used by the CLI for both
`plexus run` and `plexus scan`.
"""

import glob
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple, Dict, Any, TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from plexus.sensors.base import SensorHub
    from plexus.cameras.base import CameraHub
    from plexus.adapters.can_detect import DetectedCAN


# ─────────────────────────────────────────────────────────────────────────────
# Data Classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SensorInfo:
    """Lightweight info object for display (matches DetectedSensor pattern)."""
    name: str
    description: str


@dataclass
class SerialDevice:
    """A detected serial port."""
    port: str
    description: str = ""
    hwid: str = ""
    manufacturer: str = ""


@dataclass
class USBDevice:
    """A detected USB device."""
    name: str
    vendor_id: str = ""
    product_id: str = ""
    manufacturer: str = ""
    serial: str = ""


@dataclass
class GPIOInfo:
    """GPIO availability information."""
    chip: str
    num_lines: int = 0
    used_lines: int = 0


@dataclass
class BluetoothDevice:
    """A detected Bluetooth/BLE device."""
    name: str
    address: str
    rssi: Optional[int] = None
    type: str = "classic"  # "classic", "le", or "dual"


@dataclass
class NetworkInterface:
    """A detected network interface."""
    name: str
    type: str = "unknown"  # "ethernet", "wifi", "can", "loopback", "other"
    ip: str = ""
    mac: str = ""
    state: str = "unknown"  # "up", "down", "unknown"
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SystemInfo:
    """Comprehensive system information."""
    hostname: str = ""
    platform: str = ""
    arch: str = ""
    cpu: str = ""
    cpu_cores: int = 0
    ram_mb: int = 0
    disk_gb: float = 0.0
    os_version: str = ""
    python_version: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Existing Detection Functions
# ─────────────────────────────────────────────────────────────────────────────

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


def detect_named_sensors(
    sensor_types: List[str],
) -> Tuple[Optional["SensorHub"], List[SensorInfo]]:
    """Create a SensorHub from explicit --sensor CLI arguments.

    Args:
        sensor_types: List of sensor type names (e.g. ["system"])

    Returns:
        (sensor_hub or None, list of SensorInfo for display)
    """
    from plexus.sensors import SENSOR_REGISTRY, SensorHub

    hub = SensorHub()
    info_list = []

    for sensor_type in sensor_types:
        sensor_type = sensor_type.lower()
        if sensor_type not in SENSOR_REGISTRY:
            valid = ", ".join(sorted(SENSOR_REGISTRY.keys()))
            raise ValueError(f"Unknown sensor type '{sensor_type}'. Valid types: {valid}")

        driver_class = SENSOR_REGISTRY[sensor_type]
        sensor = driver_class()
        hub.add(sensor)
        info_list.append(SensorInfo(name=sensor.name, description=sensor.description))

    if not info_list:
        return None, []

    return hub, info_list


# ─────────────────────────────────────────────────────────────────────────────
# Serial Port Detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_serial() -> List[SerialDevice]:
    """Detect serial ports: USB-UART, /dev/ttyUSB*, /dev/ttyACM*, etc.

    Tries pyserial first for rich metadata, falls back to glob on /dev/tty*.

    Returns:
        List of detected serial devices. Empty list on failure.
    """
    # Method 1: pyserial (best — gives description, hwid, manufacturer)
    try:
        import serial.tools.list_ports
        ports = serial.tools.list_ports.comports()
        devices = []
        for p in ports:
            # Skip purely virtual/internal ports with no real hardware
            if p.hwid == "n/a" and not p.description:
                continue
            devices.append(SerialDevice(
                port=p.device,
                description=p.description or "",
                hwid=p.hwid or "",
                manufacturer=p.manufacturer or "",
            ))
        if devices:
            logger.debug(f"Serial: pyserial found {len(devices)} port(s)")
            return devices
        # pyserial found nothing — still try glob fallback
    except ImportError:
        logger.debug("Serial: pyserial not available, falling back to glob")
    except Exception as e:
        logger.debug(f"Serial: pyserial failed: {e}")

    # Method 2: Glob for common serial device paths
    devices = []
    patterns = [
        "/dev/ttyUSB*",
        "/dev/ttyACM*",
        "/dev/tty.usbserial*",
        "/dev/tty.usbmodem*",
        "/dev/tty.SLAB*",          # CP210x on macOS
        "/dev/tty.wchusbserial*",  # CH340 on macOS
    ]
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            devices.append(SerialDevice(
                port=path,
                description=_serial_description_from_path(path),
            ))

    if devices:
        logger.debug(f"Serial: glob found {len(devices)} port(s)")
    return devices


def _serial_description_from_path(path: str) -> str:
    """Infer a human-readable description from a serial device path."""
    name = os.path.basename(path)
    if "ttyUSB" in name:
        return "USB-Serial adapter"
    if "ttyACM" in name:
        return "USB CDC device"
    if "usbserial" in name:
        return "USB-Serial adapter"
    if "usbmodem" in name:
        return "USB modem"
    if "SLAB" in name:
        return "CP210x USB-UART"
    if "wchusbserial" in name:
        return "CH340 USB-UART"
    return "Serial port"


# ─────────────────────────────────────────────────────────────────────────────
# USB Device Detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_usb() -> List[USBDevice]:
    """Detect USB devices connected to the system.

    On Linux, reads /sys/bus/usb/devices. On macOS, uses system_profiler.

    Returns:
        List of detected USB devices. Empty list on failure.
    """
    system = platform.system()

    if system == "Linux":
        return _detect_usb_linux()
    elif system == "Darwin":
        return _detect_usb_macos()
    else:
        logger.debug(f"USB: unsupported platform {system}")
        return []


def _detect_usb_linux() -> List[USBDevice]:
    """Detect USB devices on Linux via /sys/bus/usb/devices."""
    devices = []
    usb_path = "/sys/bus/usb/devices"

    if not os.path.isdir(usb_path):
        logger.debug("USB: /sys/bus/usb/devices not found")
        return devices

    try:
        for entry in sorted(os.listdir(usb_path)):
            dev_dir = os.path.join(usb_path, entry)
            product_file = os.path.join(dev_dir, "product")
            if not os.path.isfile(product_file):
                continue

            name = _read_sysfs(product_file) or "Unknown"
            vendor_id = _read_sysfs(os.path.join(dev_dir, "idVendor")) or ""
            product_id = _read_sysfs(os.path.join(dev_dir, "idProduct")) or ""
            manufacturer = _read_sysfs(os.path.join(dev_dir, "manufacturer")) or ""
            serial = _read_sysfs(os.path.join(dev_dir, "serial")) or ""

            devices.append(USBDevice(
                name=name,
                vendor_id=vendor_id,
                product_id=product_id,
                manufacturer=manufacturer,
                serial=serial,
            ))
    except OSError as e:
        logger.debug(f"USB: error reading sysfs: {e}")

    logger.debug(f"USB: Linux sysfs found {len(devices)} device(s)")
    return devices


def _detect_usb_macos() -> List[USBDevice]:
    """Detect USB devices on macOS via system_profiler."""
    devices = []
    try:
        result = subprocess.run(
            ["system_profiler", "SPUSBDataType", "-detailLevel", "mini"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            logger.debug(f"USB: system_profiler failed: {result.stderr}")
            return devices

        current_name = ""
        current_vendor_id = ""
        current_product_id = ""
        current_manufacturer = ""
        current_serial = ""

        for line in result.stdout.splitlines():
            stripped = line.strip()

            # Device name lines end with ':' and are indented
            if stripped.endswith(":") and not stripped.startswith("USB") and line.startswith("    "):
                # Save previous device if any
                if current_name:
                    devices.append(USBDevice(
                        name=current_name,
                        vendor_id=current_vendor_id,
                        product_id=current_product_id,
                        manufacturer=current_manufacturer,
                        serial=current_serial,
                    ))
                current_name = stripped.rstrip(":")
                current_vendor_id = ""
                current_product_id = ""
                current_manufacturer = ""
                current_serial = ""
            elif "Vendor ID:" in stripped:
                current_vendor_id = stripped.split(":", 1)[1].strip()
            elif "Product ID:" in stripped:
                current_product_id = stripped.split(":", 1)[1].strip()
            elif "Manufacturer:" in stripped:
                current_manufacturer = stripped.split(":", 1)[1].strip()
            elif "Serial Number:" in stripped:
                current_serial = stripped.split(":", 1)[1].strip()

        # Save last device
        if current_name:
            devices.append(USBDevice(
                name=current_name,
                vendor_id=current_vendor_id,
                product_id=current_product_id,
                manufacturer=current_manufacturer,
                serial=current_serial,
            ))

    except FileNotFoundError:
        logger.debug("USB: system_profiler not found")
    except subprocess.TimeoutExpired:
        logger.debug("USB: system_profiler timed out")
    except Exception as e:
        logger.debug(f"USB: system_profiler error: {e}")

    logger.debug(f"USB: macOS found {len(devices)} device(s)")
    return devices


def _read_sysfs(path: str) -> Optional[str]:
    """Read a sysfs file, returning None on failure."""
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except (OSError, IOError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# GPIO Detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_gpio() -> List[GPIOInfo]:
    """Detect GPIO availability (Linux: /sys/class/gpio or gpiod).

    Returns:
        List of detected GPIO chips. Empty list if GPIO not available.
    """
    # Method 1: gpiod Python bindings
    try:
        import gpiod
        chips = []
        for entry in sorted(glob.glob("/dev/gpiochip*")):
            chip_name = os.path.basename(entry)
            try:
                chip = gpiod.Chip(entry)
                num_lines = chip.num_lines if hasattr(chip, 'num_lines') else 0
                chip.close()
                chips.append(GPIOInfo(
                    chip=chip_name,
                    num_lines=num_lines,
                ))
            except Exception as e:
                logger.debug(f"GPIO: gpiod error on {chip_name}: {e}")
                chips.append(GPIOInfo(chip=chip_name))
        if chips:
            logger.debug(f"GPIO: gpiod found {len(chips)} chip(s)")
            return chips
    except ImportError:
        logger.debug("GPIO: gpiod not available, falling back to sysfs")
    except Exception as e:
        logger.debug(f"GPIO: gpiod failed: {e}")

    # Method 2: sysfs /sys/class/gpio/gpiochip*
    chips = []
    for chip_path in sorted(glob.glob("/sys/class/gpio/gpiochip*")):
        chip_name = os.path.basename(chip_path)
        ngpio_str = _read_sysfs(os.path.join(chip_path, "ngpio"))
        num_lines = int(ngpio_str) if ngpio_str and ngpio_str.isdigit() else 0
        chips.append(GPIOInfo(
            chip=chip_name,
            num_lines=num_lines,
        ))

    if chips:
        logger.debug(f"GPIO: sysfs found {len(chips)} chip(s)")

    return chips


# ─────────────────────────────────────────────────────────────────────────────
# Bluetooth Detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_bluetooth() -> List[BluetoothDevice]:
    """Detect Bluetooth/BLE devices in range.

    Tries Python bluetooth library, falls back to hcitool/bluetoothctl.

    Returns:
        List of discovered Bluetooth devices. Empty list on failure.
    """
    # Method 1: PyBluez
    try:
        import bluetooth
        nearby = bluetooth.discover_devices(duration=4, lookup_names=True, lookup_class=False)
        devices = []
        for addr, name in nearby:
            devices.append(BluetoothDevice(
                name=name or "Unknown",
                address=addr,
                type="classic",
            ))
        if devices:
            logger.debug(f"Bluetooth: PyBluez found {len(devices)} device(s)")
        return devices
    except ImportError:
        logger.debug("Bluetooth: PyBluez not available")
    except Exception as e:
        logger.debug(f"Bluetooth: PyBluez failed: {e}")

    # Method 2: bluetoothctl (Linux)
    if platform.system() == "Linux":
        try:
            # Start a quick scan and list
            result = subprocess.run(
                ["bluetoothctl", "devices"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                devices = []
                for line in result.stdout.strip().splitlines():
                    # Format: "Device AA:BB:CC:DD:EE:FF DeviceName"
                    parts = line.strip().split(None, 2)
                    if len(parts) >= 3 and parts[0] == "Device":
                        devices.append(BluetoothDevice(
                            name=parts[2],
                            address=parts[1],
                            type="classic",
                        ))
                if devices:
                    logger.debug(f"Bluetooth: bluetoothctl found {len(devices)} device(s)")
                return devices
        except FileNotFoundError:
            logger.debug("Bluetooth: bluetoothctl not found")
        except subprocess.TimeoutExpired:
            logger.debug("Bluetooth: bluetoothctl timed out")
        except Exception as e:
            logger.debug(f"Bluetooth: bluetoothctl failed: {e}")

    # Method 3: macOS system_profiler
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["system_profiler", "SPBluetoothDataType"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                devices = []
                current_name = ""
                current_address = ""
                in_devices_section = False

                for line in result.stdout.splitlines():
                    stripped = line.strip()
                    if "Connected:" in stripped or "Devices" in stripped:
                        in_devices_section = True
                        continue
                    if in_devices_section:
                        if stripped.endswith(":") and "Address" not in stripped:
                            if current_name and current_address:
                                devices.append(BluetoothDevice(
                                    name=current_name,
                                    address=current_address,
                                    type="classic",
                                ))
                            current_name = stripped.rstrip(":")
                            current_address = ""
                        elif "Address:" in stripped:
                            current_address = stripped.split(":", 1)[1].strip()

                if current_name and current_address:
                    devices.append(BluetoothDevice(
                        name=current_name,
                        address=current_address,
                        type="classic",
                    ))

                if devices:
                    logger.debug(f"Bluetooth: macOS found {len(devices)} device(s)")
                return devices
        except FileNotFoundError:
            pass
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            logger.debug(f"Bluetooth: macOS detection failed: {e}")

    return []


# ─────────────────────────────────────────────────────────────────────────────
# Network Interface Detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_network() -> List[NetworkInterface]:
    """Detect network interfaces with details.

    Uses psutil if available, falls back to `ip addr` (Linux) or
    `ifconfig` (macOS).

    Returns:
        List of detected network interfaces. Empty list on failure.
    """
    # Method 1: psutil (best — cross-platform, rich data)
    try:
        import psutil
        return _detect_network_psutil(psutil)
    except ImportError:
        logger.debug("Network: psutil not available, falling back to system commands")
    except Exception as e:
        logger.debug(f"Network: psutil failed: {e}")

    # Method 2: Platform-specific commands
    system = platform.system()
    if system == "Linux":
        return _detect_network_linux()
    elif system == "Darwin":
        return _detect_network_macos()

    return []


def _detect_network_psutil(psutil) -> List[NetworkInterface]:
    """Detect network interfaces using psutil."""
    interfaces = []
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()

    for iface_name, iface_stats in sorted(stats.items()):
        # Determine type
        iface_type = _classify_interface(iface_name)

        # Get IP and MAC addresses
        ip_addr = ""
        mac_addr = ""
        if iface_name in addrs:
            for addr in addrs[iface_name]:
                if addr.family == socket.AF_INET:
                    ip_addr = addr.address
                # psutil.AF_LINK = 17 on Linux, 18 on macOS
                if hasattr(psutil, "AF_LINK") and addr.family == psutil.AF_LINK:
                    mac_addr = addr.address

        state = "up" if iface_stats.isup else "down"
        extra = {}

        if iface_stats.speed > 0 and iface_type == "ethernet":
            extra["speed_mbps"] = iface_stats.speed

        # WiFi details (Linux)
        if iface_type == "wifi" and state == "up":
            wifi_info = _get_wifi_info(iface_name)
            extra.update(wifi_info)

        # CAN bitrate (Linux)
        if iface_type == "can" and state == "up":
            bitrate_str = _read_sysfs(
                f"/sys/class/net/{iface_name}/can_bittiming/bitrate"
            )
            if bitrate_str and bitrate_str.isdigit():
                extra["bitrate"] = int(bitrate_str)

        interfaces.append(NetworkInterface(
            name=iface_name,
            type=iface_type,
            ip=ip_addr,
            mac=mac_addr,
            state=state,
            extra=extra,
        ))

    return interfaces


def _detect_network_linux() -> List[NetworkInterface]:
    """Detect network interfaces on Linux using `ip addr`."""
    interfaces = []
    try:
        result = subprocess.run(
            ["ip", "-o", "addr", "show"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return interfaces

        seen = {}
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            iface_name = parts[1]
            # ip -o shows "eth0" or "eth0:" — strip colon
            iface_name = iface_name.rstrip(":")

            if iface_name not in seen:
                iface_type = _classify_interface(iface_name)
                seen[iface_name] = NetworkInterface(
                    name=iface_name,
                    type=iface_type,
                    state="up",
                    extra={},
                )

            # Parse inet addresses
            if "inet " in line:
                for i, p in enumerate(parts):
                    if p == "inet" and i + 1 < len(parts):
                        seen[iface_name].ip = parts[i + 1].split("/")[0]

            # Parse link/ether MAC
            if "link/ether" in line:
                for i, p in enumerate(parts):
                    if p == "link/ether" and i + 1 < len(parts):
                        seen[iface_name].mac = parts[i + 1]

        interfaces = list(seen.values())

        # Enrich WiFi info
        for iface in interfaces:
            if iface.type == "wifi" and iface.state == "up":
                iface.extra.update(_get_wifi_info(iface.name))

    except FileNotFoundError:
        logger.debug("Network: 'ip' command not found")
    except subprocess.TimeoutExpired:
        logger.debug("Network: 'ip' command timed out")
    except Exception as e:
        logger.debug(f"Network: Linux detection failed: {e}")

    return interfaces


def _detect_network_macos() -> List[NetworkInterface]:
    """Detect network interfaces on macOS using `ifconfig`."""
    interfaces = []
    try:
        result = subprocess.run(
            ["ifconfig"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return interfaces

        current_iface = None
        for line in result.stdout.splitlines():
            # Interface header line: "en0: flags=..."
            if not line.startswith("\t") and not line.startswith(" ") and ":" in line:
                iface_name = line.split(":")[0]
                iface_type = _classify_interface(iface_name)
                state = "up" if "UP" in line else "down"
                current_iface = NetworkInterface(
                    name=iface_name,
                    type=iface_type,
                    state=state,
                    extra={},
                )
                interfaces.append(current_iface)
            elif current_iface:
                stripped = line.strip()
                if stripped.startswith("inet "):
                    parts = stripped.split()
                    if len(parts) >= 2:
                        current_iface.ip = parts[1]
                elif stripped.startswith("ether "):
                    parts = stripped.split()
                    if len(parts) >= 2:
                        current_iface.mac = parts[1]

        # Enrich WiFi info on macOS
        for iface in interfaces:
            if iface.type == "wifi" and iface.state == "up":
                iface.extra.update(_get_wifi_info_macos(iface.name))

    except FileNotFoundError:
        logger.debug("Network: ifconfig not found")
    except subprocess.TimeoutExpired:
        logger.debug("Network: ifconfig timed out")
    except Exception as e:
        logger.debug(f"Network: macOS detection failed: {e}")

    return interfaces


def _classify_interface(name: str) -> str:
    """Classify a network interface by its name."""
    name_lower = name.lower()
    if name_lower.startswith("lo") or name_lower == "lo0":
        return "loopback"
    if name_lower.startswith(("wlan", "wlp", "wlx")):
        return "wifi"
    if name_lower.startswith(("en", "eth", "enp", "eno", "ens")):
        # On macOS en0 is often WiFi — check further
        if platform.system() == "Darwin" and name_lower in ("en0",):
            # en0 is WiFi on most Macs
            return "wifi"
        return "ethernet"
    if name_lower.startswith("can") or name_lower.startswith("vcan"):
        return "can"
    if name_lower.startswith("docker") or name_lower.startswith("br-"):
        return "bridge"
    if name_lower.startswith("veth"):
        return "veth"
    if name_lower.startswith(("awdl", "llw")):
        return "airdrop"
    if name_lower.startswith("utun") or name_lower.startswith("tun"):
        return "tunnel"
    return "other"


def _get_wifi_info(iface_name: str) -> Dict[str, Any]:
    """Get WiFi details for an interface on Linux using iwconfig/iw."""
    info = {}
    # Try iw first
    try:
        result = subprocess.run(
            ["iw", "dev", iface_name, "link"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped.startswith("SSID:"):
                    info["ssid"] = stripped.split(":", 1)[1].strip()
                elif "signal:" in stripped:
                    # e.g. "signal: -52 dBm"
                    parts = stripped.split()
                    for i, p in enumerate(parts):
                        if p == "signal:" and i + 1 < len(parts):
                            try:
                                info["signal_dbm"] = int(parts[i + 1])
                            except ValueError:
                                pass
            if info:
                return info
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    except Exception as e:
        logger.debug(f"WiFi: iw failed for {iface_name}: {e}")

    # Try iwconfig fallback
    try:
        result = subprocess.run(
            ["iwconfig", iface_name],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if 'ESSID:"' in line:
                    start = line.index('ESSID:"') + 7
                    end = line.index('"', start)
                    info["ssid"] = line[start:end]
                if "Signal level=" in line:
                    part = line.split("Signal level=")[1].split()[0]
                    try:
                        info["signal_dbm"] = int(part.replace("dBm", ""))
                    except ValueError:
                        pass
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    except Exception as e:
        logger.debug(f"WiFi: iwconfig failed for {iface_name}: {e}")

    return info


def _get_wifi_info_macos(iface_name: str) -> Dict[str, Any]:
    """Get WiFi details for an interface on macOS."""
    info = {}
    try:
        # macOS 14.4+: use the airport utility path
        airport_path = (
            "/System/Library/PrivateFrameworks/Apple80211.framework"
            "/Versions/Current/Resources/airport"
        )
        if os.path.exists(airport_path):
            result = subprocess.run(
                [airport_path, "-I"],
                capture_output=True, text=True, timeout=3,
            )
        else:
            # Try networksetup as fallback
            result = subprocess.run(
                ["networksetup", "-getairportnetwork", iface_name],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0 and ":" in result.stdout:
                ssid = result.stdout.split(":", 1)[1].strip()
                if ssid:
                    info["ssid"] = ssid
            return info

        if result.returncode == 0:
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped.startswith("SSID:"):
                    info["ssid"] = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("agrCtlRSSI:"):
                    try:
                        info["signal_dbm"] = int(stripped.split(":", 1)[1].strip())
                    except ValueError:
                        pass
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    except Exception as e:
        logger.debug(f"WiFi: macOS detection failed for {iface_name}: {e}")

    return info


# ─────────────────────────────────────────────────────────────────────────────
# System Info Detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_system() -> SystemInfo:
    """Get comprehensive system information.

    Uses stdlib modules (platform, os, shutil, socket) for broad compatibility.

    Returns:
        SystemInfo dataclass. Fields may be empty strings / zero if unavailable.
    """
    info = SystemInfo()

    try:
        info.hostname = socket.gethostname()
    except Exception:
        info.hostname = "unknown"

    info.platform = platform.system()
    info.arch = platform.machine()
    info.python_version = platform.python_version()

    # CPU info
    info.cpu = _get_cpu_info()
    try:
        info.cpu_cores = os.cpu_count() or 0
    except Exception:
        info.cpu_cores = 0

    # RAM
    info.ram_mb = _get_ram_mb()

    # Disk (available space on root partition)
    try:
        usage = shutil.disk_usage("/")
        info.disk_gb = round(usage.free / (1024 ** 3), 1)
    except Exception:
        info.disk_gb = 0.0

    # OS version
    info.os_version = _get_os_version()

    return info


def _get_cpu_info() -> str:
    """Get a human-readable CPU description."""
    system = platform.system()

    # Linux: /proc/cpuinfo
    if system == "Linux":
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
                    # ARM chips often use "Hardware" or "Model"
                    if line.startswith("Hardware"):
                        return line.split(":", 1)[1].strip()
        except (OSError, IOError):
            pass

        # ARM fallback: check device-tree
        model = _read_sysfs("/proc/device-tree/model")
        if model:
            return model

    # macOS: sysctl
    if system == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return platform.processor() or platform.machine()


def _get_ram_mb() -> int:
    """Get total RAM in megabytes."""
    # Method 1: psutil
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 * 1024))
    except ImportError:
        pass

    # Method 2: Linux /proc/meminfo
    if platform.system() == "Linux":
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        # Value is in kB
                        kb = int(line.split()[1])
                        return round(kb / 1024)
        except (OSError, IOError, ValueError):
            pass

    # Method 3: macOS sysctl
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                return round(int(result.stdout.strip()) / (1024 * 1024))
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass

    return 0


def _get_os_version() -> str:
    """Get a human-readable OS version string."""
    system = platform.system()

    if system == "Linux":
        # Try /etc/os-release
        try:
            with open("/etc/os-release", "r") as f:
                info = {}
                for line in f:
                    if "=" in line:
                        key, val = line.strip().split("=", 1)
                        info[key] = val.strip('"')
                pretty = info.get("PRETTY_NAME")
                if pretty:
                    return pretty
        except (OSError, IOError):
            pass

        return f"Linux {platform.release()}"

    if system == "Darwin":
        mac_ver = platform.mac_ver()[0]
        if mac_ver:
            return f"macOS {mac_ver}"
        return "macOS"

    return platform.platform()


# ─────────────────────────────────────────────────────────────────────────────
# Full Scan (aggregate all detectors)
# ─────────────────────────────────────────────────────────────────────────────

def detect_all(bus: int = 1) -> Dict[str, Any]:
    """Run all detection functions and return a combined dictionary.

    This is used by `plexus scan --json` and for the comprehensive scan output.

    Args:
        bus: I2C bus number for sensor scanning.

    Returns:
        Dictionary with keys for each hardware category.
    """
    # System info
    sys_info = detect_system()

    # I2C sensors
    _, sensors = detect_sensors(bus)

    # Cameras
    _, cameras = detect_cameras()

    # Serial ports
    serial_devices = detect_serial()

    # USB devices
    usb_devices = detect_usb()

    # Network interfaces
    network_interfaces = detect_network()

    # GPIO
    gpio_chips = detect_gpio()

    # Bluetooth
    bt_devices = detect_bluetooth()

    # CAN
    _, can_up, can_down = detect_can()

    return {
        "system": asdict(sys_info),
        "sensors": [
            {
                "name": s.name,
                "address": f"0x{s.address:02X}",
                "bus": s.bus,
                "description": s.description,
            }
            for s in sensors
        ],
        "cameras": [
            {
                "name": c.name,
                "device_id": c.device_id,
                "description": c.description,
            }
            for c in cameras
        ],
        "serial": [asdict(d) for d in serial_devices],
        "usb": [asdict(d) for d in usb_devices],
        "network": [
            {
                "name": n.name,
                "type": n.type,
                "ip": n.ip,
                "mac": n.mac,
                "state": n.state,
                "extra": n.extra,
            }
            for n in network_interfaces
        ],
        "gpio": [asdict(g) for g in gpio_chips],
        "bluetooth": [asdict(b) for b in bt_devices],
        "can": {
            "up": [
                {
                    "interface": c.interface,
                    "channel": c.channel,
                    "bitrate": c.bitrate,
                }
                for c in can_up
            ],
            "down": [
                {
                    "interface": c.interface,
                    "channel": c.channel,
                }
                for c in can_down
            ],
        },
    }
