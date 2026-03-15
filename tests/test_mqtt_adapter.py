"""
Tests for MQTTAdapter

Run with: pytest tests/test_mqtt_adapter.py -v
"""

import json
import pytest
from unittest.mock import Mock, MagicMock, patch


class TestMQTTAdapterImport:
    """Test adapter import and registration."""

    def test_adapter_import(self):
        from plexus.adapters.mqtt import MQTTAdapter
        assert MQTTAdapter is not None

    def test_adapter_registration(self):
        from plexus.adapters import AdapterRegistry
        from plexus.adapters.mqtt import MQTTAdapter  # noqa: F401

        assert AdapterRegistry.has("mqtt")
        info = AdapterRegistry.info("mqtt")
        assert info["name"] == "mqtt"
        assert "paho-mqtt" in info["requires"]


class TestMQTTAdapterConfig:
    """Test adapter configuration."""

    def test_default_config(self):
        from plexus.adapters.mqtt import MQTTAdapter

        adapter = MQTTAdapter()
        assert adapter.broker == "localhost"
        assert adapter.port == 1883
        assert adapter.topic == "#"
        assert adapter.username is None
        assert adapter.password is None
        assert adapter.use_tls is False
        assert adapter.qos == 0
        assert adapter.prefix == ""

    def test_custom_config(self):
        from plexus.adapters.mqtt import MQTTAdapter

        adapter = MQTTAdapter(
            broker="mqtt.example.com",
            port=8883,
            topic="sensors/#",
            username="user",
            password="pass",
            use_tls=True,
            prefix="home/",
            qos=1,
        )
        assert adapter.broker == "mqtt.example.com"
        assert adapter.port == 8883
        assert adapter.topic == "sensors/#"
        assert adapter.username == "user"
        assert adapter.password == "pass"
        assert adapter.use_tls is True
        assert adapter.prefix == "home/"
        assert adapter.qos == 1


class TestMQTTAdapterMessageParsing:
    """Test MQTT message parsing."""

    @pytest.fixture
    def adapter(self):
        from plexus.adapters.mqtt import MQTTAdapter
        return MQTTAdapter(broker="localhost", prefix="")

    def test_parse_json_object(self, adapter):
        """JSON object: each key becomes a metric."""
        payload = json.dumps({"temperature": 22.5, "humidity": 45}).encode()
        metrics = adapter._parse_message("sensors/room1", payload)

        assert len(metrics) == 2
        names = {m.name for m in metrics}
        assert "sensors.room1.temperature" in names
        assert "sensors.room1.humidity" in names

    def test_parse_json_array(self, adapter):
        """JSON array: becomes a single array metric."""
        payload = json.dumps([1.0, 2.0, 3.0]).encode()
        metrics = adapter._parse_message("data/array", payload)

        assert len(metrics) == 1
        assert metrics[0].name == "data.array"
        assert metrics[0].value == [1.0, 2.0, 3.0]

    def test_parse_numeric_value(self, adapter):
        """Simple numeric payload."""
        metrics = adapter._parse_message("sensors/temp", b"22.5")

        assert len(metrics) == 1
        assert metrics[0].name == "sensors.temp"
        assert metrics[0].value == 22.5

    def test_parse_integer_value(self, adapter):
        """Integer payload should be int, not float."""
        metrics = adapter._parse_message("count", b"42")

        assert len(metrics) == 1
        assert metrics[0].value == 42
        assert isinstance(metrics[0].value, int)

    def test_parse_string_value(self, adapter):
        """Non-numeric string payload."""
        metrics = adapter._parse_message("status", b"online")

        assert len(metrics) == 1
        assert metrics[0].name == "status"
        assert metrics[0].value == "online"

    def test_parse_empty_payload(self, adapter):
        """Empty payload returns no metrics."""
        metrics = adapter._parse_message("topic", b"")
        assert metrics == []

    def test_parse_binary_payload(self, adapter):
        """Binary payload (non-UTF-8) returns no metrics."""
        metrics = adapter._parse_message("topic", b"\x80\x81\x82")
        assert metrics == []

    def test_topic_to_metric_name(self, adapter):
        """Slashes in topic become dots in metric name."""
        metrics = adapter._parse_message("a/b/c", b"1")
        assert metrics[0].name == "a.b.c"

    def test_prefix_stripping(self):
        """Configured prefix is stripped from topic."""
        from plexus.adapters.mqtt import MQTTAdapter

        adapter = MQTTAdapter(prefix="home/")
        metrics = adapter._parse_message("home/sensors/temp", b"22")

        assert metrics[0].name == "sensors.temp"


