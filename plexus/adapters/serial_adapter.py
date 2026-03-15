"""
Serial Port Adapter - Read serial data from devices into Plexus

This adapter reads data from serial ports (USB, UART, RS-232/485) and
parses incoming lines into Plexus metrics. Supports multiple parsing
formats for common embedded device output patterns.

Requirements:
    pip install plexus-agent[serial]
    # or
    pip install pyserial

Usage:
    from plexus.adapters import SerialAdapter

    # Basic usage - parse "metric:value" lines
    adapter = SerialAdapter(port="/dev/ttyUSB0", baudrate=115200)
    adapter.connect()
    for metric in adapter.poll():
        print(f"{metric.name}: {metric.value}")

    # JSON mode - parse {"temp": 22.5, "humidity": 45} lines
    adapter = SerialAdapter(
        port="/dev/ttyACM0",
        baudrate=9600,
        parser="json",
    )

    # CSV mode - first line is headers, subsequent lines are values
    # e.g., "temp,humidity,pressure\\n22.5,45,1013.2"
    adapter = SerialAdapter(
        port="COM3",
        baudrate=115200,
        parser="csv",
    )

    # Run with callback
    def handle_data(metrics):
        for m in metrics:
            print(f"{m.name}: {m.value}")

    adapter.run(on_data=handle_data)

Emitted metrics:
    - serial.{metric_name} - Parsed metric with configurable prefix

Parser formats:
    - "line": Expects "metric_name:value" per line (default)
    - "json": Expects a JSON object per line; each key becomes a metric
    - "csv": First line is comma-separated headers, subsequent lines are values

Requires: pip install pyserial
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from plexus.adapters.base import (
    ProtocolAdapter,
    Metric,
    AdapterConfig,
    AdapterState,
    ConnectionError,
    ProtocolError,
)
from plexus.adapters.registry import AdapterRegistry

logger = logging.getLogger(__name__)

# Optional dependency — imported at module level so it can be mocked in tests
try:
    import serial as pyserial
except ImportError:
    pyserial = None  # type: ignore[assignment]


class SerialAdapter(ProtocolAdapter):
    """
    Serial port protocol adapter.

    Reads lines from a serial port and parses them into Plexus metrics.
    Three parser modes are supported:

    **line** (default): Each line contains "metric_name:value". The metric
    name and numeric value are split on the colon. Non-numeric values are
    forwarded as strings.

        Example input::

            temperature:22.5
            humidity:45
            status:OK

    **json**: Each line is a JSON object. Every key-value pair in the object
    becomes a separate metric.

        Example input::

            {"temperature": 22.5, "humidity": 45}

    **csv**: The first line received is treated as a comma-separated header
    row. All subsequent lines are parsed as values matching those headers.

        Example input::

            temperature,humidity,pressure
            22.5,45,1013.2
            22.6,44,1013.1

    Args:
        port: Serial port path (e.g., "/dev/ttyUSB0", "COM3")
        baudrate: Baud rate (default: 9600)
        parser: Parsing mode - "line", "json", or "csv" (default: "line")
        line_ending: Line ending character(s) (default: "\\n")
        prefix: Prefix prepended to all metric names (default: "serial.")
        timeout: Serial read timeout in seconds (default: 1.0)
        source_id: Optional source identifier attached to all emitted metrics

    Example:
        adapter = SerialAdapter(
            port="/dev/ttyUSB0",
            baudrate=115200,
            parser="json",
        )

        with adapter:
            while True:
                for metric in adapter.poll():
                    print(f"{metric.name} = {metric.value}")
    """

    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 9600,
        parser: str = "line",
        line_ending: str = "\n",
        prefix: str = "serial.",
        timeout: float = 1.0,
        source_id: Optional[str] = None,
        **kwargs,
    ):
        config = AdapterConfig(
            name="serial",
            params={
                "port": port,
                "baudrate": baudrate,
                "parser": parser,
                "line_ending": line_ending,
                "prefix": prefix,
                **kwargs,
            },
        )
        super().__init__(config)

        self.port = port
        self.baudrate = baudrate
        self.parser = parser
        self.line_ending = line_ending
        self.prefix = prefix
        self.timeout = timeout
        self._source_id = source_id

        self._serial: Optional[Any] = None  # serial.Serial instance
        self._csv_headers: Optional[List[str]] = None
        self._read_buffer: str = ""

    def validate_config(self) -> bool:
        """Validate adapter configuration."""
        if not self.port:
            raise ValueError("Serial port is required")

        valid_parsers = ["line", "json", "csv"]
        if self.parser not in valid_parsers:
            raise ValueError(
                f"Invalid parser '{self.parser}'. "
                f"Valid parsers: {', '.join(valid_parsers)}"
            )

        if self.baudrate <= 0:
            raise ValueError("Baud rate must be positive")

        return True

    def connect(self) -> bool:
        """
        Open the serial port.

        Returns:
            True if the port was opened successfully, False otherwise.

        Raises:
            ConnectionError: If pyserial is not installed or the port
                cannot be opened.
        """
        if pyserial is None:
            self._set_state(AdapterState.ERROR, "pyserial not installed")
            raise ConnectionError(
                "pyserial is required. Install with: pip install plexus-agent[serial] "
                "or pip install pyserial"
            )

        try:
            self._set_state(AdapterState.CONNECTING)
            logger.info(
                f"Opening serial port: {self.port} at {self.baudrate} baud"
            )

            self._serial = pyserial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
            )

            # Reset CSV headers for a fresh connection
            self._csv_headers = None
            self._read_buffer = ""

            self._set_state(AdapterState.CONNECTED)
            logger.info(f"Serial port opened: {self.port}")
            return True

        except Exception as e:
            self._set_state(AdapterState.ERROR, str(e))
            logger.error(f"Failed to open serial port: {e}")
            raise ConnectionError(f"Serial connection failed: {e}")

    def disconnect(self) -> None:
        """Close the serial port."""
        if self._serial:
            try:
                self._serial.close()
                logger.info(f"Serial port closed: {self.port}")
            except Exception as e:
                logger.warning(f"Error closing serial port: {e}")
            finally:
                self._serial = None

        self._csv_headers = None
        self._read_buffer = ""
        self._set_state(AdapterState.DISCONNECTED)

    def poll(self) -> List[Metric]:
        """
        Read available lines from the serial port and parse them into metrics.

        Reads all available data from the serial buffer, splits into complete
        lines, and parses each line according to the configured parser mode.

        Returns:
            List of Metric objects. Empty list if no complete lines available.

        Raises:
            OSError: On port disconnect (triggers auto-reconnect in run loop).
            ProtocolError: If reading data fails.
        """
        if not self._serial or not self._serial.is_open:
            return []

        metrics: List[Metric] = []

        try:
            lines = self._read_lines()
            for line in lines:
                parsed = self._parse_line(line)
                metrics.extend(parsed)
        except OSError:
            raise  # Let run loop handle disconnect/reconnect
        except Exception as e:
            logger.error(f"Error reading from serial port: {e}")
            raise ProtocolError(f"Serial read error: {e}")

        return metrics

    def _read_lines(self) -> List[str]:
        """
        Read complete lines from the serial port.

        Reads all bytes currently in the serial buffer, appends them to an
        internal buffer, and splits on the configured line ending. Incomplete
        lines (no trailing line ending) are kept in the buffer for the next
        call.

        Returns:
            List of complete lines (without line endings).
        """
        if not self._serial:
            return []

        # Read all available bytes
        waiting = self._serial.in_waiting
        if waiting > 0:
            raw = self._serial.read(waiting)
        else:
            # Do a blocking read up to timeout for one byte, then grab rest
            raw = self._serial.read(1)
            if raw:
                extra = self._serial.in_waiting
                if extra > 0:
                    raw += self._serial.read(extra)

        if not raw:
            return []

        try:
            self._read_buffer += raw.decode("utf-8", errors="replace")
        except Exception:
            return []

        # Split on line ending
        parts = self._read_buffer.split(self.line_ending)

        # Last element is either empty (line ended with separator) or
        # an incomplete line — keep it in the buffer
        self._read_buffer = parts[-1]
        complete_lines = [line.strip() for line in parts[:-1] if line.strip()]

        return complete_lines

    def _parse_line(self, line: str) -> List[Metric]:
        """
        Parse a single line using the configured parser.

        Dispatches to the appropriate parser method based on self.parser.

        Args:
            line: A complete line of text from the serial port.

        Returns:
            List of Metric objects parsed from the line.
        """
        if self.parser == "json":
            return self._parse_json(line)
        elif self.parser == "csv":
            return self._parse_csv(line)
        else:
            return self._parse_key_value(line)

    def _parse_key_value(self, line: str) -> List[Metric]:
        """
        Parse a "metric_name:value" line.

        If the line contains a colon, splits on the first colon to get the
        metric name and value. Attempts to convert the value to a number;
        falls back to string.

        Args:
            line: Line in "name:value" format.

        Returns:
            Single-element list with the parsed Metric, or empty list on
            parse failure.
        """
        if ":" not in line:
            logger.debug(f"Skipping line without colon separator: {line!r}")
            return []

        name, _, raw_value = line.partition(":")
        name = name.strip()
        raw_value = raw_value.strip()

        if not name or not raw_value:
            return []

        value = self._coerce_value(raw_value)
        metric_name = f"{self.prefix}{name}"

        return [
            Metric(
                name=metric_name,
                value=value,
                timestamp=time.time(),
                source_id=self._source_id,
            )
        ]

    def _parse_json(self, line: str) -> List[Metric]:
        """
        Parse a JSON object line. Each key becomes a separate metric.

        Args:
            line: Line containing a JSON object string.

        Returns:
            List of Metric objects, one per key-value pair.
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.debug(f"Failed to parse JSON line: {line!r}")
            return []

        if not isinstance(data, dict):
            logger.debug(f"JSON line is not an object: {line!r}")
            return []

        metrics: List[Metric] = []
        now = time.time()

        for key, value in data.items():
            if not self._is_valid_value(value):
                continue

            metric_name = f"{self.prefix}{key}"
            metrics.append(
                Metric(
                    name=metric_name,
                    value=value,
                    timestamp=now,
                    source_id=self._source_id,
                )
            )

        return metrics

    def _parse_csv(self, line: str) -> List[Metric]:
        """
        Parse a CSV line using previously received headers.

        The first line received in CSV mode is treated as the header row.
        Subsequent lines are parsed as comma-separated values matching
        those headers positionally.

        Args:
            line: Comma-separated line of text.

        Returns:
            List of Metric objects, one per column. Empty list if this is
            the header line or parsing fails.
        """
        parts = [p.strip() for p in line.split(",")]

        if self._csv_headers is None:
            # First line is the header
            self._csv_headers = parts
            logger.debug(f"CSV headers set: {self._csv_headers}")
            return []

        if len(parts) != len(self._csv_headers):
            logger.debug(
                f"CSV column count mismatch: expected {len(self._csv_headers)}, "
                f"got {len(parts)}"
            )
            return []

        metrics: List[Metric] = []
        now = time.time()

        for header, raw_value in zip(self._csv_headers, parts):
            if not header or not raw_value:
                continue

            value = self._coerce_value(raw_value)
            metric_name = f"{self.prefix}{header}"

            metrics.append(
                Metric(
                    name=metric_name,
                    value=value,
                    timestamp=now,
                    source_id=self._source_id,
                )
            )

        return metrics

    def _coerce_value(self, raw: str) -> Any:
        """
        Coerce a raw string value to the most appropriate Python type.

        Tries int, then float, then returns the original string.
        """
        # Try int
        try:
            return int(raw)
        except ValueError:
            pass

        # Try float
        try:
            return float(raw)
        except ValueError:
            pass

        # Boolean-ish strings
        lower = raw.lower()
        if lower in ("true", "yes", "on"):
            return True
        if lower in ("false", "no", "off"):
            return False

        return raw

    def _is_valid_value(self, value: Any) -> bool:
        """Check if a value is a valid Metric value type."""
        return isinstance(value, (int, float, str, bool, dict, list))

    def write(self, data: str) -> bool:
        """
        Write a string to the serial port.

        Args:
            data: String data to send. Line ending is NOT automatically appended.

        Returns:
            True if written successfully.

        Raises:
            ProtocolError: If the port is not open or write fails.
        """
        if not self._serial or not self._serial.is_open:
            raise ProtocolError("Serial port is not open")

        try:
            self._serial.write(data.encode("utf-8"))
            self._serial.flush()
            logger.debug(f"Wrote to serial: {data!r}")
            return True
        except Exception as e:
            logger.error(f"Failed to write to serial port: {e}")
            raise ProtocolError(f"Serial write error: {e}")

    @property
    def stats(self) -> Dict[str, Any]:
        """Get adapter statistics including serial-specific info."""
        base_stats = super().stats
        base_stats.update({
            "port": self.port,
            "baudrate": self.baudrate,
            "parser": self.parser,
            "csv_headers": self._csv_headers,
            "is_open": self._serial.is_open if self._serial else False,
        })
        return base_stats


# Register the adapter
AdapterRegistry.register(
    "serial",
    SerialAdapter,
    description="Serial port adapter for USB/UART/RS-232/RS-485 devices",
    author="Plexus",
    version="1.0.0",
    requires=["pyserial"],
)
