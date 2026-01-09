"""
WebSocket connector for remote terminal access and sensor streaming.

Connects to the Plexus PartyKit server and allows:
- Remote command execution
- Bidirectional sensor streaming (start/stop from dashboard)
- Real-time sensor configuration
"""

import asyncio
import base64
import json
import os
import platform
import shlex
import subprocess
import time
from typing import Optional, Callable, TYPE_CHECKING

import websockets
from websockets.exceptions import ConnectionClosed

from plexus.config import get_api_key, get_device_token, get_endpoint, get_source_id, get_org_id

if TYPE_CHECKING:
    from plexus.sensors.base import SensorHub
    from plexus.cameras.base import CameraHub


class PlexusConnector:
    """
    WebSocket client that connects to Plexus PartyKit server.
    Supports bidirectional streaming for sensor data.
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
    ):
        # Device token is preferred over API key (new pairing flow)
        self.device_token = device_token or get_device_token()
        self.api_key = api_key or get_api_key()
        self.endpoint = (endpoint or get_endpoint()).rstrip("/")
        self.source_id = source_id or get_source_id()
        self.org_id = org_id or get_org_id() or "default"
        self.on_status = on_status or (lambda x: None)
        self.sensor_hub = sensor_hub
        self.camera_hub = camera_hub

        self._ws = None
        self._running = False
        self._authenticated = False
        self._current_process: Optional[subprocess.Popen] = None
        self._active_streams: dict[str, asyncio.Task] = {}
        self._active_camera_streams: dict[str, asyncio.Task] = {}
        self._current_session: Optional[dict] = None  # {session_id, session_name}

    def _get_ws_url(self) -> str:
        """Get PartyKit WebSocket URL."""
        # 1. Explicit env var takes priority
        ws_endpoint = os.environ.get("PLEXUS_WS_URL")
        if ws_endpoint:
            base = ws_endpoint.rstrip("/")
            # PartyKit URL format: ws://host/party/{room_id}
            if "/party/" in base:
                return base  # Already has room
            return f"{base}/party/{self.org_id}"

        # 2. Try to discover from main API
        try:
            import httpx
            resp = httpx.get(f"{self.endpoint}/api/config", timeout=5.0)
            if resp.status_code == 200:
                config = resp.json()
                ws_url = config.get("ws_url")
                if ws_url:
                    base = ws_url.rstrip("/")
                    return f"{base}/party/{self.org_id}"
        except Exception:
            pass

        # 3. Fallback: local PartyKit dev server
        return f"ws://127.0.0.1:1999/party/{self.org_id}"

    async def connect(self):
        """Connect to the Plexus PartyKit server and listen for commands."""
        if not self.device_token and not self.api_key:
            raise ValueError("No credentials configured. Run 'plexus pair' first.")

        ws_url = self._get_ws_url()
        self.on_status(f"Connecting to {ws_url}...")

        self._running = True

        while self._running:
            try:
                async with websockets.connect(
                    ws_url,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    self._authenticated = False

                    # Gather sensor info if available
                    sensors_info = []
                    if self.sensor_hub:
                        sensors_info = self.sensor_hub.get_info()

                    # Gather camera info if available
                    cameras_info = []
                    if self.camera_hub:
                        cameras_info = self.camera_hub.get_info()

                    # Build auth message - prefer device_token over api_key
                    auth_msg = {
                        "type": "device_auth",
                        "source_id": self.source_id,
                        "platform": platform.system(),
                        "sensors": sensors_info,
                        "cameras": cameras_info,
                    }
                    if self.device_token:
                        auth_msg["device_token"] = self.device_token
                    elif self.api_key:
                        auth_msg["api_key"] = self.api_key

                    # Authenticate with PartyKit
                    await ws.send(json.dumps(auth_msg))

                    self.on_status("Authenticating...")

                    # Listen for messages
                    async for message in ws:
                        await self._handle_message(message)

            except ConnectionClosed as e:
                self.on_status(f"Connection closed: {e.reason}")
                if self._running:
                    self.on_status("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)
            except Exception as e:
                self.on_status(f"Connection error: {e}")
                if self._running:
                    self.on_status("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")

            # Handle authentication response
            if msg_type == "authenticated":
                self._authenticated = True
                self.on_status(f"Connected! Source ID: {data.get('source_id')}")
                return

            if msg_type == "error":
                self.on_status(f"Error: {data.get('message')}")
                return

            # Only process commands if authenticated
            if not self._authenticated:
                return

            if msg_type == "execute":
                await self._execute_command(data)
            elif msg_type == "cancel":
                self._cancel_current()
            elif msg_type == "ping":
                await self._ws.send(json.dumps({"type": "pong"}))
            elif msg_type == "start_stream":
                await self._start_stream(data)
            elif msg_type == "stop_stream":
                await self._stop_stream(data)
            elif msg_type == "configure":
                await self._configure_sensor(data)
            elif msg_type == "start_session":
                await self._start_session(data)
            elif msg_type == "stop_session":
                await self._stop_session(data)
            elif msg_type == "start_camera":
                await self._start_camera_stream(data)
            elif msg_type == "stop_camera":
                await self._stop_camera_stream(data)
            elif msg_type == "configure_camera":
                await self._configure_camera(data)

        except json.JSONDecodeError:
            self.on_status(f"Invalid message: {message}")

    async def _start_stream(self, data: dict):
        """Start streaming sensor data to the server."""
        stream_id = data.get("id")
        metrics = data.get("metrics", [])
        interval_ms = data.get("interval_ms", 100)

        if not self.sensor_hub:
            self.on_status("No sensor hub configured - cannot stream")
            await self._ws.send(json.dumps({
                "type": "output",
                "id": stream_id,
                "event": "error",
                "error": "No sensors configured on this device",
            }))
            return

        self.on_status(f"Starting stream {stream_id}: {metrics} @ {interval_ms}ms")

        async def stream_loop():
            try:
                while stream_id in self._active_streams:
                    all_readings = self.sensor_hub.read_all()
                    if metrics:
                        readings = [r for r in all_readings if r.metric in metrics]
                    else:
                        readings = all_readings
                    points = [
                        {
                            "metric": r.metric,
                            "value": r.value,
                            "timestamp": int(time.time() * 1000),
                        }
                        for r in readings
                    ]
                    await self._ws.send(json.dumps({
                        "type": "telemetry",
                        "points": points,
                    }))
                    await asyncio.sleep(interval_ms / 1000)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.on_status(f"Stream error: {e}")

        task = asyncio.create_task(stream_loop())
        self._active_streams[stream_id] = task

    async def _stop_stream(self, data: dict):
        """Stop a running sensor stream."""
        stream_id = data.get("id")

        if stream_id in self._active_streams:
            self._active_streams[stream_id].cancel()
            del self._active_streams[stream_id]
            self.on_status(f"Stopped stream {stream_id}")
        elif stream_id == "*":
            for task in self._active_streams.values():
                task.cancel()
            self._active_streams.clear()
            self.on_status("Stopped all streams")

    async def _configure_sensor(self, data: dict):
        """Configure a sensor on this device."""
        sensor_name = data.get("sensor")
        config = data.get("config", {})

        if not self.sensor_hub:
            return

        sensor = self.sensor_hub.get_sensor(sensor_name)
        if sensor and hasattr(sensor, "configure"):
            try:
                sensor.configure(**config)
                self.on_status(f"Configured {sensor_name}: {config}")
            except Exception as e:
                self.on_status(f"Failed to configure {sensor_name}: {e}")

    async def _start_session(self, data: dict):
        """Start a recording session - streams data with session_id tag."""
        session_id = data.get("session_id")
        session_name = data.get("session_name", "Untitled")
        metrics = data.get("metrics", [])
        interval_ms = data.get("interval_ms", 100)

        if not self.sensor_hub:
            self.on_status("No sensor hub configured - cannot record session")
            await self._ws.send(json.dumps({
                "type": "output",
                "id": session_id,
                "event": "error",
                "error": "No sensors configured on this device",
            }))
            return

        # Store current session info
        self._current_session = {
            "session_id": session_id,
            "session_name": session_name,
        }

        self.on_status(f"Starting session '{session_name}' ({session_id})")

        async def session_stream_loop():
            try:
                while session_id in self._active_streams and self._current_session:
                    all_readings = self.sensor_hub.read_all()
                    if metrics:
                        readings = [r for r in all_readings if r.metric in metrics]
                    else:
                        readings = all_readings
                    points = [
                        {
                            "metric": r.metric,
                            "value": r.value,
                            "timestamp": int(time.time() * 1000),
                            "tags": {"session_id": session_id},  # Tag with session
                        }
                        for r in readings
                    ]
                    await self._ws.send(json.dumps({
                        "type": "telemetry",
                        "points": points,
                        "session_id": session_id,  # Include session_id at message level too
                    }))
                    await asyncio.sleep(interval_ms / 1000)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.on_status(f"Session stream error: {e}")

        task = asyncio.create_task(session_stream_loop())
        self._active_streams[session_id] = task

        # Notify that session has started
        await self._ws.send(json.dumps({
            "type": "session_started",
            "session_id": session_id,
            "session_name": session_name,
        }))

    async def _stop_session(self, data: dict):
        """Stop a recording session."""
        session_id = data.get("session_id")

        if session_id in self._active_streams:
            self._active_streams[session_id].cancel()
            del self._active_streams[session_id]
            self.on_status(f"Stopped session {session_id}")

        if self._current_session and self._current_session.get("session_id") == session_id:
            self._current_session = None

        # Notify that session has stopped
        await self._ws.send(json.dumps({
            "type": "session_stopped",
            "session_id": session_id,
        }))

    async def _start_camera_stream(self, data: dict):
        """Start streaming video frames from a camera."""
        camera_id = data.get("camera_id")
        frame_rate = data.get("frame_rate", 10)
        resolution = data.get("resolution")
        quality = data.get("quality")

        if not self.camera_hub:
            self.on_status("No camera hub configured - cannot stream video")
            await self._ws.send(json.dumps({
                "type": "error",
                "message": "No cameras configured on this device",
            }))
            return

        camera = self.camera_hub.get_camera(camera_id)
        if not camera:
            self.on_status(f"Camera not found: {camera_id}")
            await self._ws.send(json.dumps({
                "type": "error",
                "message": f"Camera not found: {camera_id}",
            }))
            return

        # Apply configuration if provided
        if resolution:
            camera.resolution = tuple(resolution)
        if quality:
            camera.quality = quality
        camera.frame_rate = frame_rate

        self.on_status(f"Starting camera stream: {camera_id} @ {frame_rate}fps")

        async def camera_stream_loop():
            interval = 1.0 / frame_rate
            try:
                # Initialize camera
                camera.setup()

                while camera_id in self._active_camera_streams:
                    frame = camera.capture()
                    if frame:
                        # Base64 encode the JPEG data
                        frame_b64 = base64.b64encode(frame.data).decode('ascii')

                        await self._ws.send(json.dumps({
                            "type": "video_frame",
                            "camera_id": camera_id,
                            "frame": frame_b64,
                            "width": frame.width,
                            "height": frame.height,
                            "timestamp": int(frame.timestamp * 1000),
                            "tags": frame.tags,
                        }))
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.on_status(f"Camera stream error: {e}")
            finally:
                camera.cleanup()

        task = asyncio.create_task(camera_stream_loop())
        self._active_camera_streams[camera_id] = task

    async def _stop_camera_stream(self, data: dict):
        """Stop a running camera stream."""
        camera_id = data.get("camera_id")

        if camera_id == "*":
            # Stop all camera streams
            for task in self._active_camera_streams.values():
                task.cancel()
            self._active_camera_streams.clear()
            self.on_status("Stopped all camera streams")
        elif camera_id in self._active_camera_streams:
            self._active_camera_streams[camera_id].cancel()
            del self._active_camera_streams[camera_id]
            self.on_status(f"Stopped camera stream: {camera_id}")

    async def _configure_camera(self, data: dict):
        """Configure a camera on this device."""
        camera_id = data.get("camera_id")
        config = data.get("config", {})

        if not self.camera_hub:
            return

        camera = self.camera_hub.get_camera(camera_id)
        if camera:
            try:
                if "resolution" in config:
                    camera.resolution = tuple(config["resolution"])
                if "quality" in config:
                    camera.quality = config["quality"]
                if "frame_rate" in config:
                    camera.frame_rate = config["frame_rate"]
                self.on_status(f"Configured camera {camera_id}: {config}")
            except Exception as e:
                self.on_status(f"Failed to configure camera {camera_id}: {e}")

    async def _execute_command(self, data: dict):
        """Execute a shell command and stream output back."""
        command = data.get("command", "")
        cmd_id = data.get("id", "unknown")

        if not command:
            return

        self.on_status(f"Executing: {command}")

        await self._ws.send(json.dumps({
            "type": "output",
            "id": cmd_id,
            "event": "start",
            "command": command,
        }))

        try:
            try:
                args = shlex.split(command)
            except ValueError as e:
                await self._ws.send(json.dumps({
                    "type": "output",
                    "id": cmd_id,
                    "event": "error",
                    "error": f"Invalid command syntax: {e}",
                }))
                return

            self._current_process = subprocess.Popen(
                args,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=os.getcwd(),
            )

            for line in iter(self._current_process.stdout.readline, ""):
                if not self._running:
                    break
                await self._ws.send(json.dumps({
                    "type": "output",
                    "id": cmd_id,
                    "event": "data",
                    "data": line,
                }))

            return_code = self._current_process.wait()

            await self._ws.send(json.dumps({
                "type": "output",
                "id": cmd_id,
                "event": "exit",
                "code": return_code,
            }))

        except Exception as e:
            await self._ws.send(json.dumps({
                "type": "output",
                "id": cmd_id,
                "event": "error",
                "error": str(e),
            }))

        finally:
            self._current_process = None

    def _cancel_current(self):
        """Cancel the currently running command."""
        if self._current_process:
            self._current_process.terminate()
            self.on_status("Command cancelled")

    def disconnect(self):
        """Disconnect from the server."""
        self._running = False
        self._cancel_current()
        for task in self._active_streams.values():
            task.cancel()
        self._active_streams.clear()
        for task in self._active_camera_streams.values():
            task.cancel()
        self._active_camera_streams.clear()
        self._ws = None


def run_connector(
    api_key: Optional[str] = None,
    device_token: Optional[str] = None,
    endpoint: Optional[str] = None,
    on_status: Optional[Callable[[str], None]] = None,
    sensor_hub: Optional["SensorHub"] = None,
    camera_hub: Optional["CameraHub"] = None,
):
    """Run the connector (blocking)."""
    connector = PlexusConnector(
        api_key=api_key,
        device_token=device_token,
        endpoint=endpoint,
        on_status=on_status,
        sensor_hub=sensor_hub,
        camera_hub=camera_hub,
    )

    try:
        asyncio.run(connector.connect())
    except KeyboardInterrupt:
        connector.disconnect()