class TestMQTTAdapterConnection:
    """Test connection handling."""

    def test_connect_success(self):
        """Test successful connection by directly setting up adapter state."""
        from plexus.adapters.mqtt import MQTTAdapter
        from plexus.adapters.base import AdapterState

        adapter = MQTTAdapter(broker="localhost")

        # Directly test the connection logic by simulating what connect() does
        mock_client = MagicMock()
        adapter._client = mock_client
        adapter._set_state(AdapterState.CONNECTING)

        # Simulate on_connect callback (what paho calls when connected)
        adapter._on_connect(mock_client, None, None, Mock(is_failure=False))

        assert adapter.state == AdapterState.CONNECTED
        mock_client.subscribe.assert_called_once_with("#", qos=0)

    def test_on_connect_failure(self):
        """Test on_connect with failure sets ERROR state."""
        from plexus.adapters.mqtt import MQTTAdapter
        from plexus.adapters.base import AdapterState

        adapter = MQTTAdapter(broker="localhost")
        adapter._client = MagicMock()
        adapter._set_state(AdapterState.CONNECTING)

        # Simulate failed connection callback (paho-mqtt 1.x style: rc=5)
        adapter._on_connect(adapter._client, None, None, 5)

        assert adapter.state == AdapterState.ERROR

    def test_disconnect(self):
        """Test disconnect cleans up."""
        from plexus.adapters.mqtt import MQTTAdapter
        from plexus.adapters.base import AdapterState

        adapter = MQTTAdapter()
        mock_client = MagicMock()
        adapter._client = mock_client

        adapter.disconnect()

        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()
        assert adapter.state == AdapterState.DISCONNECTED

    def test_poll_returns_pending_metrics(self):
        """Poll returns accumulated metrics and clears buffer."""
        from plexus.adapters.mqtt import MQTTAdapter
        from plexus.adapters.base import Metric

        adapter = MQTTAdapter()
        adapter._pending_metrics = [
            Metric("temp", 22.5),
            Metric("humidity", 45),
        ]

        metrics = adapter.poll()

        assert len(metrics) == 2
        assert adapter._pending_metrics == []


class TestMQTTAdapterReconnection:
    """Test MQTT reconnection behavior."""

    def test_unexpected_disconnect_triggers_reconnect(self):
        """Unexpected disconnect (rc!=0) should set RECONNECTING state."""
        from plexus.adapters.mqtt import MQTTAdapter
        from plexus.adapters.base import AdapterState

        adapter = MQTTAdapter()
        adapter._client = MagicMock()

        with patch("threading.Thread") as mock_thread:
            adapter._on_disconnect(adapter._client, None, 1)

            assert adapter.state == AdapterState.RECONNECTING
            mock_thread.assert_called_once()
            mock_thread.return_value.start.assert_called_once()

    def test_clean_disconnect_no_reconnect(self):
        """Clean disconnect (rc=0) should not attempt reconnect."""
        from plexus.adapters.mqtt import MQTTAdapter
        from plexus.adapters.base import AdapterState

        adapter = MQTTAdapter()
        adapter._client = MagicMock()

        with patch("threading.Thread") as mock_thread:
            adapter._on_disconnect(adapter._client, None, 0)

            assert adapter.state == AdapterState.DISCONNECTED
            mock_thread.assert_not_called()

    def test_auto_reconnect_disabled(self):
        """No reconnect thread when auto_reconnect is False."""
        from plexus.adapters.mqtt import MQTTAdapter
        from plexus.adapters.base import AdapterState

        adapter = MQTTAdapter()
        adapter.config.auto_reconnect = False
        adapter._client = MagicMock()

        with patch("threading.Thread") as mock_thread:
            adapter._on_disconnect(adapter._client, None, 1)

            assert adapter.state == AdapterState.RECONNECTING
            mock_thread.assert_not_called()


class TestMQTTAdapterOnMessage:
    """Test on_message callback."""

    def test_on_message_json(self):
        """on_message parses JSON and extends pending metrics."""
        from plexus.adapters.mqtt import MQTTAdapter

        adapter = MQTTAdapter()

        msg = Mock()
        msg.topic = "sensors/temp"
        msg.payload = json.dumps({"value": 22.5}).encode()

        adapter._on_message(None, None, msg)

        assert len(adapter._pending_metrics) == 1
        assert adapter._pending_metrics[0].name == "sensors.temp.value"

    def test_on_message_invalid_payload(self):
        """Invalid message should not crash, just log."""
        from plexus.adapters.mqtt import MQTTAdapter

        adapter = MQTTAdapter()

        msg = Mock()
        msg.topic = "test"
        msg.payload = b"\x80\x81"

        adapter._on_message(None, None, msg)
        assert adapter._pending_metrics == []
