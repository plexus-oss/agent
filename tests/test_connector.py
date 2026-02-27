"""Tests for PlexusConnector reconnection limits."""

import pytest

websockets = pytest.importorskip("websockets")

from unittest.mock import patch

from plexus.connector import PlexusConnector


class TestConnectorReconnect:
    def test_default_max_reconnect_is_none(self):
        """By default, reconnection attempts are unlimited."""
        with patch("plexus.connector.get_api_key", return_value="plx_test"), \
             patch("plexus.connector.get_endpoint", return_value="http://localhost"), \
             patch("plexus.connector.get_source_id", return_value="src-1"), \
             patch("plexus.connector.get_org_id", return_value="org-1"):
            c = PlexusConnector()
        assert c.max_reconnect_attempts is None
        assert c._reconnect_count == 0

    def test_custom_max_reconnect(self):
        """Custom max_reconnect_attempts should be stored."""
        with patch("plexus.connector.get_api_key", return_value="plx_test"), \
             patch("plexus.connector.get_endpoint", return_value="http://localhost"), \
             patch("plexus.connector.get_source_id", return_value="src-1"), \
             patch("plexus.connector.get_org_id", return_value="org-1"):
            c = PlexusConnector(max_reconnect_attempts=10)
        assert c.max_reconnect_attempts == 10
