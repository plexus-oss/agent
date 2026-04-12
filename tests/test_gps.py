"""Tests for GPS NMEA parsing hardening."""


from plexus.sensors.gps import (
    _nmea_checksum,
    _nmea_to_decimal,
    _validate_coordinate,
    GPSSensor,
)
from plexus.sensors.base import SensorReading


# ─── Checksum validation ─────────────────────────────────────────────────────


class TestNMEAChecksum:
    def test_valid_checksum(self):
        assert _nmea_checksum("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,47.0,M,,*4F")

    def test_invalid_checksum(self):
        assert not _nmea_checksum("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,47.0,M,,*FF")

    def test_missing_checksum_rejected_by_default(self):
        """Sentences without '*' should be rejected when require_checksum=True."""
        assert not _nmea_checksum("$GPGGA,no_star")

    def test_missing_checksum_allowed_when_not_required(self):
        assert _nmea_checksum("$GPGGA,no_star", require_checksum=False)

    def test_bad_hex_in_checksum(self):
        assert not _nmea_checksum("$GPGGA,data*ZZ")


# ─── Coordinate validation ──────────────────────────────────────────────────


class TestCoordinateValidation:
    def test_valid_coordinates(self):
        assert _validate_coordinate(0.0, 0.0)
        assert _validate_coordinate(90.0, 180.0)
        assert _validate_coordinate(-90.0, -180.0)
        assert _validate_coordinate(48.1173, 11.5167)

    def test_invalid_latitude(self):
        assert not _validate_coordinate(91.0, 0.0)
        assert not _validate_coordinate(-91.0, 0.0)

    def test_invalid_longitude(self):
        assert not _validate_coordinate(0.0, 181.0)
        assert not _validate_coordinate(0.0, -181.0)


# ─── NMEA to decimal ────────────────────────────────────────────────────────


class TestNMEAToDecimal:
    def test_north_latitude(self):
        result = _nmea_to_decimal("4807.038", "N")
        assert result is not None
        assert abs(result - 48.1173) < 0.001

    def test_south_latitude(self):
        result = _nmea_to_decimal("4807.038", "S")
        assert result is not None
        assert result < 0

    def test_empty_returns_none(self):
        assert _nmea_to_decimal("", "N") is None
        assert _nmea_to_decimal("4807.038", "") is None

    def test_nonnumeric_returns_none(self):
        assert _nmea_to_decimal("abc", "N") is None


# ─── Parse method hardening ──────────────────────────────────────────────────


class TestParseHardening:
    def _make_sensor(self):
        sensor = GPSSensor.__new__(GPSSensor)
        sensor._latitude = None
        sensor._longitude = None
        sensor._altitude = None
        sensor._speed_knots = None
        sensor._satellites = None
        sensor._hdop = None
        sensor._valid = False
        return sensor

    def test_gga_nonnumeric_quality(self):
        """Non-numeric quality indicator should not crash."""
        sensor = self._make_sensor()
        fields = ["$GPGGA", "123519", "4807.038", "N", "01131.000", "E", "abc", "08", "0.9", "545.4"]
        sensor._parse_gga(fields)
        assert not sensor._valid

    def test_gga_nonnumeric_satellites(self):
        """Non-numeric satellite count should be handled gracefully."""
        sensor = self._make_sensor()
        fields = ["$GPGGA", "123519", "4807.038", "N", "01131.000", "E", "1", "abc", "0.9", "545.4"]
        sensor._parse_gga(fields)
        assert sensor._satellites is None
        assert sensor._valid  # Rest of parse should succeed

    def test_gga_nonnumeric_altitude(self):
        sensor = self._make_sensor()
        fields = ["$GPGGA", "123519", "4807.038", "N", "01131.000", "E", "1", "08", "0.9", "abc"]
        sensor._parse_gga(fields)
        assert sensor._altitude is None
        assert sensor._valid

    def test_rmc_nonnumeric_speed(self):
        sensor = self._make_sensor()
        fields = ["$GPRMC", "123519", "A", "4807.038", "N", "01131.000", "E", "abc"]
        sensor._parse_rmc(fields)
        assert sensor._speed_knots is None
        assert sensor._valid

    def test_gga_out_of_range_coordinate_rejected(self):
        """Coordinates outside valid range should be rejected."""
        sensor = self._make_sensor()
        # latitude > 90 via raw NMEA: 9900.000 = 99 degrees
        fields = ["$GPGGA", "123519", "9900.000", "N", "01131.000", "E", "1", "08", "0.9", "545.4"]
        sensor._parse_gga(fields)
        # Lat would be 99.0 which is out of range -> parse should reject
        assert sensor._latitude is None


# ─── validate_reading override ───────────────────────────────────────────────


class TestGPSValidateReading:
    def test_rejects_out_of_range_latitude(self):
        sensor = GPSSensor.__new__(GPSSensor)
        assert not sensor.validate_reading(SensorReading("gps_latitude", 100.0))

    def test_accepts_valid_latitude(self):
        sensor = GPSSensor.__new__(GPSSensor)
        assert sensor.validate_reading(SensorReading("gps_latitude", 48.0))

    def test_rejects_out_of_range_altitude(self):
        sensor = GPSSensor.__new__(GPSSensor)
        assert not sensor.validate_reading(SensorReading("gps_altitude", 200000.0))

    def test_rejects_negative_satellites(self):
        sensor = GPSSensor.__new__(GPSSensor)
        assert not sensor.validate_reading(SensorReading("gps_satellites", -1))
