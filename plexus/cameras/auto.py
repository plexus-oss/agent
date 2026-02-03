"""
Camera auto-detection utilities.

Scans for available cameras and creates appropriate drivers.
"""

import os
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple, Type

from plexus.cameras.base import BaseCamera, CameraHub


@dataclass
class DetectedCamera:
    """Information about a detected camera."""
    name: str
    device_id: str
    driver: Type[BaseCamera]
    description: str


def _get_pi_camera_v4l2_indices() -> Set[int]:
    """Get V4L2 device indices that belong to the Pi Camera pipeline.

    On Raspberry Pi, the camera module exposes multiple /dev/video* devices
    through the Unicam/ISP pipeline. These should not be opened by OpenCV
    as they conflict with picamera2/libcamera access.
    """
    pi_indices: Set[int] = set()
    sysfs = "/sys/class/video4linux"
    if not os.path.isdir(sysfs):
        return pi_indices
    for entry in os.listdir(sysfs):
        if not entry.startswith("video"):
            continue
        try:
            with open(os.path.join(sysfs, entry, "name")) as f:
                name = f.read().strip().lower()
            if "unicam" in name or "bcm2835" in name or "rp1" in name:
                pi_indices.add(int(entry[5:]))  # strip "video" prefix
        except (IOError, OSError, ValueError):
            pass
    return pi_indices


def scan_usb_cameras(
    max_cameras: int = 10, skip_indices: Optional[Set[int]] = None
) -> List[DetectedCamera]:
    """
    Scan for USB cameras using OpenCV.

    Args:
        max_cameras: Maximum number of device indices to check.
        skip_indices: V4L2 device indices to skip (e.g. Pi Camera devices).

    Returns:
        List of detected USB cameras.
    """
    try:
        import cv2
    except ImportError:
        return []

    from plexus.cameras.usb import USBCamera

    detected = []

    for i in range(max_cameras):
        if skip_indices and i in skip_indices:
            continue
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            # Get camera info if available
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cap.release()

            detected.append(DetectedCamera(
                name=f"USB Camera {i}",
                device_id=f"usb:{i}",
                driver=USBCamera,
                description=f"USB webcam at index {i} ({width}x{height})",
            ))
        else:
            cap.release()
            # Stop after first failed index (cameras are typically sequential)
            if i > 0:
                break

    return detected


def scan_pi_cameras() -> List[DetectedCamera]:
    """
    Scan for Raspberry Pi cameras using picamera2.

    Returns:
        List of detected Pi cameras.
    """
    try:
        from picamera2 import Picamera2
    except ImportError:
        return []

    from plexus.cameras.picamera import PiCamera

    detected = []

    try:
        # Get list of available cameras
        camera_info = Picamera2.global_camera_info()

        for i, info in enumerate(camera_info):
            model = info.get("Model", "Unknown")
            detected.append(DetectedCamera(
                name=f"Pi Camera {i}",
                device_id=f"picam:{i}",
                driver=PiCamera,
                description=f"Raspberry Pi Camera: {model}",
            ))
    except Exception:
        pass

    return detected


def scan_cameras() -> List[DetectedCamera]:
    """
    Scan for all available cameras (USB and Pi).

    Pi cameras are scanned first so their V4L2 devices can be excluded
    from the USB scan, preventing duplicate detection and device conflicts.

    Returns:
        List of all detected cameras.
    """
    cameras = []

    pi_cameras = scan_pi_cameras()
    cameras.extend(pi_cameras)

    # If Pi cameras exist, skip their V4L2 device indices so OpenCV
    # doesn't open them (which blocks picamera2/libcamera access).
    skip = _get_pi_camera_v4l2_indices() if pi_cameras else set()
    cameras.extend(scan_usb_cameras(skip_indices=skip))

    return cameras


def auto_cameras(
    frame_rate: Optional[float] = None,
    resolution: Optional[Tuple[int, int]] = None,
    quality: Optional[int] = None,
) -> CameraHub:
    """
    Auto-detect cameras and create a CameraHub.

    Args:
        frame_rate: Override frame rate for all cameras.
        resolution: Override resolution for all cameras.
        quality: Override JPEG quality for all cameras.

    Returns:
        CameraHub with detected cameras added.
    """
    hub = CameraHub()
    detected = scan_cameras()

    for camera_info in detected:
        kwargs = {"camera_id": camera_info.device_id}

        if frame_rate is not None:
            kwargs["frame_rate"] = frame_rate
        if resolution is not None:
            kwargs["resolution"] = resolution
        if quality is not None:
            kwargs["quality"] = quality

        # Create camera instance based on driver type
        if camera_info.device_id.startswith("usb:"):
            device_index = int(camera_info.device_id.split(":")[1])
            kwargs["device_index"] = device_index
        elif camera_info.device_id.startswith("picam:"):
            camera_num = int(camera_info.device_id.split(":")[1])
            kwargs["camera_num"] = camera_num

        try:
            camera = camera_info.driver(**kwargs)
            hub.add(camera)
        except Exception:
            pass

    return hub


def get_camera_info() -> List[dict]:
    """
    Get information about supported camera types.

    Returns:
        List of camera type info dicts.
    """
    return [
        {
            "name": "USB Camera",
            "description": "USB webcams and built-in cameras via OpenCV",
            "requires": "opencv-python",
            "install": "pip install plexus-agent[camera]",
        },
        {
            "name": "Pi Camera",
            "description": "Raspberry Pi Camera Module via picamera2",
            "requires": "picamera2",
            "install": "pip install plexus-agent[picamera]",
        },
    ]
