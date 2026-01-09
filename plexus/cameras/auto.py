"""
Camera auto-detection utilities.

Scans for available cameras and creates appropriate drivers.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple, Type

from plexus.cameras.base import BaseCamera, CameraHub


@dataclass
class DetectedCamera:
    """Information about a detected camera."""
    name: str
    device_id: str
    driver: Type[BaseCamera]
    description: str


def scan_usb_cameras(max_cameras: int = 10) -> List[DetectedCamera]:
    """
    Scan for USB cameras using OpenCV.

    Args:
        max_cameras: Maximum number of device indices to check.

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

    Returns:
        List of all detected cameras.
    """
    cameras = []
    cameras.extend(scan_usb_cameras())
    cameras.extend(scan_pi_cameras())
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
