"""
GPS NMEA sensor driver.

Reads NMEA sentences from a serial GPS module (e.g., NEO-6M, NEO-7M, u-blox)
and provides latitude, longitude, altitude, speed, and satellite count.

Default: /dev/ttyAMA0 at 9600 baud (Raspberry Pi UART)

Usage:
    from plexus.sensors import GPSSensor

    gps = GPSSensor()
    for reading in gps.read():
        print(f"{reading.metric}: {reading.value}")

Or with auto-detection:
    from plexus.sensors import GPSSensor

    gps = GPSSensor.auto_detect()
    if gps:
        for reading in gps.read():
            print(f"{reading.metric}: {reading.value}")
"""

import time
import logging
from typing import List, Optional
from .base import BaseSensor, SensorReading

logger = logging.getLogger(__name__)

# Common GPS serial ports on Linux/Raspberry Pi
GPS_SERIAL_PORTS = [
    "/dev/ttyAMA0",     # Raspberry Pi built-in UART
    "/dev/ttyACM0",     # USB GPS (u-blox)
    "/dev/ttyUSB0",     # USB-serial GPS
    "/dev/ttyS0",       # Standard serial
    "/dev/serial0",     # RPi serial symlink
]

GPS_DEFAULT_BAUD = 9600


def _nmea_to_decimal(raw: str, direction: str) -> Optional[float]:
    """Convert NMEA coordinate (DDMM.MMMMM) to decimal degrees."""
    if not raw or not direction:
        return None

    try:
        raw_f = float(raw)
    except ValueError:
        return None

    degrees = int(raw_f / 100)
    minutes = raw_f - (degrees * 100)
    decimal = degrees + (minutes / 60.0)

    if direction in ("S", "W"):
        decimal = -decimal

    return decimal


def _nmea_checksum(sentence: str) -> bool:
    """Verify NMEA checksum (optional — returns True if no checksum present)."""
    if "*" not in sentence:
        return True

    body, checksum_str = sentence.rsplit("*", 1)
    # Remove leading $
    body = body.lstrip("$")

    try:
        expected = int(checksum_str[:2], 16)
    except ValueError:
        return False

    computed = 0
    for c in body:
        computed ^= ord(c)

    return computed == expected


class GPSSensor(BaseSensor):
    """
    GPS NMEA sensor driver.

    Reads NMEA sentences from a serial GPS module.

    Provides:
    - gps_latitude: Latitude in decimal degrees
    - gps_longitude: Longitude in decimal degrees
    - gps_altitude: Altitude in meters (from GGA)
    - gps_speed_knots: Speed over ground in knots (from RMC)
    - gps_satellites: Number of satellites in use
    - gps_hdop: Horizontal dilution of precision
    """

    name = "GPS"
    description = "GPS receiver (position, altitude, speed)"
    metrics = [
        "gps_latitude", "gps_longitude", "gps_altitude",
        "gps_speed_knots", "gps_satellites", "gps_hdop",
    ]
    i2c_addresses = []  # Not an I2C sensor

    def __init__(
        self,
        port: str = "/dev/ttyAMA0",
        baudrate: int = GPS_DEFAULT_BAUD,
        sample_rate: float = 1.0,
        prefix: str = "",
        tags: Optional[dict] = None,
    ):
        super().__init__(sample_rate=sample_rate, prefix=prefix, tags=tags)
        self.port = port
        self.baudrate = baudrate
        self._serial = None
        self._latitude = None
        self._longitude = None
        self._altitude = None
        self._speed_knots = None
        self._satellites = None
        self._hdop = None
        self._valid = False
        self._buffer = ""

    def setup(self) -> None:
        try:
            import serial
        except ImportError:
            raise ImportError(
                "pyserial is required for GPS. Install with: pip install pyserial"
            )

        self._serial = serial.Serial(
            self.port,
            self.baudrate,
            timeout=1.0,
        )
        # Flush input buffer
        self._serial.reset_input_buffer()

    def cleanup(self) -> None:
        if self._serial:
            self._serial.close()
            self._serial = None

    def _parse_gga(self, fields: List[str]) -> None:
        """Parse $GPGGA or $GNGGA sentence."""
        if len(fields) < 10:
            return

        quality = int(fields[6]) if fields[6] else 0
        if quality == 0:
            self._valid = False
            return

        lat = _nmea_to_decimal(fields[2], fields[3])
        lon = _nmea_to_decimal(fields[4], fields[5])

        if lat is not None:
            self._latitude = lat
        if lon is not None:
            self._longitude = lon

        self._satellites = int(fields[7]) if fields[7] else None
        self._hdop = float(fields[8]) if fields[8] else None
        self._altitude = float(fields[9]) if fields[9] else None
        self._valid = True

    def _parse_rmc(self, fields: List[str]) -> None:
        """Parse $GPRMC or $GNRMC sentence."""
        if len(fields) < 8:
            return

        if fields[2] != "A":  # V = void (no fix)
            return

        lat = _nmea_to_decimal(fields[3], fields[4])
        lon = _nmea_to_decimal(fields[5], fields[6])

        if lat is not None:
            self._latitude = lat
        if lon is not None:
            self._longitude = lon

        self._speed_knots = float(fields[7]) if fields[7] else None
        self._valid = True

    def _process_line(self, line: str) -> None:
        """Process a single NMEA sentence."""
        line = line.strip()
        if not line.startswith("$"):
            return

        if not _nmea_checksum(line):
            return

        # Remove checksum for parsing
        if "*" in line:
            line = line[:line.index("*")]

        fields = line.split(",")
        sentence_type = fields[0]

        if sentence_type in ("$GPGGA", "$GNGGA"):
            self._parse_gga(fields)
        elif sentence_type in ("$GPRMC", "$GNRMC"):
            self._parse_rmc(fields)

    def read(self) -> List[SensorReading]:
        if self._serial is None:
            self.setup()

        # Read available data (non-blocking batch)
        if self._serial.in_waiting > 0:
            raw = self._serial.read(self._serial.in_waiting)
            self._buffer += raw.decode("ascii", errors="ignore")

            # Process complete lines
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                self._process_line(line)

        if not self._valid:
            return []

        readings = []
        if self._latitude is not None:
            readings.append(SensorReading("gps_latitude", round(self._latitude, 6)))
        if self._longitude is not None:
            readings.append(SensorReading("gps_longitude", round(self._longitude, 6)))
        if self._altitude is not None:
            readings.append(SensorReading("gps_altitude", round(self._altitude, 1)))
        if self._speed_knots is not None:
            readings.append(SensorReading("gps_speed_knots", round(self._speed_knots, 1)))
        if self._satellites is not None:
            readings.append(SensorReading("gps_satellites", self._satellites))
        if self._hdop is not None:
            readings.append(SensorReading("gps_hdop", round(self._hdop, 1)))

        return readings

    def is_available(self) -> bool:
        """Check if a GPS module is connected and sending data."""
        try:
            import serial

            ser = serial.Serial(self.port, self.baudrate, timeout=3.0)
            data = ser.read(256)
            ser.close()
            # Check if we got any NMEA data
            return b"$GP" in data or b"$GN" in data
        except Exception:
            return False

    @classmethod
    def auto_detect(cls) -> Optional["GPSSensor"]:
        """
        Try to find a connected GPS module on common serial ports.

        Returns:
            GPSSensor instance if found, None otherwise.
        """
        for port in GPS_SERIAL_PORTS:
            try:
                sensor = cls(port=port)
                if sensor.is_available():
                    logger.info(f"GPS detected on {port}")
                    return cls(port=port)
            except Exception:
                continue

        return None
