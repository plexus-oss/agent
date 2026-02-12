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
import json
import logging
import os
import platform
import time
from typing import Optional, Callable, List, Dict, Any, TYPE_CHECKING

import websockets
from websockets.exceptions import ConnectionClosed

from plexus.config import get_api_key, get_device_token, get_endpoint, get_source_id, get_org_id, get_command_allowlist, get_command_denylist
from plexus.commands import CommandExecutor, DEFAULT_COMMAND_DENYLIST
from plexus.streaming import StreamManager

if TYPE_CHECKING:
    from plexus.sensors.base import SensorHub
    from plexus.cameras.base import CameraHub
    from plexus.adapters.can_detect import DetectedCAN

logger = logging.getLogger(__name__)


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
        command_allowlist: Optional[List[str]] = None,
        command_denylist: Optional[List[str]] = None,
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
        self._http_session: Optional[Any] = None

        # Delegates
        allowlist = command_allowlist or get_command_allowlist()
        denylist = (
            command_denylist if command_denylist is not None
            else (get_command_denylist() or DEFAULT_COMMAND_DENYLIST)
        )
        self._commands = CommandExecutor(
            allowlist=allowlist, denylist=denylist, on_status=self.on_status,
        )
        self._streams = StreamManager(
            sensor_hub=sensor_hub,
            camera_hub=camera_hub,
            can_adapters=can_adapters,
            on_status=self.on_status,
            persist_fn=self._persist_async,
        )

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
        """Persist data points to ClickHouse via HTTP."""
        if not self.api_key and not self.device_token:
            return False

        try:
            formatted = [
                {
                    "metric": p["metric"],
                    "value": p["value"],
                    "source_id": self.source_id,
                    "timestamp": p.get("timestamp", int(time.time() * 1000)),
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
        except Exception as e:
            logger.debug(f"Persist failed: {e}")
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

            # Command handlers - delegate to focused modules
            handlers = {
                "start_stream": lambda d: self._streams.start_stream(d, self._ws),
                "stop_stream": lambda d: self._streams.stop_stream(d),
                "start_camera": lambda d: self._streams.start_camera(d, self._ws),
                "stop_camera": lambda d: self._streams.stop_camera(d),
                "start_can": lambda d: self._streams.start_can_stream(d, self._ws),
                "stop_can": lambda d: self._streams.stop_can_stream(d),
                "execute": lambda d: self._commands.execute(d, self._ws, lambda: self._running),
                "cancel": lambda _: self._commands.cancel(),
                "configure": lambda d: self._streams.configure_sensor(d),
                "configure_camera": lambda d: self._streams.configure_camera(d),
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
    # Cleanup
    # =========================================================================

    def disconnect(self):
        """Disconnect and cleanup."""
        self._running = False
        self._commands.cancel()
        self._streams.cancel_all()
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
    command_allowlist: Optional[List[str]] = None,
    command_denylist: Optional[List[str]] = None,
):
    """Run the connector (blocking). Handles SIGTERM for graceful shutdown."""
    import signal

    connector = PlexusConnector(
        api_key=api_key,
        device_token=device_token,
        endpoint=endpoint,
        on_status=on_status,
        sensor_hub=sensor_hub,
        camera_hub=camera_hub,
        can_adapters=can_adapters,
        command_allowlist=command_allowlist,
        command_denylist=command_denylist,
    )

    def _handle_sigterm(signum, frame):
        connector.disconnect()

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        asyncio.run(connector.connect())
    except KeyboardInterrupt:
        connector.disconnect()
