"""
WebSocket transport for the Plexus Python SDK.

Wire-compatible with the C SDK (`plexus_ws.c`). Targets the gateway's
`/ws/device` endpoint and exchanges the same JSON frames:

    client → {"type": "device_auth", "api_key": ..., "source_id": ...,
              "install_id": ..., "platform": "python-sdk",
              "agent_version": ..., "commands": [...]}
    server → {"type": "authenticated", "source_id": ...}

The server-returned `source_id` in the `authenticated` frame is
authoritative: if the gateway auto-suffixed on a collision (e.g. the
desired name was already claimed by a different install_id), the
client's `source_id` is updated in place to match.
    client → {"type": "telemetry", "points": [...]}
    client → {"type": "heartbeat", "source_id": ..., "agent_version": ...}   # every 30s
    server → {"type": "typed_command", "id": ..., "command": ..., "params": {...}}
    client → {"type": "command_result", "id": ..., "command": ..., "event": "ack"}
    client → {"type": "command_result", "id": ..., "command": ...,
              "event": "result" | "error", "result": {...} | "error": "..."}

Runs the read loop on a background daemon thread so callers can stay sync.
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

try:
    import websocket  # websocket-client
except ImportError as e:  # pragma: no cover - import-time failure is obvious
    raise ImportError(
        "WebSocket transport requires 'websocket-client'. "
        "Install with: pip install websocket-client"
    ) from e

logger = logging.getLogger(__name__)

# By default, print connection status to stderr so users running
# `python my_script.py` can see what's happening without having to
# configure the logging module. Set PLEXUS_QUIET=1 to disable.
_QUIET = os.environ.get("PLEXUS_QUIET", "").lower() in ("1", "true", "yes")


def _say(line: str) -> None:
    """Single-line status message to stderr. Skipped if PLEXUS_QUIET=1."""
    if _QUIET:
        return
    try:
        sys.stderr.write(f"[plexus] {line}\n")
        sys.stderr.flush()
    except Exception:
        # Stderr blew up — don't take the whole client down with it.
        pass

AUTH_TIMEOUT_S = 10.0
HEARTBEAT_INTERVAL_S = 30.0
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 60.0

CommandHandler = Callable[[str, Dict[str, Any]], Optional[Dict[str, Any]]]


@dataclass
class _RegisteredCommand:
    name: str
    handler: CommandHandler
    description: Optional[str] = None
    params: List[Dict[str, Any]] = field(default_factory=list)

    def to_manifest(self) -> Dict[str, Any]:
        m: Dict[str, Any] = {"name": self.name}
        if self.description:
            m["description"] = self.description
        if self.params:
            m["params"] = self.params
        return m


class WebSocketTransport:
    """Background WebSocket connection to the Plexus gateway.

    Lifecycle:
        t = WebSocketTransport(api_key, source_id, ws_url)
        t.start()
        t.wait_authenticated(timeout=5)
        t.send_points([...])
        t.stop()
    """

    def __init__(
        self,
        api_key: str,
        source_id: str,
        ws_url: str,
        *,
        install_id: str = "",
        agent_version: str = "0.0.0",
        platform: str = "python-sdk",
        auto_reconnect: bool = True,
        on_source_id_assigned: Optional[Callable[[str], None]] = None,
    ):
        if not api_key:
            raise ValueError("api_key required")
        if not source_id:
            raise ValueError("source_id required")

        self.api_key = api_key
        self.source_id = source_id
        self.install_id = install_id
        self.ws_url = _ensure_device_path(ws_url)
        self.agent_version = agent_version
        self.platform = platform
        self.auto_reconnect = auto_reconnect
        self._on_source_id_assigned = on_source_id_assigned

        self._commands: Dict[str, _RegisteredCommand] = {}
        self._ws: Optional[websocket.WebSocket] = None
        self._ws_lock = threading.Lock()
        self._authenticated = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._backoff_attempt = 0

    # ------------------------------------------------------------------ public

    def register_command(
        self,
        name: str,
        handler: CommandHandler,
        *,
        description: Optional[str] = None,
        params: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Register a command handler. Must be called before start() to be
        advertised in the auth frame."""
        self._commands[name] = _RegisteredCommand(
            name=name, handler=handler, description=description, params=params or []
        )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="plexus-ws", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        with self._ws_lock:
            ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:  # pragma: no cover
                pass
        if self._thread:
            self._thread.join(timeout=timeout)

    def wait_authenticated(self, timeout: float = AUTH_TIMEOUT_S) -> bool:
        return self._authenticated.wait(timeout=timeout)

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated.is_set()

    def send_points(self, points: List[Dict[str, Any]]) -> bool:
        """Send a telemetry frame. Returns False if the socket is not
        authenticated — caller is expected to fall back to HTTP."""
        if not points:
            return True
        if not self._authenticated.is_set():
            return False
        frame = {"type": "telemetry", "points": points}
        return self._send_frame(frame)

    # ------------------------------------------------------------------ thread

    def _run(self) -> None:
        first_attempt = True
        while not self._stop.is_set():
            try:
                self._connect_and_serve()
            except Exception as e:
                msg = str(e)
                logger.warning("plexus ws loop error: %s", msg)
                # Loud the first time so users running a script see it,
                # quieter on subsequent retries to avoid log spam.
                if first_attempt:
                    if "auth failed" in msg.lower() or "invalid api key" in msg.lower():
                        _say(f"✗ Auth rejected by gateway: {msg}")
                        _say("  Check your key — `plexus whoami` shows what's on disk.")
                    else:
                        _say(f"✗ Connection failed: {msg}")
                        _say("  SDK will keep retrying with backoff.")
            finally:
                self._authenticated.clear()
                with self._ws_lock:
                    self._ws = None

            if not self.auto_reconnect or self._stop.is_set():
                break

            delay = _backoff_delay(self._backoff_attempt)
            self._backoff_attempt = min(self._backoff_attempt + 1, 10)
            logger.info("plexus ws reconnect in %.1fs", delay)
            first_attempt = False
            if self._stop.wait(timeout=delay):
                break

    def _connect_and_serve(self) -> None:
        ws = websocket.create_connection(self.ws_url, timeout=AUTH_TIMEOUT_S)
        with self._ws_lock:
            self._ws = ws

        # 1. Send device_auth
        desired_source_id = self.source_id
        auth = {
            "type": "device_auth",
            "api_key": self.api_key,
            "source_id": desired_source_id,
            "platform": self.platform,
            "agent_version": self.agent_version,
        }
        if self.install_id:
            auth["install_id"] = self.install_id
        if self._commands:
            auth["commands"] = [c.to_manifest() for c in self._commands.values()]
        ws.send(json.dumps(auth))

        # 2. Wait for authenticated
        ws.settimeout(AUTH_TIMEOUT_S)
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException as e:
            raise TimeoutError("auth timeout") from e

        msg = _safe_json(raw)
        if msg.get("type") != "authenticated":
            raise RuntimeError(f"auth failed: {msg}")

        # The gateway may return a different source_id if the desired name
        # was already claimed by another install — adopt the assigned value
        # so all subsequent frames (heartbeats, future reconnects) use it.
        assigned = msg.get("source_id")
        if isinstance(assigned, str) and assigned and assigned != self.source_id:
            logger.info(
                "plexus ws source_id auto-suffixed: requested=%s assigned=%s",
                desired_source_id, assigned,
            )
            self.source_id = assigned
            if self._on_source_id_assigned is not None:
                try:
                    self._on_source_id_assigned(assigned)
                except Exception as e:  # pragma: no cover - callback errors must not break auth
                    logger.debug("on_source_id_assigned callback raised: %s", e)

        was_reconnect = self._backoff_attempt > 0
        self._authenticated.set()
        self._backoff_attempt = 0
        logger.info("plexus ws authenticated as %s", self.source_id)
        if was_reconnect:
            _say(f"✓ Reconnected as {self.source_id}")
        else:
            _say(f"✓ Connected to gateway as {self.source_id}")
            _say(f"  endpoint: {self.ws_url}")

        # 3. Read loop with heartbeat pump
        ws.settimeout(1.0)
        last_heartbeat = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
                self._send_frame({
                    "type": "heartbeat",
                    "source_id": self.source_id,
                    "agent_version": self.agent_version,
                })
                last_heartbeat = now

            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except (websocket.WebSocketConnectionClosedException, OSError):
                logger.info("plexus ws closed")
                return

            if not raw:
                continue
            self._dispatch(_safe_json(raw))

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == "typed_command":
            self._handle_command(msg)
        elif mtype == "error":
            logger.warning("plexus ws server error: %s", msg.get("detail") or msg)
        # ignore unknown types — forward-compat

    def _handle_command(self, msg: Dict[str, Any]) -> None:
        cmd_id = msg.get("id") or ""
        command = msg.get("command") or ""
        params = msg.get("params") or {}

        # Ack immediately (matches C SDK: plexus_ws.c:275-280)
        self._send_frame({
            "type": "command_result",
            "id": cmd_id,
            "command": command,
            "event": "ack",
        })

        reg = self._commands.get(command)
        if reg is None:
            self._send_frame({
                "type": "command_result",
                "id": cmd_id,
                "command": command,
                "event": "error",
                "error": f"unknown command: {command}",
            })
            return

        # Run the handler off the read-loop thread so a slow handler doesn't
        # block heartbeats or other inbound frames.
        threading.Thread(
            target=self._run_handler,
            args=(reg, cmd_id, command, params),
            daemon=True,
        ).start()

    def _run_handler(
        self,
        reg: _RegisteredCommand,
        cmd_id: str,
        command: str,
        params: Dict[str, Any],
    ) -> None:
        try:
            result = reg.handler(command, params)
        except Exception as e:
            self._send_frame({
                "type": "command_result",
                "id": cmd_id,
                "command": command,
                "event": "error",
                "error": str(e),
            })
            return
        self._send_frame({
            "type": "command_result",
            "id": cmd_id,
            "command": command,
            "event": "result",
            "result": result if result is not None else {},
        })

    def _send_frame(self, frame: Dict[str, Any]) -> bool:
        with self._ws_lock:
            ws = self._ws
        if ws is None:
            return False
        try:
            ws.send(json.dumps(frame))
            return True
        except Exception as e:
            logger.debug("plexus ws send failed: %s", e)
            return False


# --------------------------------------------------------------------- helpers


def _ensure_device_path(url: str) -> str:
    url = url.rstrip("/")
    if url.endswith("/ws/device"):
        return url
    return url + "/ws/device"


def _safe_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return {}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with ±25% jitter, capped at BACKOFF_MAX_S.
    Matches plexus_ws.c:44-52."""
    base = min(BACKOFF_BASE_S * (2 ** attempt), BACKOFF_MAX_S)
    jitter = base * 0.25 * (2 * random.random() - 1)
    return max(0.1, base + jitter)
