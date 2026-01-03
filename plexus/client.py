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

Note: Requires login first. Run 'plexus login' to connect your account.
"""

import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

from plexus.config import get_api_key, get_device_id, get_endpoint, require_login

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
        device_id: Unique identifier for this device. Auto-generated if not provided.
        timeout: Request timeout in seconds. Default 10s.

    Raises:
        RuntimeError: If not logged in (no API key configured)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        device_id: Optional[str] = None,
        timeout: float = 10.0,
    ):
        self.api_key = api_key or get_api_key()

        # Require login if no API key provided
        if not self.api_key:
            require_login()

        self.endpoint = (endpoint or get_endpoint()).rstrip("/")
        self.device_id = device_id or get_device_id()
        self.timeout = timeout

        self._session_id: Optional[str] = None
        self._session: Optional[requests.Session] = None

        # Buffer for batch operations
        self._buffer: List[Dict[str, Any]] = []
        self._buffer_size = 100

    def _get_session(self) -> requests.Session:
        """Get or create a requests session for connection pooling."""
        if self._session is None:
            self._session = requests.Session()
            if self.api_key:
                self._session.headers["x-api-key"] = self.api_key
            self._session.headers["Content-Type"] = "application/json"
            self._session.headers["User-Agent"] = "agent/0.1.0"
        return self._session

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
            "timestamp": timestamp or time.time(),
            "device_id": self.device_id,
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
        """Send data points to the API."""
        if not self.api_key:
            raise AuthenticationError(
                "No API key configured. Run 'plexus init' or set PLEXUS_API_KEY"
            )

        url = f"{self.endpoint}/api/ingest"

        try:
            response = self._get_session().post(
                url,
                json={"points": points},
                timeout=self.timeout,
            )

            if response.status_code == 401:
                raise AuthenticationError("Invalid API key")
            elif response.status_code == 403:
                raise AuthenticationError("API key doesn't have write permissions")
            elif response.status_code >= 400:
                raise PlexusError(f"API error: {response.status_code} - {response.text}")

            return True

        except requests.exceptions.Timeout:
            raise PlexusError(f"Request timed out after {self.timeout}s")
        except requests.exceptions.ConnectionError as e:
            raise PlexusError(f"Connection failed: {e}")

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
                    "device_id": self.device_id,
                    "status": "started",
                    "tags": tags,
                    "timestamp": time.time(),
                },
                timeout=self.timeout,
            )
        except Exception:
            pass  # Session tracking is optional, don't fail if it doesn't work

        try:
            yield
        finally:
            # Notify API that session ended
            try:
                self._get_session().post(
                    f"{self.endpoint}/api/sessions",
                    json={
                        "session_id": session_id,
                        "device_id": self.device_id,
                        "status": "ended",
                        "timestamp": time.time(),
                    },
                    timeout=self.timeout,
                )
            except Exception:
                pass
            self._session_id = None

    def close(self):
        """Close the client and release resources."""
        if self._session:
            self._session.close()
            self._session = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
