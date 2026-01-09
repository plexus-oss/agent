"""
USB webcam driver using OpenCV.

Supports any camera compatible with cv2.VideoCapture (USB webcams, built-in cameras).
"""

import time
from typing import Optional, Tuple

from plexus.cameras.base import BaseCamera, CameraFrame

# OpenCV is optional - only imported when USBCamera is used
try:
    import cv2
    import numpy as np
    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False


class USBCamera(BaseCamera):
    """
    USB webcam driver using OpenCV VideoCapture.

    Works with USB webcams, built-in laptop cameras, and other
    video capture devices supported by OpenCV.

    Usage:
        from plexus.cameras import USBCamera

        camera = USBCamera(device_index=0)
        camera.setup()
        frame = camera.capture()
        camera.cleanup()
    """

    name = "USB Camera"
    description = "USB webcam via OpenCV VideoCapture"

    def __init__(
        self,
        device_index: int = 0,
        frame_rate: float = 15.0,
        resolution: Tuple[int, int] = (640, 480),
        quality: int = 80,
        camera_id: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize USB camera driver.

        Args:
            device_index: Camera device index (0 = first camera, 1 = second, etc.)
            frame_rate: Target frames per second. Default 15 fps.
            resolution: (width, height) tuple. Default (640, 480).
            quality: JPEG quality 1-100. Default 80.
            camera_id: Unique identifier. Defaults to "usb:{device_index}".
        """
        if not OPENCV_AVAILABLE:
            raise ImportError(
                "OpenCV is required for USB camera support. "
                "Install with: pip install plexus-agent[camera]"
            )

        super().__init__(
            frame_rate=frame_rate,
            resolution=resolution,
            quality=quality,
            camera_id=camera_id or f"usb:{device_index}",
            **kwargs,
        )
        self.device_index = device_index
        self._cap: Optional[cv2.VideoCapture] = None

    def setup(self) -> None:
        """Initialize the camera."""
        self._cap = cv2.VideoCapture(self.device_index)

        if not self._cap.isOpened():
            raise RuntimeError(f"Failed to open camera at index {self.device_index}")

        # Set resolution
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])

        # Set frame rate if supported
        self._cap.set(cv2.CAP_PROP_FPS, self.frame_rate)

        # Read actual values (camera may not support requested settings)
        actual_width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.resolution = (actual_width, actual_height)

    def cleanup(self) -> None:
        """Release the camera."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def capture(self) -> Optional[CameraFrame]:
        """
        Capture a single frame.

        Returns:
            CameraFrame with JPEG-encoded image, or None if capture failed.
        """
        if self._cap is None:
            self.setup()

        ret, frame = self._cap.read()
        if not ret or frame is None:
            return None

        # Encode to JPEG
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, self.quality]
        success, jpeg_data = cv2.imencode('.jpg', frame, encode_params)

        if not success:
            return None

        return CameraFrame(
            data=jpeg_data.tobytes(),
            width=frame.shape[1],
            height=frame.shape[0],
            timestamp=time.time(),
            camera_id=self.camera_id,
            tags=self.tags.copy(),
        )

    def is_available(self) -> bool:
        """Check if camera is available without fully initializing."""
        if not OPENCV_AVAILABLE:
            return False

        cap = cv2.VideoCapture(self.device_index)
        available = cap.isOpened()
        cap.release()
        return available

    def get_info(self) -> dict:
        """Get camera information."""
        info = super().get_info()
        info["device_index"] = self.device_index
        return info
