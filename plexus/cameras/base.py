"""
Base camera class and utilities for Plexus camera drivers.

All camera drivers inherit from BaseCamera and implement the capture() method.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Callable
import time


@dataclass
class CameraFrame:
    """A single camera frame with JPEG-encoded image data."""
    data: bytes
    width: int
    height: int
    timestamp: float = field(default_factory=time.time)
    camera_id: str = ""
    tags: Dict[str, str] = field(default_factory=dict)


class BaseCamera(ABC):
    """
    Base class for all camera drivers.

    Subclasses must implement:
    - capture() -> Optional[CameraFrame]: Capture a single frame
    - name: Human-readable camera name

    Optional overrides:
    - setup(): Initialize the camera (called once)
    - cleanup(): Clean up resources (called on stop)
    - is_available(): Check if camera is connected
    """

    name: str = "Unknown Camera"
    description: str = ""

    def __init__(
        self,
        frame_rate: float = 10.0,
        resolution: Tuple[int, int] = (640, 480),
        quality: int = 80,
        camera_id: str = "",
        tags: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize the camera driver.

        Args:
            frame_rate: Target frames per second. Default 10 fps.
            resolution: (width, height) tuple. Default (640, 480).
            quality: JPEG quality 1-100. Default 80.
            camera_id: Unique identifier for this camera.
            tags: Tags to add to all frames from this camera.
        """
        self.frame_rate = frame_rate
        self.resolution = resolution
        self.quality = max(1, min(100, quality))
        self.camera_id = camera_id
        self.tags = tags or {}
        self._running = False
        self._error: Optional[str] = None

    @abstractmethod
    def capture(self) -> Optional[CameraFrame]:
        """
        Capture a single frame from the camera.

        Returns:
            CameraFrame with JPEG-encoded image data, or None if capture failed.
        """
        pass

    def setup(self) -> None:
        """
        Initialize the camera hardware.
        Called once before capturing starts.
        Override in subclass if needed.
        """
        pass

    def cleanup(self) -> None:
        """
        Clean up camera resources.
        Called when camera is stopped.
        Override in subclass if needed.
        """
        pass

    def is_available(self) -> bool:
        """
        Check if the camera is connected and responding.

        Returns:
            True if camera is available.
        """
        try:
            frame = self.capture()
            return frame is not None
        except Exception:
            return False

    def get_info(self) -> Dict[str, Any]:
        """Get camera information for display."""
        return {
            "camera_id": self.camera_id,
            "name": self.name,
            "description": self.description,
            "frame_rate": self.frame_rate,
            "resolution": list(self.resolution),
            "quality": self.quality,
            "available": self.is_available(),
        }


class CameraHub:
    """
    Manages multiple cameras.

    Usage:
        from plexus.cameras import CameraHub, USBCamera

        hub = CameraHub()
        hub.add(USBCamera(device_index=0))
        hub.add(USBCamera(device_index=1))

        # Capture from all cameras
        frames = hub.capture_all()
    """

    def __init__(self):
        self.cameras: List[BaseCamera] = []
        self._on_frame: Optional[Callable[[CameraFrame], None]] = None
        self._on_error: Optional[Callable[[BaseCamera, Exception], None]] = None

    def add(self, camera: BaseCamera) -> "CameraHub":
        """Add a camera to the hub."""
        self.cameras.append(camera)
        return self

    def remove(self, camera: BaseCamera) -> "CameraHub":
        """Remove a camera from the hub."""
        self.cameras.remove(camera)
        return self

    def setup(self) -> None:
        """Initialize all cameras."""
        for camera in self.cameras:
            try:
                camera.setup()
            except Exception as e:
                camera._error = str(e)
                if self._on_error:
                    self._on_error(camera, e)

    def cleanup(self) -> None:
        """Clean up all cameras."""
        for camera in self.cameras:
            try:
                camera.cleanup()
            except Exception:
                pass

    def capture_all(self) -> List[CameraFrame]:
        """Capture from all cameras once."""
        frames = []
        for camera in self.cameras:
            try:
                frame = camera.capture()
                if frame:
                    frames.append(frame)
            except Exception as e:
                camera._error = str(e)
                if self._on_error:
                    self._on_error(camera, e)
        return frames

    def get_camera(self, camera_id: str) -> Optional[BaseCamera]:
        """Get a camera by ID."""
        for camera in self.cameras:
            if camera.camera_id == camera_id:
                return camera
        return None

    def get_info(self) -> List[Dict[str, Any]]:
        """Get info about all cameras."""
        return [c.get_info() for c in self.cameras]

    def on_frame(self, callback: Callable[[CameraFrame], None]) -> "CameraHub":
        """Set callback for each frame."""
        self._on_frame = callback
        return self

    def on_error(self, callback: Callable[[BaseCamera, Exception], None]) -> "CameraHub":
        """Set callback for camera errors."""
        self._on_error = callback
        return self
