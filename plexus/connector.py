"""
Plexus Device Connector

Connects devices to Plexus via WebSocket for real-time streaming and control.

Data Flow:
┌─────────────────────────────────────────────────────────────────┐
│  Device (this agent)                                            │
│       │                                                         │
│       ├──► WebSocket (PartyKit) ──► Dashboard (real-time view)  │
│       │                                                         │
│       └──► HTTP (/api/ingest) ──► ClickHouse (storage)          │
│            (only when store=True)                               │
└─────────────────────────────────────────────────────────────────┘

User Controls (from Dashboard UI):
- "View Live" → store=False → WebSocket only (free, no storage)
- "Record"    → store=True  → WebSocket + HTTP (uses storage quota)

Authentication:
- Device token (plxd_*) from pairing works for both WebSocket and HTTP
- API key (plx_*) also works if user prefers
"""

import asyncio
import base64
import json
import os
import platform
import shlex
import subprocess
import time
from typing import Optional, Callable, List, Dict, Any, TYPE_CHECKING

import websockets
from websockets.exceptions import ConnectionClosed

from plexus.config import get_api_key, get_device_token, get_endpoint, get_source_id, get_org_id

if TYPE_CHECKING:
    from plexus.sensors.base import SensorHub
    from plexus.cameras.base import CameraHub
    from plexus.adapters.can_detect import DetectedCAN


