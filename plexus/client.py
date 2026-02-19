"""
Plexus client for sending sensor data.

Usage:
    from plexus import Plexus

    px = Plexus()
    px.send("temperature", 72.5)

    # With tags
    px.send("motor.rpm", 3450, tags={"motor_id": "A1"})

    # Flexible value types (not just numbers!)
    px.send("robot.state", "MOVING")                    # String states
    px.send("error.code", "E_MOTOR_STALL")              # Error codes
    px.send("position", {"x": 1.5, "y": 2.3, "z": 0.8}) # Complex objects
    px.send("joint_angles", [0.5, 1.2, -0.3, 0.0])      # Arrays
    px.send("motor.enabled", True)                      # Booleans

    # Batch send
    px.send_batch([
        ("temperature", 72.5),
        ("humidity", 45.2),
        ("pressure", 1013.25),
    ])

    # Session recording
    with px.session("motor-test-001"):
        while True:
            px.send("temperature", read_temp())
            time.sleep(0.01)

Note: Requires authentication. Run 'plexus pair' or set PLEXUS_API_KEY.
"""

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

from plexus.buffer import BufferBackend, MemoryBuffer, SqliteBuffer
from plexus.config import (
    RetryConfig,
    get_api_key,
    get_endpoint,
    get_source_id,
    require_login,
)
from plexus.typed_commands import CommandRegistry

logger = logging.getLogger(__name__)

# Flexible value type - supports any JSON-serializable value
FlexValue = Union[int, float, str, bool, Dict[str, Any], List[Any]]


class PlexusError(Exception):
    """Base exception for Plexus errors."""

    pass


class AuthenticationError(PlexusError):
    """Raised when API key is missing or invalid."""

    pass


