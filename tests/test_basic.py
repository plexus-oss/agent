"""Basic tests for plexus-python."""

import time

from plexus import __version__
from plexus.client import Plexus
from plexus.config import DEFAULT_CONFIG


def test_version():
    """Version should be a string."""
    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_default_config():
    """Default config should have expected keys."""
    assert "api_key" in DEFAULT_CONFIG
    assert "source_id" in DEFAULT_CONFIG


def test_client_init():
    """Client should initialize without error."""
    px = Plexus(api_key="test_key", endpoint="http://localhost")
    assert px.api_key == "test_key"
    assert px.endpoint == "http://localhost"


def test_make_point():
    """Client should create valid data points."""
    px = Plexus(api_key="test", endpoint="http://localhost")
    point = px._make_point("temperature", 72.5)

    assert point["metric"] == "temperature"
    assert point["value"] == 72.5
    assert "timestamp" in point
    assert "source_id" not in point


def test_make_point_with_tags():
    """Data points should include tags when provided."""
    px = Plexus(api_key="test", endpoint="http://localhost")
    point = px._make_point("temperature", 72.5, tags={"sensor": "A1"})

    assert point["tags"] == {"sensor": "A1"}


def test_normalize_ts_ms_applies_clock_offset():
    px = Plexus(api_key="test", endpoint="http://localhost")
    px._clock_offset_ms = 5000
    before = int(time.time() * 1000)
    ts = px._normalize_ts_ms(None)
    after = int(time.time() * 1000)
    assert before + 5000 <= ts <= after + 5000


def test_normalize_ts_ms_ignores_offset_for_supplied_timestamp():
    px = Plexus(api_key="test", endpoint="http://localhost")
    px._clock_offset_ms = 5000
    ts = px._normalize_ts_ms(1_700_000_000.0)
    assert ts == 1_700_000_000_000
