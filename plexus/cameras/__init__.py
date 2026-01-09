"""
Plexus camera drivers for streaming video from hardware devices.

Supports USB webcams and Raspberry Pi Camera Modules.

Usage:
    # USB webcam
    from plexus.cameras import USBCamera
    camera = USBCamera(device_index=0)
    frame = camera.capture()

    # Auto-detect cameras
    from plexus.cameras import scan_cameras, auto_cameras
    detected = scan_cameras()
    hub = auto_cameras()

Install camera support:
    pip install plexus-agent[camera]      # USB webcams (OpenCV)
    pip install plexus-agent[picamera]    # Raspberry Pi Camera
"""

from plexus.cameras.base import BaseCamera, CameraFrame, CameraHub
from plexus.cameras.auto import (
    DetectedCamera,
    scan_cameras,
    scan_usb_cameras,
    scan_pi_cameras,
    auto_cameras,
    get_camera_info,
)

# Optional imports - only available if dependencies installed
try:
    from plexus.cameras.usb import USBCamera
except ImportError:
    USBCamera = None

try:
    from plexus.cameras.picamera import PiCamera
except ImportError:
    PiCamera = None


__all__ = [
    # Base classes
    "BaseCamera",
    "CameraFrame",
    "CameraHub",
    # Drivers (may be None if dependencies not installed)
    "USBCamera",
    "PiCamera",
    # Auto-detection
    "DetectedCamera",
    "scan_cameras",
    "scan_usb_cameras",
    "scan_pi_cameras",
    "auto_cameras",
    "get_camera_info",
]
