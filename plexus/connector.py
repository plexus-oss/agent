"""
Plexus Device Connector

Connects devices to Plexus via WebSocket for real-time streaming.

Data Flow:
┌─────────────────────────────────────────────────────────────────┐
│  Device (this agent)                                            │
│       │                                                         │
│       ├──► WebSocket (PartyKit) ──► Dashboard (real-time view)  │
│       │                                                         │
│       └──► HTTP (/api/ingest) ──► ClickHouse (storage)          │
│            (only when store=True OR draining backlog)           │
└─────────────────────────────────────────────────────────────────┘

User Controls (from Dashboard UI):
- "View Live" → store=False → WebSocket only (free, no storage)
- "Record"    → store=True  → WebSocket + HTTP (uses storage quota)

Store-and-forward:
- When WebSocket disconnects, StreamManager buffers telemetry to SQLite
- On reconnect, connector drains the backlog via HTTP /api/ingest
- Both paths coexist: live WebSocket streaming + HTTP backlog drain

Authentication:
- API key (plx_*) is the auth method for all device connections
"""

import asyncio
import gzip
import json
import logging
import os
import platform
import random
import socket
import time
from typing import Optional, Callable, List, Dict, Any, Tuple, TYPE_CHECKING

import websockets
from websockets.exceptions import ConnectionClosed

from plexus.config import get_api_key, get_endpoint, get_source_id, get_org_id, get_persistent_buffer
from plexus.buffer import SqliteBuffer
from plexus.streaming import StreamManager

if TYPE_CHECKING:
    from plexus.sensors.base import SensorHub
    from plexus.cameras.base import CameraHub
    from plexus.adapters.can_detect import DetectedCAN
    from plexus.adapters.mavlink_detect import DetectedMAVLink

logger = logging.getLogger(__name__)