class Plexus:
    """
    Client for sending sensor data to Plexus.

    Args:
        api_key: Your Plexus API key. If not provided, reads from
                 PLEXUS_API_KEY env var or ~/.plexus/config.json
        endpoint: API endpoint URL. Defaults to https://app.plexus.company
        source_id: Unique identifier for this source. Auto-generated if not provided.
        timeout: Request timeout in seconds. Default 10s.
        retry_config: Configuration for retry behavior. If None, uses defaults.
        max_buffer_size: Maximum number of points to buffer locally on failures. Default 10000.

    Raises:
        RuntimeError: If not logged in (no API key configured)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        source_id: Optional[str] = None,
        timeout: float = 10.0,
        retry_config: Optional[RetryConfig] = None,
        max_buffer_size: int = 10000,
        persistent_buffer: bool = False,
        buffer_path: Optional[str] = None,
    ):
        self.api_key = api_key or get_api_key()

        # Require login if no API key provided
        if not self.api_key:
            require_login()

        self.endpoint = (endpoint or get_endpoint()).rstrip("/")
        self.source_id = source_id or get_source_id()
        self.timeout = timeout
        self.retry_config = retry_config or RetryConfig()
        self.max_buffer_size = max_buffer_size

        self._session_id: Optional[str] = None
        self._session: Optional[requests.Session] = None

        # Typed command registry — commands registered via @px.command decorator
        self._command_registry = CommandRegistry()

        # Pluggable buffer backend for failed sends
        if persistent_buffer:
            self._buffer: BufferBackend = SqliteBuffer(
                path=buffer_path, max_size=max_buffer_size
            )
        else:
            self._buffer: BufferBackend = MemoryBuffer(max_size=max_buffer_size)

    def _get_session(self) -> requests.Session:
        """Get or create a requests session for connection pooling."""
        if self._session is None:
            self._session = requests.Session()
            if self.api_key:
                self._session.headers["x-api-key"] = self.api_key
            self._session.headers["Content-Type"] = "application/json"
            from plexus import __version__
            self._session.headers["User-Agent"] = f"plexus-agent/{__version__}"
        return self._session

    @staticmethod
    def _normalize_ts_ms(timestamp: Optional[float] = None) -> int:
        """Normalize a timestamp to milliseconds.

        Accepts:
            - None: returns current time in ms
            - float seconds (e.g. time.time()): converts to ms
            - int/float ms: returned as-is
        """
        if timestamp is None:
            return int(time.time() * 1000)
        # Heuristic: values < 1e12 are seconds
        if timestamp > 0 and timestamp < 1e12:
            return int(timestamp * 1000)
        return int(timestamp)

    def _make_point(
        self,
        metric: str,
        value: FlexValue,
        timestamp: Optional[float] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Create a data point dictionary.

        Value can be:
            - number (int/float): Traditional sensor readings
            - string: State machines, error codes, status
            - bool: Binary flags, enabled/disabled states
            - dict: Complex objects, vectors, nested data
            - list: Arrays, coordinates, multi-value readings
        """
        point = {
            "metric": metric,
            "value": value,
            "timestamp": self._normalize_ts_ms(timestamp),
            "source_id": self.source_id,
        }
        if tags:
            point["tags"] = tags
        if self._session_id:
            point["session_id"] = self._session_id
        return point

    def send(
        self,
        metric: str,
        value: FlexValue,
        timestamp: Optional[float] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        Send a single metric value to Plexus.

        Args:
            metric: Name of the metric (e.g., "temperature", "motor.rpm")
            value: Value to send. Can be:
                   - number (int/float): px.send("temp", 72.5)
                   - string: px.send("state", "RUNNING")
                   - bool: px.send("enabled", True)
                   - dict: px.send("pos", {"x": 1, "y": 2})
                   - list: px.send("angles", [0.5, 1.2, -0.3])
            timestamp: Unix timestamp. If not provided, uses current time.
            tags: Optional key-value tags for the metric

        Returns:
            True if successful

        Raises:
            AuthenticationError: If API key is missing or invalid (cloud mode only)
            PlexusError: If the request fails

        Example:
            px.send("temperature", 72.5)
            px.send("motor.rpm", 3450, tags={"motor_id": "A1"})
            px.send("robot.state", "IDLE")
            px.send("position", {"x": 1.5, "y": 2.3, "z": 0.0})
        """
        point = self._make_point(metric, value, timestamp, tags)
        return self._send_points([point])

    def send_batch(
        self,
        points: List[Tuple[str, FlexValue]],
        timestamp: Optional[float] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        Send multiple metrics at once.

        Args:
            points: List of (metric, value) tuples. Values can be any FlexValue type.
            timestamp: Shared timestamp for all points. If not provided, uses current time.
            tags: Shared tags for all points

        Returns:
            True if successful

        Example:
            px.send_batch([
                ("temperature", 72.5),
                ("humidity", 45.2),
                ("robot.state", "RUNNING"),
                ("position", {"x": 1.0, "y": 2.0}),
            ])
        """
        ts = timestamp or time.time()
        data_points = [self._make_point(m, v, ts, tags) for m, v in points]
        return self._send_points(data_points)

    def _send_points(self, points: List[Dict[str, Any]]) -> bool:
        """Send data points to the API with retry and buffering.

        Retry behavior:
        - Retries on: Timeout, ConnectionError, HTTP 429 (rate limit), HTTP 5xx
        - No retry on: HTTP 401/403 (auth errors), HTTP 400/422 (bad request)
        - After max retries: buffers points locally for next send attempt
        """
        if not self.api_key:
            raise AuthenticationError(
                "No API key configured. Run 'plexus pair' or set PLEXUS_API_KEY"
            )

        # Include any previously buffered points
        all_points = self._get_buffered_points() + points

        url = f"{self.endpoint}/api/ingest"
        last_error: Optional[Exception] = None

        for attempt in range(self.retry_config.max_retries + 1):
            try:
                response = self._get_session().post(
                    url,
                    json={"points": all_points},
                    timeout=self.timeout,
                )

                # Auth errors - don't retry, raise immediately
                if response.status_code == 401:
                    raise AuthenticationError("Invalid API key")
                elif response.status_code == 403:
                    raise AuthenticationError("API key doesn't have write permissions")

                # Bad request errors - don't retry (client error)
                elif response.status_code in (400, 422):
                    raise PlexusError(
                        f"Bad request: {response.status_code} - {response.text}"
                    )

                # Rate limit - retry with backoff
                elif response.status_code == 429:
                    last_error = PlexusError("Rate limited (429)")
                    if attempt < self.retry_config.max_retries:
                        time.sleep(self.retry_config.get_delay(attempt))
                        continue
                    break

                # Server errors - retry with backoff
                elif response.status_code >= 500:
                    last_error = PlexusError(
                        f"Server error: {response.status_code} - {response.text}"
                    )
                    if attempt < self.retry_config.max_retries:
                        time.sleep(self.retry_config.get_delay(attempt))
                        continue
                    break

                # Success - clear the buffer and return
                elif response.status_code < 400:
                    self._clear_buffer()
                    return True

                # Other 4xx errors - don't retry
                else:
                    raise PlexusError(
                        f"API error: {response.status_code} - {response.text}"
                    )

            except requests.exceptions.Timeout:
                last_error = PlexusError(f"Request timed out after {self.timeout}s")
                if attempt < self.retry_config.max_retries:
                    time.sleep(self.retry_config.get_delay(attempt))
                    continue
                break

            except requests.exceptions.ConnectionError as e:
                last_error = PlexusError(f"Connection failed: {e}")
                if attempt < self.retry_config.max_retries:
                    time.sleep(self.retry_config.get_delay(attempt))
                    continue
                break

        # All retries failed - buffer the points for later
        self._add_to_buffer(points)

        if last_error:
            raise last_error
        raise PlexusError("Send failed after all retries")

    def _add_to_buffer(self, points: List[Dict[str, Any]]) -> None:
        """Add points to the local buffer for later retry."""
        self._buffer.add(points)

    def _get_buffered_points(self) -> List[Dict[str, Any]]:
        """Get a copy of buffered points without clearing."""
        return self._buffer.get_all()

    def _clear_buffer(self) -> None:
        """Clear the failed points buffer."""
        self._buffer.clear()

    def buffer_size(self) -> int:
        """Return the number of points currently buffered locally.

        Points are buffered when sends fail after all retries.
        They will be included in the next send attempt.
        """
        return self._buffer.size()

    def flush_buffer(self) -> bool:
        """Attempt to send all buffered points.

        Returns:
            True if buffer is empty (either was empty or successfully flushed)

        Raises:
            PlexusError: If flush fails (points remain in buffer)
        """
        if self.buffer_size() == 0:
            return True

        # Send with empty new points list - will include buffered points
        return self._send_points([])

    @contextmanager
    def session(self, session_id: str, tags: Optional[Dict[str, str]] = None):
        """
        Context manager for recording a session.

        All sends within this context will be tagged with the session ID,
        making it easy to replay and analyze later.

        Args:
            session_id: Unique identifier for this session (e.g., "motor-test-001")
            tags: Optional tags to apply to all points in this session

        Example:
            with px.session("motor-test-001"):
                while True:
                    px.send("temperature", read_temp())
                    time.sleep(0.01)
        """
        self._session_id = session_id

        # Notify API that session started
        try:
            self._get_session().post(
                f"{self.endpoint}/api/sessions",
                json={
                    "session_id": session_id,
                    "source_id": self.source_id,
                    "status": "started",
                    "tags": tags,
                    "timestamp": time.time(),
                },
                timeout=self.timeout,
            )
        except Exception as e:
            logger.debug(f"Session start notification failed: {e}")

        try:
            yield
        finally:
            # Notify API that session ended
            try:
                self._get_session().post(
                    f"{self.endpoint}/api/sessions",
                    json={
                        "session_id": session_id,
                        "source_id": self.source_id,
                        "status": "ended",
                        "timestamp": time.time(),
                    },
                    timeout=self.timeout,
                )
            except Exception as e:
                logger.debug(f"Session end notification failed: {e}")
            self._session_id = None

    def command(self, name: str, description: str = ""):
        """
        Decorator to register a typed command.

        The command schema (name, params, types, constraints) is advertised
        to the dashboard on connect. The dashboard auto-generates UI controls
        (sliders, dropdowns, toggles) from the schema.

        Use with @param decorator for typed parameters:

            @px.command("set_speed")
            @param("rpm", type="float", min=0, max=10000, unit="RPM")
            def set_motor_speed(rpm):
                motor.set_speed(rpm)
                return {"actual_rpm": motor.read_rpm()}

        Args:
            name: Command name (used in API and dashboard)
            description: Human-readable description (falls back to docstring)
        """
        from typing import Callable

        def decorator(fn: Callable) -> Callable:
            params = getattr(fn, "_plexus_params", [])
            doc = description or (fn.__doc__ or "").strip()
            self._command_registry.register(name, fn, doc, params)
            return fn

        return decorator

    @property
    def commands(self) -> CommandRegistry:
        """Access the typed command registry."""
        return self._command_registry

    def close(self):
        """Close the client and release resources."""
        if self._session:
            self._session.close()
            self._session = None
        if hasattr(self._buffer, "close"):
            self._buffer.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
