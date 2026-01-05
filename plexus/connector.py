"""
WebSocket connector for remote terminal access and sensor streaming.

Connects to the Plexus server and allows:
- Remote command execution
- Bidirectional sensor streaming (start/stop from dashboard)
- Real-time sensor configuration
"""

import asyncio
import json
import os
import platform
import shlex
import subprocess
import time
from typing import Optional, Callable, TYPE_CHECKING

import websockets
from websockets.exceptions import ConnectionClosed

from plexus.config import get_api_key, get_endpoint, get_device_id

if TYPE_CHECKING:
    from plexus.sensors.base import SensorHub


class PlexusConnector:
    """
    WebSocket client that connects to Plexus and executes commands remotely.
    Supports bidirectional streaming for sensor data.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        device_id: Optional[str] = None,
        on_status: Optional[Callable[[str], None]] = None,
        sensor_hub: Optional["SensorHub"] = None,
    ):
        self.api_key = api_key or get_api_key()
        self.endpoint = (endpoint or get_endpoint()).rstrip("/")
        self.device_id = device_id or get_device_id()
        self.on_status = on_status or (lambda x: None)
        self.sensor_hub = sensor_hub

        self._ws = None
        self._running = False
        self._current_process: Optional[subprocess.Popen] = None
        self._active_streams: dict[str, asyncio.Task] = {}

    def _get_ws_url(self) -> str:
        """Get WebSocket URL via discovery or env var."""
        # 1. Explicit env var takes priority
        ws_endpoint = os.environ.get("PLEXUS_WS_URL")
        if ws_endpoint:
            return f"{ws_endpoint.rstrip('/')}/ws/device"

        # 2. Try to discover from main API
        try:
            import httpx
            resp = httpx.get(f"{self.endpoint}/api/config", timeout=5.0)
            if resp.status_code == 200:
                config = resp.json()
                ws_url = config.get("ws_url")
                if ws_url:
                    return f"{ws_url.rstrip('/')}/ws/device"
        except Exception:
            pass  # Fall through to fallback

        # 3. Fallback: derive from endpoint (won't work on Vercel, but useful for local dev)
        url = self.endpoint.replace("https://", "wss://").replace("http://", "ws://")
        return f"{url}/ws/device"

    async def connect(self):
        """Connect to the Plexus server and listen for commands."""
        if not self.api_key:
            raise ValueError("No API key configured. Run 'plexus init' first.")

        ws_url = self._get_ws_url()
        self.on_status(f"Connecting to {ws_url}...")

        headers = [
            ("x-api-key", self.api_key),
            ("x-device-id", self.device_id),
            ("x-platform", platform.system()),
            ("x-python-version", platform.python_version()),
        ]

        self._running = True

        while self._running:
            try:
                async with websockets.connect(
                    ws_url,
                    additional_headers=headers,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    self.on_status("Connected! Waiting for commands...")

                    # Send initial handshake
                    await ws.send(json.dumps({
                        "type": "handshake",
                        "device_id": self.device_id,
                        "platform": platform.system(),
                        "cwd": os.getcwd(),
                    }))

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

            if msg_type == "execute":
                await self._execute_command(data)
            elif msg_type == "cancel":
                self._cancel_current()
            elif msg_type == "ping":
                await self._ws.send(json.dumps({"type": "pong"}))
            # Streaming control
            elif msg_type == "start_stream":
                await self._start_stream(data)
            elif msg_type == "stop_stream":
                await self._stop_stream(data)
            elif msg_type == "configure":
                await self._configure_sensor(data)

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
                    readings = self.sensor_hub.read(metrics if metrics else None)
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
                        "stream_id": stream_id,
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
            # Stop all streams
            for sid, task in self._active_streams.items():
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

    async def _execute_command(self, data: dict):
        """Execute a shell command and stream output back."""
        command = data.get("command", "")
        cmd_id = data.get("id", "unknown")

        if not command:
            return

        self.on_status(f"Executing: {command}")

        # Send start notification
        await self._ws.send(json.dumps({
            "type": "output",
            "id": cmd_id,
            "event": "start",
            "command": command,
        }))

        try:
            # Execute command safely without shell=True to prevent injection
            # Use shlex.split to properly parse the command into arguments
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

            # Stream output line by line
            for line in iter(self._current_process.stdout.readline, ""):
                if not self._running:
                    break
                await self._ws.send(json.dumps({
                    "type": "output",
                    "id": cmd_id,
                    "event": "data",
                    "data": line,
                }))

            # Wait for process to complete
            return_code = self._current_process.wait()

            # Send completion
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
        # Stop all active streams
        for task in self._active_streams.values():
            task.cancel()
        self._active_streams.clear()
        # Note: WebSocket will be closed when the connection context exits
        self._ws = None


def run_connector(
    api_key: Optional[str] = None,
    endpoint: Optional[str] = None,
    on_status: Optional[Callable[[str], None]] = None,
):
    """Run the connector (blocking)."""
    connector = PlexusConnector(
        api_key=api_key,
        endpoint=endpoint,
        on_status=on_status,
    )

    try:
        asyncio.run(connector.connect())
    except KeyboardInterrupt:
        # disconnect() just sets flags - actual cleanup happens in connect()
        connector.disconnect()