class PlexusConnector:
    """
    WebSocket client that connects to Plexus for real-time data streaming.

    Supports:
    - Real-time sensor streaming (controlled from dashboard)
    - Camera streaming
    - Optional data persistence (when recording)
    - Store-and-forward buffering for intermittent connectivity
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        source_id: Optional[str] = None,
        source_name: Optional[str] = None,
        org_id: Optional[str] = None,
        on_status: Optional[Callable[[str], None]] = None,
        sensor_hub: Optional["SensorHub"] = None,
        camera_hub: Optional["CameraHub"] = None,
        can_adapters: Optional[List["DetectedCAN"]] = None,
        mavlink_connections: Optional[List["DetectedMAVLink"]] = None,
        max_reconnect_attempts: Optional[int] = None,
        persistent_buffer: Optional[bool] = None,
        buffer_path: Optional[str] = None,
    ):
        self.api_key = api_key or get_api_key()
        self.endpoint = (endpoint or get_endpoint()).rstrip("/")
        self.source_id = source_id or get_source_id()
        self.source_name = source_name
        resolved_org_id = org_id or get_org_id() or self._resolve_org_id()
        if not resolved_org_id:
            raise ValueError(
                "Could not resolve org_id from API key. "
                "Check your API key and network connection, or set PLEXUS_ORG_ID."
            )
        self.org_id = resolved_org_id
        self.on_status = on_status or (lambda x: None)
        self.sensor_hub = sensor_hub
        self.camera_hub = camera_hub
        self.can_adapters = can_adapters
        self.mavlink_connections = mavlink_connections
        self.max_reconnect_attempts = max_reconnect_attempts

        self._ws = None
        self._running = False
        self._authenticated = False
        self._reconnect_count = 0
        self._connect_time: float = 0.0
        self._http_session: Optional[Any] = None
        self._px: Optional[Any] = None  # Plexus client ref for buffer flush
        self._drain_task: Optional[asyncio.Task] = None

        # Store-and-forward buffer
        use_buffer = persistent_buffer if persistent_buffer is not None else get_persistent_buffer()
        if use_buffer:
            self._buffer = SqliteBuffer(
                path=buffer_path,
                max_size=None,  # Unlimited — disk-bound for store-and-forward
                max_bytes=500 * 1024 * 1024,  # 500MB safety valve
            )
            logger.info("Store-and-forward enabled (SQLite buffer)")
        else:
            self._buffer = None

        self._streams = StreamManager(
            sensor_hub=sensor_hub,
            camera_hub=camera_hub,
            can_adapters=can_adapters,
            mavlink_connections=mavlink_connections,
            on_status=self.on_status,
            persist_fn=self._persist_async,
            error_report_fn=self.report_error,
            buffer=self._buffer,
            endpoint=self.endpoint,
            api_key=self.api_key,
            source_id=self.source_id,
        )

    # =========================================================================
    # Heartbeat & Error Reporting
    # =========================================================================

    async def _heartbeat_loop(self, ws, interval=30):
        """Send heartbeat every interval so server knows device is alive."""
        from plexus import __version__
        try:
            while True:
                await asyncio.sleep(interval)
                await ws.send(json.dumps({
                    "type": "heartbeat",
                    "source_id": self.source_id,
                    "uptime_s": time.time() - self._connect_time,
                    "agent_version": __version__,
                }))
        except (asyncio.CancelledError, ConnectionClosed):
            pass

    async def report_error(self, source: str, error: str, severity: str = "warning"):
        """Report device-side error to dashboard via WebSocket."""
        if self._ws:
            try:
                await self._ws.send(json.dumps({
                    "type": "device_error",
                    "source_id": self.source_id,
                    "source": source,
                    "error": str(error),
                    "severity": severity,
                    "timestamp": time.time(),
                }))
            except Exception:
                logger.debug("Failed to send error report to dashboard")

    # =========================================================================
    # Org ID Resolution
    # =========================================================================

    def _resolve_org_id(self) -> Optional[str]:
        """Resolve org_id from the API key via the verify-key endpoint.

        Caches the result in config so subsequent runs don't need the request.
        """
        if not self.api_key:
            return None
        try:
            import requests
            resp = requests.get(
                f"{self.endpoint}/api/auth/verify-key",
                headers={"x-api-key": self.api_key},
                timeout=10,
            )
            if resp.status_code == 200:
                org_id = resp.json().get("org_id")
                if org_id:
                    # Cache in config for future runs
                    from plexus.config import load_config, save_config
                    config = load_config()
                    config["org_id"] = org_id
                    save_config(config)
                    return org_id
        except Exception:
            pass
        return None

    @staticmethod
    def _get_local_ip() -> str:
        """Get the local IP address of this device."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "unknown"

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
            if self.api_key:
                self._http_session.headers["x-api-key"] = self.api_key
            self._http_session.headers["Content-Type"] = "application/json"
            from plexus import __version__
            self._http_session.headers["User-Agent"] = f"plexus-agent/{__version__}"
        return self._http_session

    def _register_device(self, source_id: Optional[str] = None):
        """Send sensor readings to /api/ingest to register the device and capture schema.

        The AI dashboard generator needs 3+ metrics in the schema table.
        Sensor data normally goes WebSocket-only, so we POST the first
        reading to /api/ingest to seed the schema.
        """
        if not source_id or not self.api_key:
            return
        try:
            points = []

            # Read actual sensor data if available
            if self.sensor_hub:
                try:
                    readings = self.sensor_hub.read_all()
                    for r in readings:
                        if isinstance(r.value, (int, float)):
                            points.append({
                                "source_id": source_id,
                                "metric": r.metric,
                                "value": r.value,
                            })
                except Exception as e:
                    logger.debug(f"Sensor read for registration failed: {e}")

            # Always include a heartbeat
            if not points:
                points.append({
                    "source_id": source_id,
                    "metric": "_heartbeat",
                    "value": 1,
                })

            session = self._get_http_session()
            session.post(
                f"{self.endpoint}/api/ingest",
                json={"points": points},
                timeout=5.0,
            )
        except Exception as e:
            logger.debug(f"Device registration failed: {e}")

    def _persist_points(self, points: List[Dict[str, Any]]) -> Tuple[bool, float]:
        """Persist data points to ClickHouse via HTTP. Gzip-compressed for large payloads.

        Returns (success, retry_after_seconds). retry_after is 0 on success,
        >0 when rate-limited (429), or -1 on non-retryable failure.
        """
        if not self.api_key:
            return False, -1

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

            payload = json.dumps({"points": formatted}).encode("utf-8")
            session = self._get_http_session()
            url = f"{self.endpoint}/api/ingest"

            # Gzip compress payloads > 1KB
            if len(payload) > 1024:
                body = gzip.compress(payload, compresslevel=6)
                response = session.post(
                    url, data=body, timeout=10.0,
                    headers={
                        **session.headers,
                        "Content-Encoding": "gzip",
                    },
                )
            else:
                response = session.post(url, data=payload, timeout=5.0)

            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", 30))
                return False, retry_after
            return response.status_code < 400, 0
        except Exception as e:
            logger.debug(f"Persist failed: {e}")
            return False, 0

    async def _persist_async(self, points: List[Dict[str, Any]]):
        """Async wrapper - runs HTTP in thread pool."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._persist_points, points)
        # Return value (success, retry_after) intentionally ignored for
        # fire-and-forget recording persistence

    # =========================================================================
    # Backlog Drain (Store-and-Forward)
    # =========================================================================

    async def _drain_backlog(self):
        """Drain buffered telemetry to /api/ingest via HTTP.

        Called on reconnect when the buffer has data from a disconnection period.
        Runs as a background task alongside live WebSocket streaming.
        Handles rate limits (429), re-buffers on failure, and stops on disconnect.
        """
        if not self._buffer:
            return

        total_drained = 0
        initial_size = self._buffer.size()
        if initial_size == 0:
            return

        self.on_status(f"Draining {initial_size:,} buffered points...")
        loop = asyncio.get_event_loop()
        consecutive_failures = 0

        while self._running and self._authenticated:
            try:
                batch, remaining = await loop.run_in_executor(
                    None, self._buffer.drain, 5000
                )
            except Exception as e:
                logger.debug(f"Buffer drain read failed: {e}")
                break

            if not batch:
                break

            success, retry_after = await loop.run_in_executor(
                None, self._persist_points, batch
            )

            if not success:
                # Re-buffer the failed batch
                try:
                    self._buffer.add(batch)
                except Exception:
                    pass

                if retry_after > 0:
                    # Rate-limited — pause and retry
                    self.on_status(f"Backlog drain rate-limited, waiting {retry_after:.0f}s...")
                    await asyncio.sleep(retry_after)
                    consecutive_failures = 0
                    continue
                elif retry_after < 0:
                    # Non-retryable (auth error, etc.) — stop completely
                    self.on_status("Backlog drain stopped (non-retryable error)")
                    break
                else:
                    # Transient failure — retry with backoff
                    consecutive_failures += 1
                    if consecutive_failures >= 5:
                        self.on_status("Backlog drain paused (5 consecutive failures)")
                        break
                    backoff = min(2 ** consecutive_failures, 30)
                    self.on_status(f"Backlog upload failed, retrying in {backoff}s...")
                    await asyncio.sleep(backoff)
                    continue

            consecutive_failures = 0
            total_drained += len(batch)

            if remaining > 0:
                self.on_status(f"Backlog: {total_drained:,}/{initial_size:,} sent, {remaining:,} remaining")
            else:
                self.on_status(f"Backlog drained: {total_drained:,} points uploaded")

            # Brief yield to avoid starving the event loop
            await asyncio.sleep(0.05)

    def _start_drain(self):
        """Start backlog drain as a background task if buffer has data."""
        if not self._buffer or self._buffer.size() == 0:
            return
        # Cancel any existing drain
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
        self._drain_task = asyncio.create_task(self._drain_backlog())

    # =========================================================================
    # WebSocket Connection
    # =========================================================================

    async def connect(self):
        """Connect to Plexus and stream data.

        Uses exponential backoff with jitter on reconnection:
        1s → 2s → 4s → 8s → ... → 60s max, with ±25% jitter.
        Backoff resets after a successful connection that lasts >30s.
        """
        if not self.api_key:
            raise ValueError("No API key. Run 'plexus start' first.")

        ws_url = self._get_ws_url()
        self.on_status(f"Connecting to {ws_url}...")

        self._running = True
        self._reconnect_count = 0
        backoff = 1.0
        max_backoff = 60.0

        while self._running:
            connected_at = time.monotonic()
            try:
                async with websockets.connect(ws_url, ping_interval=30, ping_timeout=10) as ws:
                    self._ws = ws
                    self._authenticated = False
                    self._connect_time = time.time()

                    # Reset backoff and reconnect counter after stable connection (>30s)
                    backoff = 1.0

                    # Build auth message with device metadata
                    from plexus import __version__
                    auth_msg = {
                        "type": "device_auth",
                        "api_key": self.api_key,
                        "source_id": self.source_id,
                        "source_name": self.source_name,
                        "platform": platform.system(),
                        "agent_version": __version__,
                        "hostname": socket.gethostname(),
                        "ip_addresses": [self._get_local_ip()],
                        "os_detail": f"{platform.system()} {platform.release()}",
                        "python_version": platform.python_version(),
                        "sensors": self.sensor_hub.get_info() if self.sensor_hub else [],
                        "cameras": self.camera_hub.get_info() if self.camera_hub else [],
                        "can": [
                            {"interface": c.interface, "channel": c.channel, "bitrate": c.bitrate}
                            for c in self.can_adapters
                        ] if self.can_adapters else [],
                        "mavlink": [
                            {"connection_string": m.connection_string, "transport": m.transport}
                            for m in self.mavlink_connections
                        ] if self.mavlink_connections else [],
                    }

                    await ws.send(json.dumps(auth_msg))
                    self.on_status("Authenticating...")

                    # Launch heartbeat alongside message listener
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
                    try:
                        async for message in ws:
                            await self._handle_message(message)
                    finally:
                        heartbeat_task.cancel()
                        try:
                            await heartbeat_task
                        except asyncio.CancelledError:
                            pass

            except ConnectionClosed as e:
                self.on_status(f"Disconnected: {e.reason}")
            except Exception as e:
                self.on_status(f"Error: {e}")

            if self._running:
                # Don't escalate backoff if connection was stable (>30s)
                if time.monotonic() - connected_at < 30:
                    backoff = min(backoff * 2, max_backoff)
                    self._reconnect_count += 1
                else:
                    backoff = 1.0
                    self._reconnect_count = 0

                # Check max reconnect attempts
                if self.max_reconnect_attempts is not None and self._reconnect_count >= self.max_reconnect_attempts:
                    self.on_status(f"Max reconnect attempts ({self.max_reconnect_attempts}) reached, giving up")
                    break

                # Add ±25% jitter to prevent thundering herd
                jitter = backoff * random.uniform(0.75, 1.25)
                delay = min(jitter, max_backoff)
                self.on_status(f"Reconnecting in {delay:.1f}s...")
                await asyncio.sleep(delay)

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")

            if msg_type == "authenticated":
                self._authenticated = True
                self.on_status(f"Connected as {data.get('source_id')}")
                # Register device via /api/ingest so it appears in the UI
                self._register_device(data.get("source_id"))
                # Drain any buffered telemetry from disconnection period
                self._start_drain()
                return

            if msg_type == "error":
                err_msg = data.get("message")
                if err_msg:
                    self.on_status(f"Error: {err_msg}")
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
                "start_mavlink": lambda d: self._streams.start_mavlink_stream(d, self._ws),
                "stop_mavlink": lambda d: self._streams.stop_mavlink_stream(d),
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
        """Disconnect and cleanup, flushing any buffered telemetry."""
        self._running = False
        self._streams.cancel_all()

        # Cancel any in-progress backlog drain
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()

        # Flush any buffered points before closing
        if self._px:
            try:
                self._px.flush_buffer()
            except Exception:
                logger.warning("Failed to flush buffer on shutdown")

        self._ws = None

        if self._http_session:
            self._http_session.close()
            self._http_session = None

        # Log remaining buffer size on shutdown (don't close — data persists)
        if self._buffer:
            remaining = self._buffer.size()
            if remaining > 0:
                logger.info("Shutting down with %d points buffered (will drain on next start)", remaining)


def run_connector(
    api_key: Optional[str] = None,
    endpoint: Optional[str] = None,
    source_name: Optional[str] = None,
    on_status: Optional[Callable[[str], None]] = None,
    sensor_hub: Optional["SensorHub"] = None,
    camera_hub: Optional["CameraHub"] = None,
    can_adapters: Optional[List["DetectedCAN"]] = None,
    mavlink_connections: Optional[List["DetectedMAVLink"]] = None,
    max_reconnect_attempts: Optional[int] = None,
    persistent_buffer: Optional[bool] = None,
):
    """Run the connector (blocking). Handles SIGTERM for graceful shutdown."""
    import signal

    connector = PlexusConnector(
        api_key=api_key,
        endpoint=endpoint,
        source_name=source_name,
        on_status=on_status,
        sensor_hub=sensor_hub,
        camera_hub=camera_hub,
        can_adapters=can_adapters,
        mavlink_connections=mavlink_connections,
        max_reconnect_attempts=max_reconnect_attempts,
        persistent_buffer=persistent_buffer,
    )

    def _handle_sigterm(signum, frame):
        connector.disconnect()

    # Signal handlers can only be set in the main thread (TUI runs
    # the connector in a background thread, so skip it there).
    import threading
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        asyncio.run(connector.connect())
    except KeyboardInterrupt:
        connector.disconnect()
