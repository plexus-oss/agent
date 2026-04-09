"""
Stream management for Plexus devices.

Handles real-time sensor and camera streaming over WebSocket,
with optional HTTP persistence for recording.

Store-and-forward: when a buffer is provided, telemetry that fails to send
over WebSocket (e.g. during disconnection) is written to the buffer instead
of being dropped. The connector drains the buffer on reconnect via HTTP.
"""

import asyncio
import base64
import json
import logging
import threading
import time
import uuid
from io import BytesIO
from typing import Optional, Callable, List, Dict, Any, TYPE_CHECKING

from websockets.exceptions import ConnectionClosed

if TYPE_CHECKING:
    from plexus.sensors.base import SensorHub
    from plexus.cameras.base import CameraHub
    from plexus.adapters.can_detect import DetectedCAN
    from plexus.adapters.mavlink_detect import DetectedMAVLink
    from plexus.buffer import BufferBackend

logger = logging.getLogger(__name__)


def _upload_frame_async(endpoint: str, api_key: str, frame, source_id: str, run_id: str = None):
    """Upload a single frame to /api/frames in a background thread."""
    def _do_upload():
        try:
            import requests
            files = {"frame": ("frame.jpg", BytesIO(frame.data), "image/jpeg")}
            data = {
                "source_id": source_id,
                "camera_id": frame.camera_id,
                "timestamp": str(int(frame.timestamp * 1000)),
            }
            if run_id:
                data["run_id"] = run_id
            if frame.tags:
                data["tags"] = json.dumps(frame.tags)

            requests.post(
                f"{endpoint}/api/frames",
                files=files,
                data=data,
                headers={"x-api-key": api_key},
                timeout=10,
            )
        except Exception as e:
            logger.debug(f"Frame upload error: {e}")

    t = threading.Thread(target=_do_upload, daemon=True)
    t.start()


