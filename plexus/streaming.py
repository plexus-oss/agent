"""
Stream management for Plexus devices.

Handles real-time sensor and camera streaming over WebSocket,
with optional HTTP persistence for recording.
"""

import asyncio
import base64
import json
import logging
import time
from typing import Optional, Callable, List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from plexus.sensors.base import SensorHub
    from plexus.cameras.base import CameraHub
    from plexus.adapters.can_detect import DetectedCAN

logger = logging.getLogger(__name__)


class StreamManager:
    """Manages sensor and camera streams.

    Args:
        sensor_hub: SensorHub instance for reading sensors.
        camera_hub: CameraHub instance for capturing frames.
        on_status: Callback for status messages.
        persist_fn: Async function to persist data points (called when recording).
    """

    def __init__(
        self,
        sensor_hub: Optional["SensorHub"] = None,
        camera_hub: Optional["CameraHub"] = None,
        can_adapters: Optional[List["DetectedCAN"]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        persist_fn: Optional[Callable[[List[Dict[str, Any]]], Any]] = None,
    ):
        self.sensor_hub = sensor_hub
        self.camera_hub = camera_hub
        self.can_adapters = can_adapters or []
        self.on_status = on_status or (lambda x: None)
        self.persist_fn = persist_fn

        self._active_streams: dict[str, asyncio.Task] = {}
        self._active_camera_streams: dict[str, asyncio.Task] = {}
        self._active_can_streams: dict[str, asyncio.Task] = {}
        self._can_instances: dict[str, Any] = {}  # channel -> CANAdapter
        self._recording: bool = False

    # =========================================================================
    # Sensor Streaming
    # =========================================================================

    async def start_stream(self, data: dict, ws):
        """Start streaming sensor data.

        Args (from dashboard):
            store: bool - If True, persist to ClickHouse. If False, real-time only.
            metrics: list - Which metrics to stream (empty = all)
            interval_ms: int - Sampling interval
        """
        stream_id = data.get("id", f"stream_{int(time.time())}")
        metrics = data.get("metrics", [])
        interval_ms = data.get("interval_ms", 100)
        store = data.get("store", False)

        self._recording = store

        if not self.sensor_hub:
            self.on_status("No sensors configured")
            return

        mode = "Recording" if store else "Viewing"
        self.on_status(f"{mode}: {metrics or 'all'} @ {interval_ms}ms")

        async def stream_loop():
            # Parse metric filters (strip source_id prefix if present)
            filters = set()
            for m in metrics:
                filters.add(m.split(":", 1)[-1] if ":" in m else m)

            try:
                while stream_id in self._active_streams:
                    readings = self.sensor_hub.read_all()
                    if filters:
                        readings = [r for r in readings if r.metric in filters]

                    points = [
                        {"metric": r.metric, "value": r.value, "timestamp": int(time.time() * 1000)}
                        for r in readings
                    ]

                    # Always send to WebSocket (real-time display)
                    await ws.send(json.dumps({"type": "telemetry", "points": points}))

                    # If recording, also persist via HTTP
                    if self._recording and points and self.persist_fn:
                        asyncio.create_task(self.persist_fn(points))

                    await asyncio.sleep(interval_ms / 1000)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.on_status(f"Stream error: {e}")

        self._active_streams[stream_id] = asyncio.create_task(stream_loop())

    async def stop_stream(self, data: dict):
        """Stop sensor streaming."""
        stream_id = data.get("id")

        if stream_id == "*":
            for task in self._active_streams.values():
                task.cancel()
            self._active_streams.clear()
            self.on_status("Stopped all streams")
        elif stream_id in self._active_streams:
            self._active_streams[stream_id].cancel()
            del self._active_streams[stream_id]
            self.on_status("Stopped stream")

        self._recording = False

    async def configure_sensor(self, data: dict):
        """Configure a sensor's runtime parameters.

        Supports changing sample_rate and prefix. Sensors may also
        implement a configure() method for driver-specific settings.
        """
        if not self.sensor_hub:
            return

        sensor = self.sensor_hub.get_sensor(data.get("sensor"))
        if not sensor:
            return

        config = data.get("config", {})

        # Apply generic settings that all sensors support
        if "sample_rate" in config:
            sensor.sample_rate = float(config["sample_rate"])
        if "prefix" in config:
            sensor.prefix = config["prefix"]

        # Delegate driver-specific config if the sensor supports it
        if hasattr(sensor, "configure"):
            try:
                sensor.configure(**config)
            except Exception as e:
                self.on_status(f"Config failed: {e}")
                return

        self.on_status(f"Configured {data.get('sensor')}")

    # =========================================================================
    # Camera Streaming
    # =========================================================================

    async def start_camera(self, data: dict, ws):
        """Start camera streaming."""
        camera_id = data.get("camera_id")
        frame_rate = data.get("frame_rate", 10)

        if not self.camera_hub:
            self.on_status("No cameras configured")
            return

        camera = self.camera_hub.get_camera(camera_id)
        if not camera:
            self.on_status(f"Camera not found: {camera_id}")
            return

        if data.get("resolution"):
            camera.resolution = tuple(data["resolution"])
        if data.get("quality"):
            camera.quality = data["quality"]
        camera.frame_rate = frame_rate

        # Stop existing stream for this camera before starting a new one
        if camera_id in self._active_camera_streams:
            self._active_camera_streams[camera_id].cancel()
            try:
                await self._active_camera_streams[camera_id]
            except (asyncio.CancelledError, Exception):
                pass
            del self._active_camera_streams[camera_id]

        self.on_status(f"Camera {camera_id} @ {frame_rate}fps")

        async def camera_loop():
            interval = 1.0 / frame_rate
            try:
                camera.setup()
                while camera_id in self._active_camera_streams:
                    frame = camera.capture()
                    if frame:
                        await ws.send(json.dumps({
                            "type": "video_frame",
                            "camera_id": camera_id,
                            "frame": base64.b64encode(frame.data).decode('ascii'),
                            "width": frame.width,
                            "height": frame.height,
                            "timestamp": int(frame.timestamp * 1000),
                        }))
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                pass
            finally:
                camera.cleanup()

        self._active_camera_streams[camera_id] = asyncio.create_task(camera_loop())

    async def stop_camera(self, data: dict):
        """Stop camera streaming."""
        camera_id = data.get("camera_id")

        if camera_id == "*":
            for task in self._active_camera_streams.values():
                task.cancel()
            self._active_camera_streams.clear()
        elif camera_id in self._active_camera_streams:
            self._active_camera_streams[camera_id].cancel()
            del self._active_camera_streams[camera_id]

        self.on_status("Stopped camera")

    async def configure_camera(self, data: dict):
        """Configure a camera."""
        if not self.camera_hub:
            return

        camera = self.camera_hub.get_camera(data.get("camera_id"))
        if camera:
            config = data.get("config", {})
            if "resolution" in config:
                camera.resolution = tuple(config["resolution"])
            if "quality" in config:
                camera.quality = config["quality"]
            if "frame_rate" in config:
                camera.frame_rate = config["frame_rate"]

    # =========================================================================
    # CAN Streaming
    # =========================================================================

    async def start_can_stream(self, data: dict, ws):
        """Start streaming CAN bus data.

        Args (from dashboard):
            channel: CAN channel to stream (e.g. "can0"). Required.
            dbc_path: Optional path to DBC file for signal decoding.
            interval_ms: Poll interval in ms (default 10).
            store: Whether to persist to ClickHouse.
        """
        channel = data.get("channel")
        if not channel:
            self.on_status("No CAN channel specified")
            return

        # Find the matching detected adapter
        detected = None
        for c in self.can_adapters:
            if c.channel == channel:
                detected = c
                break

        if not detected:
            self.on_status(f"CAN channel not found: {channel}")
            return

        if not detected.is_up:
            self.on_status(f"CAN interface {channel} is down — run: plexus scan --setup")
            return

        # Stop existing stream for this channel
        if channel in self._active_can_streams:
            self._active_can_streams[channel].cancel()
            try:
                await self._active_can_streams[channel]
            except (asyncio.CancelledError, Exception):
                pass
            del self._active_can_streams[channel]
            self._cleanup_can_instance(channel)

        dbc_path = data.get("dbc_path")
        interval_ms = data.get("interval_ms", 10)
        store = data.get("store", False)

        if store:
            self._recording = True

        try:
            from plexus.adapters.can import CANAdapter

            adapter = CANAdapter(
                interface=detected.interface,
                channel=detected.channel,
                bitrate=detected.bitrate or 500000,
                dbc_path=dbc_path,
            )
            adapter.connect()
            self._can_instances[channel] = adapter
        except ImportError:
            self.on_status("python-can not installed. Install with: pip install plexus-agent[can]")
            return
        except Exception as e:
            self.on_status(f"CAN connect failed: {e}")
            return

        self.on_status(f"CAN {channel} streaming @ {interval_ms}ms")

        async def can_loop():
            loop = asyncio.get_event_loop()
            try:
                while channel in self._active_can_streams:
                    # poll() is blocking (0.1s timeout), run in thread pool
                    metrics = await loop.run_in_executor(None, adapter.poll)

                    if metrics:
                        points = [
                            {
                                "metric": m.name,
                                "value": m.value,
                                "timestamp": int((m.timestamp or time.time()) * 1000),
                                "tags": m.tags or {},
                            }
                            for m in metrics
                        ]
                        await ws.send(json.dumps({"type": "telemetry", "points": points}))

                        if self._recording and self.persist_fn:
                            asyncio.create_task(self.persist_fn(points))

                    await asyncio.sleep(interval_ms / 1000)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"CAN stream error on {channel}: {e}")
                self.on_status(f"CAN stream error: {e}")
            finally:
                self._cleanup_can_instance(channel)

        self._active_can_streams[channel] = asyncio.create_task(can_loop())

    async def stop_can_stream(self, data: dict):
        """Stop CAN streaming."""
        channel = data.get("channel")

        if channel == "*":
            for task in self._active_can_streams.values():
                task.cancel()
            self._active_can_streams.clear()
            for ch in list(self._can_instances):
                self._cleanup_can_instance(ch)
            self.on_status("Stopped all CAN streams")
        elif channel in self._active_can_streams:
            self._active_can_streams[channel].cancel()
            del self._active_can_streams[channel]
            self._cleanup_can_instance(channel)
            self.on_status(f"Stopped CAN stream: {channel}")

    def _cleanup_can_instance(self, channel: str):
        """Disconnect and remove a CAN adapter instance."""
        adapter = self._can_instances.pop(channel, None)
        if adapter:
            try:
                adapter.disconnect()
            except Exception as e:
                logger.debug(f"CAN disconnect error on {channel}: {e}")

    # =========================================================================
    # Cleanup
    # =========================================================================

    def cancel_all(self):
        """Cancel all active streams."""
        self._recording = False

        for task in self._active_streams.values():
            task.cancel()
        self._active_streams.clear()

        for task in self._active_camera_streams.values():
            task.cancel()
        self._active_camera_streams.clear()

        for task in self._active_can_streams.values():
            task.cancel()
        self._active_can_streams.clear()

        for ch in list(self._can_instances):
            self._cleanup_can_instance(ch)
