"""
Raspberry Pi Camera Module driver using picamera2.

Supports Pi Camera Module v1, v2, v3, and HQ Camera.
"""

import time
from typing import Optional, Tuple

from plexus.cameras.base import BaseCamera, CameraFrame

# picamera2 is optional - only imported when PiCamera is used
try:
    from picamera2 import Picamera2
    from picamera2.encoders import JpegEncoder
    from picamera2.outputs import FileOutput
    import io
    PICAMERA_AVAILABLE = True
except ImportError:
    PICAMERA_AVAILABLE = False


class PiCamera(BaseCamera):
    """
    Raspberry Pi Camera Module driver using picamera2.

    Works with Pi Camera Module v1, v2, v3, and HQ Camera on
    Raspberry Pi devices running Raspberry Pi OS.

    Usage:
        from plexus.cameras import PiCamera

        camera = PiCamera(camera_num=0)
        camera.setup()
        frame = camera.capture()
        camera.cleanup()
    """

    name = "Pi Camera"
    description = "Raspberry Pi Camera Module via picamera2"

    def __init__(
        self,
        camera_num: int = 0,
        frame_rate: float = 30.0,
        resolution: Tuple[int, int] = (1280, 720),
        quality: int = 85,
        camera_id: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize Pi Camera driver.

        Args:
            camera_num: Camera number (0 = first camera, 1 = second, etc.)
            frame_rate: Target frames per second. Default 30 fps.
            resolution: (width, height) tuple. Default (1280, 720).
            quality: JPEG quality 1-100. Default 85.
            camera_id: Unique identifier. Defaults to "picam:{camera_num}".
        """
        if not PICAMERA_AVAILABLE:
            raise ImportError(
                "picamera2 is required for Pi Camera support. "
                "Install with: pip install plexus-agent[picamera]"
            )

        super().__init__(
            frame_rate=frame_rate,
            resolution=resolution,
            quality=quality,
            camera_id=camera_id or f"picam:{camera_num}",
            **kwargs,
        )
        self.camera_num = camera_num
        self._picam: Optional[Picamera2] = None

    def setup(self) -> None:
        """Initialize the camera."""
        self._picam = Picamera2(camera_num=self.camera_num)

        # Configure for still capture with specified resolution
        config = self._picam.create_still_configuration(
            main={"size": self.resolution, "format": "RGB888"},
        )
        self._picam.configure(config)
        self._picam.start()

    def cleanup(self) -> None:
        """Stop and close the camera."""
        if self._picam is not None:
            self._picam.stop()
            self._picam.close()
            self._picam = None

    def capture(self) -> Optional[CameraFrame]:
        """
        Capture a single frame.

        Returns:
            CameraFrame with JPEG-encoded image, or None if capture failed.
        """
        if self._picam is None:
            self.setup()

        try:
            # Capture to numpy array
            frame = self._picam.capture_array()

            if frame is None:
                return None

            # Encode to JPEG using OpenCV if available, otherwise use PIL
            try:
                import cv2
                encode_params = [cv2.IMWRITE_JPEG_QUALITY, self.quality]
                # Convert RGB to BGR for OpenCV
                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                success, jpeg_data = cv2.imencode('.jpg', frame_bgr, encode_params)
                if not success:
                    return None
                data = jpeg_data.tobytes()
            except ImportError:
                # Fallback to PIL
                from PIL import Image
                img = Image.fromarray(frame)
                buffer = io.BytesIO()
                img.save(buffer, format='JPEG', quality=self.quality)
                data = buffer.getvalue()

            return CameraFrame(
                data=data,
                width=frame.shape[1],
                height=frame.shape[0],
                timestamp=time.time(),
                camera_id=self.camera_id,
                tags=self.tags.copy(),
            )

        except Exception:
            return None

    def is_available(self) -> bool:
        """Check if camera is available."""
        if not PICAMERA_AVAILABLE:
            return False

        try:
            camera_info = Picamera2.global_camera_info()
            return len(camera_info) > self.camera_num
        except Exception:
            return False

    def get_info(self) -> dict:
        """Get camera information."""
        info = super().get_info()
        info["camera_num"] = self.camera_num

        # Get model info if available
        if PICAMERA_AVAILABLE:
            try:
                camera_info = Picamera2.global_camera_info()
                if len(camera_info) > self.camera_num:
                    info["model"] = camera_info[self.camera_num].get("Model", "Unknown")
            except Exception:
                pass

        return info