class StreamManager:
    """Manages sensor and camera streams.

    All telemetry is relayed over WebSocket. Persistence is a gateway/
    consumer concern — the agent has no per-stream "record" state.

    Args:
        sensor_hub: SensorHub instance for reading sensors.
        camera_hub: CameraHub instance for capturing frames.
        on_status: Callback for status messages.
        buffer: Optional buffer backend for store-and-forward. When provided,
                telemetry that fails to send over WebSocket is buffered locally
                instead of being lost.
    """

    def __init__(
        self,
        sensor_hub: Optional["SensorHub"] = None,
        camera_hub: Optional["CameraHub"] = None,
        can_adapters: Optional[List["DetectedCAN"]] = None,
        mavlink_connections: Optional[List["DetectedMAVLink"]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        error_report_fn: Optional[Callable] = None,
        buffer: Optional["BufferBackend"] = None,
        store_frames: bool = False,
        endpoint: str = "",
        api_key: str = "",
        source_id: str = "",
        run_id_fn: Optional[Callable[[], Optional[str]]] = None,
        store_frames_fn: Optional[Callable[[], bool]] = None,
    ):
        self.sensor_hub = sensor_hub
        self.camera_hub = camera_hub
        self.can_adapters = can_adapters or []
        self.mavlink_connections = mavlink_connections or []
        self.on_status = on_status or (lambda x: None)
        self.error_report_fn = error_report_fn
        self.buffer = buffer
        self.store_frames = store_frames
        self._endpoint = endpoint
        self._api_key = api_key
        self._source_id = source_id
        self._run_id_fn = run_id_fn
        self._store_frames_fn = store_frames_fn

        self._active_streams: Dict[str, asyncio.Task] = {}
        self._active_camera_streams: Dict[str, asyncio.Task] = {}
        self._active_can_streams: Dict[str, asyncio.Task] = {}
        self._can_instances: Dict[str, Any] = {}  # channel -> CANAdapter
        self._active_mavlink_streams: Dict[str, asyncio.Task] = {}
        self._mavlink_instances: Dict[str, Any] = {}  # conn_string -> MAVLinkAdapter

    # =========================================================================
    # WebSocket Send with Buffer Fallback
    # =========================================================================

    async def _send_or_buffer(self, ws, points: List[Dict[str, Any]]) -> bool:
        """Send telemetry over WebSocket, falling back to buffer on failure.

        Wraps points in the pipeline envelope with version, trace_id, and
        source identifiers for gateway routing.

        Returns True if sent over WebSocket, False if buffered.
        """
        try:
            envelope = {
                "type": "telemetry",
                "v": 1,
                "trace_id": uuid.uuid4().hex,
                "source_id": self._source_id,
                "points": points,
                "ingested_at": int(time.time() * 1000),
            }
            await ws.send(json.dumps(envelope))
            return True
        except (ConnectionClosed, ConnectionError, OSError):
            if self.buffer and points:
                self.buffer.add(points)
                logger.debug("WebSocket down, buffered %d points", len(points))
            return False

    # =========================================================================
    # Sensor Streaming
    # =========================================================================

    async def start_stream(self, data: dict, ws):
        """Start streaming sensor data.

        Each sensor is sampled at its own declared `sample_rate` (Hz).
        The loop ticks at the fastest sensor's rate and reads each
        sensor only when its interval is up. Sensors with different
        rates coexist correctly — a 100Hz IMU and a 1Hz BME280 in the
        same hub each run at their natural cadence.

        Args (from dashboard):
            metrics: list - Which metrics to stream (empty = all)
            interval_ms: int - Optional global rate cap in ms. When set,
                no sensor samples faster than 1000/interval_ms Hz. When
                omitted, sensors use their declared rates.
        """
        stream_id = data.get("id", f"stream_{int(time.time())}")
        metrics = data.get("metrics", [])
        interval_ms = data.get("interval_ms")  # optional global cap

        if not self.sensor_hub:
            self.on_status("No sensors configured")
            return

        sensors = list(self.sensor_hub.sensors)
        if not sensors:
            self.on_status("No sensors available")
            return

        # Apply optional global rate cap from the dashboard.
        # Each sensor samples at min(sensor.sample_rate, cap_hz).
        cap_hz = None
        if interval_ms and interval_ms > 0:
            cap_hz = 1000.0 / interval_ms

        def effective_rate(s) -> float:
            rate = getattr(s, "sample_rate", 10.0) or 10.0
            if cap_hz is not None:
                rate = min(rate, cap_hz)
            return max(rate, 0.01)  # floor at 0.01Hz (one read per 100s)

        # Loop tick = fastest sensor's interval
        max_rate = max(effective_rate(s) for s in sensors)
        tick = 1.0 / max_rate

        metric_count = len(metrics) if metrics else "all"
        cap_note = f" cap={cap_hz:.1f}Hz" if cap_hz is not None else ""
        self.on_status(f"Streaming {metric_count} metrics (tick {tick*1000:.0f}ms{cap_note})")

        async def stream_loop():
            # Parse metric filters (strip source_id prefix if present)
            filters = set()
            for m in metrics:
                filters.add(m.split(":", 1)[-1] if ":" in m else m)

            # Per-sensor last-read timestamps
            last_read = {id(s): 0.0 for s in sensors}

            try:
                while stream_id in self._active_streams:
                    now = time.time()
                    readings = []

                    for sensor in sensors:
                        if getattr(sensor, "_disabled", False):
                            continue
                        interval = 1.0 / effective_rate(sensor)
                        if now - last_read[id(sensor)] >= interval:
                            try:
                                readings.extend(sensor.read())
                            except Exception as e:
                                logger.debug(f"Sensor read failed: {sensor.name}: {e}")
                                continue
                            last_read[id(sensor)] = now

                    if filters:
                        readings = [r for r in readings if r.metric in filters]

                    if readings:
                        points = [
                            {
                                "class": "metric",
                                "metric": r.metric,
                                "value": r.value,
                                "timestamp": int(time.time() * 1000),
                            }
                            for r in readings
                        ]
                        await self._send_or_buffer(ws, points)

                    await asyncio.sleep(tick)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.on_status(f"Stream error: {e}")
                if self.error_report_fn:
                    await self.error_report_fn(
                        "stream.sensor", str(e), "error"
                    )

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

        # Allow dashboard to enable frame persistence per-stream
        if data.get("store_frames"):
            self.store_frames = True

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
                            "v": 1,
                            "trace_id": uuid.uuid4().hex,
                            "source_id": self._source_id,
                            "camera_id": camera_id,
                            "frame": base64.b64encode(frame.data).decode('ascii'),
                            "width": frame.width,
                            "height": frame.height,
                            "timestamp": int(frame.timestamp * 1000),
                        }))

                        # Persist frame to storage if recording
                        should_store = (
                            self._store_frames_fn() if self._store_frames_fn
                            else self.store_frames
                        )
                        if should_store and frame.data:
                            try:
                                run_id = (
                                    self._run_id_fn() if self._run_id_fn
                                    else None
                                )
                                _upload_frame_async(
                                    endpoint=self._endpoint,
                                    api_key=self._api_key,
                                    frame=frame,
                                    source_id=self._source_id,
                                    run_id=run_id,
                                )
                            except Exception as e:
                                logger.debug(f"Frame upload failed: {e}")

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
            self.on_status(f"CAN interface {channel} is down — configure with: sudo ip link set {channel} up type can bitrate 500000")
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
                                "class": m.data_class,
                                "metric": m.name,
                                "value": m.value,
                                "timestamp": int((m.timestamp or time.time()) * 1000),
                                "tags": m.tags or {},
                            }
                            for m in metrics
                        ]
                        await self._send_or_buffer(ws, points)

                    await asyncio.sleep(interval_ms / 1000)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"CAN stream error on {channel}: {e}")
                self.on_status(f"CAN stream error: {e}")
                if self.error_report_fn:
                    await self.error_report_fn(
                        f"stream.can.{channel}", str(e), "error"
                    )
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
    # MAVLink Streaming
    # =========================================================================

    async def start_mavlink_stream(self, data: dict, ws):
        """Start streaming MAVLink telemetry.

        Args (from dashboard):
            connection_string: MAVLink connection (e.g. "udpin:0.0.0.0:14550"). Required.
            interval_ms: Poll interval in ms (default 10).
            include_messages: Optional list of message types to include.
            store: Whether to persist to ClickHouse.
        """
        conn_str = data.get("connection_string")
        if not conn_str:
            self.on_status("No MAVLink connection string specified")
            return

        # Stop existing stream for this connection
        if conn_str in self._active_mavlink_streams:
            self._active_mavlink_streams[conn_str].cancel()
            try:
                await self._active_mavlink_streams[conn_str]
            except (asyncio.CancelledError, Exception):
                pass
            del self._active_mavlink_streams[conn_str]
            self._cleanup_mavlink_instance(conn_str)

        interval_ms = data.get("interval_ms", 10)
        include_messages = data.get("include_messages")

        try:
            from plexus.adapters.mavlink import MAVLinkAdapter

            adapter = MAVLinkAdapter(
                connection_string=conn_str,
                include_messages=include_messages,
            )
            adapter.connect()
            self._mavlink_instances[conn_str] = adapter
        except ImportError:
            self.on_status("pymavlink not installed. Install with: pip install plexus-agent[mavlink]")
            return
        except Exception as e:
            self.on_status(f"MAVLink connect failed: {e}")
            return

        self.on_status(f"MAVLink {conn_str} streaming @ {interval_ms}ms")

        async def mavlink_loop():
            loop = asyncio.get_event_loop()
            try:
                while conn_str in self._active_mavlink_streams:
                    metrics = await loop.run_in_executor(None, adapter.poll)

                    if metrics:
                        points = [
                            {
                                "class": m.data_class,
                                "metric": m.name,
                                "value": m.value,
                                "timestamp": int((m.timestamp or time.time()) * 1000),
                                "tags": m.tags or {},
                            }
                            for m in metrics
                        ]
                        await self._send_or_buffer(ws, points)

                    await asyncio.sleep(interval_ms / 1000)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"MAVLink stream error on {conn_str}: {e}")
                self.on_status(f"MAVLink stream error: {e}")
                if self.error_report_fn:
                    await self.error_report_fn(
                        "stream.mavlink", str(e), "error"
                    )
            finally:
                self._cleanup_mavlink_instance(conn_str)

        self._active_mavlink_streams[conn_str] = asyncio.create_task(mavlink_loop())

    async def stop_mavlink_stream(self, data: dict):
        """Stop MAVLink streaming."""
        conn_str = data.get("connection_string")

        if conn_str == "*":
            for task in self._active_mavlink_streams.values():
                task.cancel()
            self._active_mavlink_streams.clear()
            for cs in list(self._mavlink_instances):
                self._cleanup_mavlink_instance(cs)
            self.on_status("Stopped all MAVLink streams")
        elif conn_str in self._active_mavlink_streams:
            self._active_mavlink_streams[conn_str].cancel()
            del self._active_mavlink_streams[conn_str]
            self._cleanup_mavlink_instance(conn_str)
            self.on_status(f"Stopped MAVLink stream: {conn_str}")

    def _cleanup_mavlink_instance(self, conn_str: str):
        """Disconnect and remove a MAVLink adapter instance."""
        adapter = self._mavlink_instances.pop(conn_str, None)
        if adapter:
            try:
                adapter.disconnect()
            except Exception as e:
                logger.debug(f"MAVLink disconnect error on {conn_str}: {e}")

    # =========================================================================
    # Cleanup
    # =========================================================================

    def cancel_all(self):
        """Cancel all active streams."""
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

        for task in self._active_mavlink_streams.values():
            task.cancel()
        self._active_mavlink_streams.clear()

        for cs in list(self._mavlink_instances):
            self._cleanup_mavlink_instance(cs)