class PlexusConnector:
    """
    WebSocket client that connects to Plexus for real-time data streaming.

    Supports:
    - Real-time sensor streaming (controlled from dashboard)
    - Camera streaming
    - Remote command execution
    - Optional data persistence (when recording)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        device_token: Optional[str] = None,
        endpoint: Optional[str] = None,
        source_id: Optional[str] = None,
        org_id: Optional[str] = None,
        on_status: Optional[Callable[[str], None]] = None,
        sensor_hub: Optional["SensorHub"] = None,
        camera_hub: Optional["CameraHub"] = None,
        can_adapters: Optional[List["DetectedCAN"]] = None,
    ):
        # Device token is preferred (from pairing flow)
        self.device_token = device_token or get_device_token()
        self.api_key = api_key or get_api_key()
        self.endpoint = (endpoint or get_endpoint()).rstrip("/")
        self.source_id = source_id or get_source_id()
        self.org_id = org_id or get_org_id() or "default"
        self.on_status = on_status or (lambda x: None)
        self.sensor_hub = sensor_hub
        self.camera_hub = camera_hub
        self.can_adapters = can_adapters

        self._ws = None
        self._running = False
        self._authenticated = False
        self._current_process: Optional[subprocess.Popen] = None
        self._active_streams: dict[str, asyncio.Task] = {}
        self._active_camera_streams: dict[str, asyncio.Task] = {}

        # Recording state - when True, data is persisted to ClickHouse
        self._recording: bool = False
        self._http_session: Optional[Any] = None

    # =========================================================================
    # Connection URLs
    # =========================================================================

    def _get_ws_url(self) -> str:
        """Get PartyKit WebSocket URL."""
        # 1. Explicit env var
        ws_endpoint = os.environ.get("PLEXUS_WS_URL")
        if ws_endpoint:
            base = ws_endpoint.rstrip("/")
            if "/party/" in base:
                return base
            return f"{base}/party/{self.org_id}"

        # 2. Discover from API
        try:
            import requests
            resp = requests.get(f"{self.endpoint}/api/config", timeout=5.0)
            if resp.status_code == 200:
                config = resp.json()
                ws_url = config.get("ws_url")
                if ws_url:
                    return f"{ws_url.rstrip('/')}/party/{self.org_id}"
        except Exception:
            pass

        # 3. Fallback: local dev server
        return f"ws://127.0.0.1:1999/party/{self.org_id}"

    # =========================================================================
    # HTTP Persistence (for recording)
    # =========================================================================

    def _get_http_session(self):
        """Get HTTP session for data persistence."""
        if self._http_session is None:
            import requests
            self._http_session = requests.Session()
            # Device token or API key - both work for /api/ingest
            auth_token = self.api_key or self.device_token
            if auth_token:
                self._http_session.headers["x-api-key"] = auth_token
            self._http_session.headers["Content-Type"] = "application/json"
            self._http_session.headers["User-Agent"] = "plexus-agent/0.1.0"
        return self._http_session

    def _persist_points(self, points: List[Dict[str, Any]]) -> bool:
        """
        Persist data points to ClickHouse via HTTP.
        Called when recording is enabled. Runs in thread pool.
        """
        if not self.api_key and not self.device_token:
            return False

        try:
            formatted = [
                {
                    "metric": p["metric"],
                    "value": p["value"],
                    "source_id": self.source_id,
                    "timestamp": p.get("timestamp", int(time.time() * 1000)) / 1000,
                    "tags": p.get("tags", {}),
                }
                for p in points
            ]

            response = self._get_http_session().post(
                f"{self.endpoint}/api/ingest",
                json={"points": formatted},
                timeout=5.0,
            )
            return response.status_code < 400
        except Exception:
            return False

    async def _persist_async(self, points: List[Dict[str, Any]]):
        """Async wrapper - runs HTTP in thread pool."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._persist_points, points)

    # =========================================================================
    # WebSocket Connection
    # =========================================================================

    async def connect(self):
        """Connect to Plexus and listen for commands."""
        if not self.device_token and not self.api_key:
            raise ValueError("No credentials. Run 'plexus pair' first.")

        ws_url = self._get_ws_url()
        self.on_status(f"Connecting to {ws_url}...")

        self._running = True

        while self._running:
            try:
                async with websockets.connect(ws_url, ping_interval=30, ping_timeout=10) as ws:
                    self._ws = ws
                    self._authenticated = False

                    # Build auth message
                    auth_msg = {
                        "type": "device_auth",
                        "source_id": self.source_id,
                        "platform": platform.system(),
                        "sensors": self.sensor_hub.get_info() if self.sensor_hub else [],
                        "cameras": self.camera_hub.get_info() if self.camera_hub else [],
                        "can": [
                            {"interface": c.interface, "channel": c.channel, "bitrate": c.bitrate}
                            for c in self.can_adapters
                        ] if self.can_adapters else [],
                    }
                    if self.device_token:
                        auth_msg["device_token"] = self.device_token
                    elif self.api_key:
                        auth_msg["api_key"] = self.api_key

                    await ws.send(json.dumps(auth_msg))
                    self.on_status("Authenticating...")

                    async for message in ws:
                        await self._handle_message(message)

            except ConnectionClosed as e:
                self.on_status(f"Disconnected: {e.reason}")
                if self._running:
                    self.on_status("Reconnecting in 5s...")
                    await asyncio.sleep(5)
            except Exception as e:
                self.on_status(f"Error: {e}")
                if self._running:
                    self.on_status("Reconnecting in 5s...")
                    await asyncio.sleep(5)

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")

            if msg_type == "authenticated":
                self._authenticated = True
                self.on_status(f"Connected as {data.get('source_id')}")
                return

            if msg_type == "error":
                self.on_status(f"Error: {data.get('message')}")
                return

            if not self._authenticated:
                return

            # Command handlers
            handlers = {
                "start_stream": self._start_stream,
                "stop_stream": self._stop_stream,
                "start_camera": self._start_camera,
                "stop_camera": self._stop_camera,
                "execute": self._execute_command,
                "cancel": lambda _: self._cancel_command(),
                "configure": self._configure_sensor,
                "configure_camera": self._configure_camera,
                "ping": lambda _: self._ws.send(json.dumps({"type": "pong"})),
            }

            handler = handlers.get(msg_type)
            if handler:
                result = handler(data)
                if asyncio.iscoroutine(result):
                    await result

        except json.JSONDecodeError:
            self.on_status(f"Invalid message: {message}")

    # =========================================================================
    # Sensor Streaming
    # =========================================================================

    async def _start_stream(self, data: dict):
        """
        Start streaming sensor data.

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
                    await self._ws.send(json.dumps({"type": "telemetry", "points": points}))

                    # If recording, also persist via HTTP
                    if self._recording and points:
                        asyncio.create_task(self._persist_async(points))

                    await asyncio.sleep(interval_ms / 1000)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.on_status(f"Stream error: {e}")

        self._active_streams[stream_id] = asyncio.create_task(stream_loop())

    async def _stop_stream(self, data: dict):
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
            self.on_status(f"Stopped stream")

        self._recording = False

    async def _configure_sensor(self, data: dict):
        """Configure a sensor."""
        if not self.sensor_hub:
            return

        sensor = self.sensor_hub.get_sensor(data.get("sensor"))
        if sensor and hasattr(sensor, "configure"):
            try:
                sensor.configure(**data.get("config", {}))
                self.on_status(f"Configured {data.get('sensor')}")
            except Exception as e:
                self.on_status(f"Config failed: {e}")

    # =========================================================================
    # Camera Streaming
    # =========================================================================

    async def _start_camera(self, data: dict):
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
                        await self._ws.send(json.dumps({
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

    async def _stop_camera(self, data: dict):
        """Stop camera streaming."""
        camera_id = data.get("camera_id")

        if camera_id == "*":
            for task in self._active_camera_streams.values():
                task.cancel()
            self._active_camera_streams.clear()
        elif camera_id in self._active_camera_streams:
            self._active_camera_streams[camera_id].cancel()
            del self._active_camera_streams[camera_id]

        self.on_status(f"Stopped camera")

    async def _configure_camera(self, data: dict):
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
    # Remote Commands
    # =========================================================================

    async def _execute_command(self, data: dict):
        """Execute shell command and stream output."""
        command = data.get("command", "")
        cmd_id = data.get("id", "cmd")

        if not command:
            return

        self.on_status(f"Running: {command}")

        await self._ws.send(json.dumps({
            "type": "output", "id": cmd_id, "event": "start", "command": command
        }))

        try:
            args = shlex.split(command)
            self._current_process = subprocess.Popen(
                args, shell=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=os.getcwd()
            )

            for line in iter(self._current_process.stdout.readline, ""):
                if not self._running:
                    break
                await self._ws.send(json.dumps({
                    "type": "output", "id": cmd_id, "event": "data", "data": line
                }))

            code = self._current_process.wait()
            await self._ws.send(json.dumps({
                "type": "output", "id": cmd_id, "event": "exit", "code": code
            }))

        except Exception as e:
            await self._ws.send(json.dumps({
                "type": "output", "id": cmd_id, "event": "error", "error": str(e)
            }))
        finally:
            self._current_process = None

    def _cancel_command(self):
        """Cancel running command."""
        if self._current_process:
            self._current_process.terminate()
            self.on_status("Cancelled")

    # =========================================================================
    # Cleanup
    # =========================================================================

    def disconnect(self):
        """Disconnect and cleanup."""
        self._running = False
        self._recording = False
        self._cancel_command()

        for task in self._active_streams.values():
            task.cancel()
        self._active_streams.clear()

        for task in self._active_camera_streams.values():
            task.cancel()
        self._active_camera_streams.clear()

        self._ws = None

        if self._http_session:
            self._http_session.close()
            self._http_session = None


def run_connector(
    api_key: Optional[str] = None,
    device_token: Optional[str] = None,
    endpoint: Optional[str] = None,
    on_status: Optional[Callable[[str], None]] = None,
    sensor_hub: Optional["SensorHub"] = None,
    camera_hub: Optional["CameraHub"] = None,
    can_adapters: Optional[List["DetectedCAN"]] = None,
):
    """Run the connector (blocking)."""
    connector = PlexusConnector(
        api_key=api_key,
        device_token=device_token,
        endpoint=endpoint,
        on_status=on_status,
        sensor_hub=sensor_hub,
        camera_hub=camera_hub,
        can_adapters=can_adapters,
    )

    try:
        asyncio.run(connector.connect())
    except KeyboardInterrupt:
        connector.disconnect()
