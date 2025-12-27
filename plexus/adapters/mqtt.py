"""
MQTT Protocol Adapter - Bridge MQTT brokers to Plexus

This adapter subscribes to MQTT topics and forwards messages as metrics.
Topic names are converted to metric names (slashes become dots).

Usage:
    from plexus.adapters import MQTTAdapter

    # Basic usage
    adapter = MQTTAdapter(broker="localhost", topic="sensors/#")

    # With authentication
    adapter = MQTTAdapter(
        broker="mqtt.example.com",
        port=8883,
        username="user",
        password="pass",
        use_tls=True,
    )

    # Run with callback
    def handle_data(metrics):
        for m in metrics:
            print(f"{m.name}: {m.value}")

    adapter.run(on_data=handle_data)

Requires: pip install plexus-agent[mqtt]
"""

import json
import time
from typing import Any, List, Optional

from plexus.adapters.base import (
    ProtocolAdapter,
    Metric,
    AdapterConfig,
    AdapterState,
)
from plexus.adapters.registry import AdapterRegistry


class MQTTAdapter(ProtocolAdapter):
    """
    MQTT protocol adapter.

    Subscribes to MQTT topics and converts messages to Plexus metrics.
    Supports JSON payloads (each key becomes a metric) and simple numeric values.

    Args:
        broker: MQTT broker hostname
        port: MQTT broker port (default: 1883)
        topic: Topic pattern to subscribe to (default: "#" for all)
        username: Optional username for authentication
        password: Optional password for authentication
        use_tls: Whether to use TLS encryption
        client_id: Optional MQTT client ID
        prefix: Topic prefix to strip from metric names
        qos: Quality of service level (0, 1, or 2)
    """

    def __init__(
        self,
        broker: str = "localhost",
        port: int = 1883,
        topic: str = "#",
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_tls: bool = False,
        client_id: Optional[str] = None,
        prefix: str = "",
        qos: int = 0,
        **kwargs,
    ):
        config = AdapterConfig(
            name="mqtt",
            params={
                "broker": broker,
                "port": port,
                "topic": topic,
                "username": username,
                "use_tls": use_tls,
                "prefix": prefix,
                "qos": qos,
            },
        )
        super().__init__(config)

        self.broker = broker
        self.port = port
        self.topic = topic
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.client_id = client_id
        self.prefix = prefix
        self.qos = qos

        self._client = None
        self._pending_metrics: List[Metric] = []

    def _check_mqtt_installed(self) -> None:
        """Check if paho-mqtt is installed."""
        import importlib.util
        if importlib.util.find_spec("paho.mqtt.client") is None:
            raise ImportError(
                "MQTT support not installed. Run: pip install plexus-agent[mqtt]"
            )

    def connect(self) -> bool:
        """Connect to the MQTT broker."""
        self._check_mqtt_installed()
        import paho.mqtt.client as mqtt

        self._set_state(AdapterState.CONNECTING)

        try:
            # Create client
            if hasattr(mqtt, 'CallbackAPIVersion'):
                # paho-mqtt 2.0+
                self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            else:
                # paho-mqtt 1.x
                self._client = mqtt.Client(client_id=self.client_id)

            # Set up callbacks
            self._client.on_connect = self._on_connect
            self._client.on_message = self._on_message
            self._client.on_disconnect = self._on_disconnect

            # Authentication
            if self.username:
                self._client.username_pw_set(self.username, self.password)

            # TLS
            if self.use_tls:
                self._client.tls_set()

            # Connect
            self._client.connect(self.broker, self.port, keepalive=60)
            self._client.loop_start()

            # Wait for connection (with timeout)
            timeout = 10
            start = time.time()
            while self._state == AdapterState.CONNECTING:
                if time.time() - start > timeout:
                    self._set_state(
                        AdapterState.ERROR,
                        f"Connection timeout after {timeout}s"
                    )
                    return False
                time.sleep(0.1)

            return self._state == AdapterState.CONNECTED

        except Exception as e:
            self._set_state(AdapterState.ERROR, str(e))
            return False

    def disconnect(self) -> None:
        """Disconnect from the MQTT broker."""
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._set_state(AdapterState.DISCONNECTED)

    def poll(self) -> List[Metric]:
        """
        Get pending metrics (collected from MQTT messages).

        Returns collected metrics and clears the buffer.
        """
        metrics = self._pending_metrics.copy()
        self._pending_metrics.clear()
        return metrics

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        """Handle MQTT connection."""
        if rc == 0:
            self._set_state(AdapterState.CONNECTED)
            client.subscribe(self.topic, qos=self.qos)
        else:
            error_messages = {
                1: "Incorrect protocol version",
                2: "Invalid client identifier",
                3: "Server unavailable",
                4: "Bad username or password",
                5: "Not authorized",
            }
            error = error_messages.get(rc, f"Unknown error: {rc}")
            self._set_state(AdapterState.ERROR, error)

    def _on_disconnect(self, client, userdata, rc, properties=None):
        """Handle MQTT disconnection."""
        if rc != 0:
            self._set_state(
                AdapterState.RECONNECTING,
                f"Unexpected disconnect: {rc}"
            )
        else:
            self._set_state(AdapterState.DISCONNECTED)

    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT message."""
        try:
            metrics = self._parse_message(msg.topic, msg.payload)
            if metrics:
                self._pending_metrics.extend(metrics)
                self._emit_data(metrics)
                self.on_data(metrics)
        except Exception:
            # Log but don't crash on parse errors
            pass

    def _parse_message(self, topic: str, payload: bytes) -> List[Metric]:
        """
        Parse MQTT message into metrics.

        Handles:
            - JSON objects: Each key becomes a metric
            - Simple numeric values: Topic becomes metric name
            - Strings: Forwarded as string metrics
        """
        metrics = []

        # Convert topic to metric name
        metric_name = topic
        if self.prefix and metric_name.startswith(self.prefix):
            metric_name = metric_name[len(self.prefix):]
        metric_name = metric_name.replace("/", ".").strip(".")

        # Decode payload
        try:
            payload_str = payload.decode("utf-8").strip()
        except UnicodeDecodeError:
            return []  # Skip binary payloads

        if not payload_str:
            return []

        # Try JSON first
        if payload_str.startswith("{") or payload_str.startswith("["):
            try:
                data = json.loads(payload_str)

                if isinstance(data, dict):
                    # Each key becomes a metric
                    for key, value in data.items():
                        if self._is_valid_value(value):
                            full_name = f"{metric_name}.{key}" if metric_name else key
                            metrics.append(Metric(full_name, value))
                elif isinstance(data, list):
                    # Array becomes array metric
                    metrics.append(Metric(metric_name, data))
                return metrics

            except json.JSONDecodeError:
                pass  # Fall through to simple value handling

        # Try numeric value
        try:
            value = float(payload_str)
            # Convert to int if it's a whole number
            if value.is_integer():
                value = int(value)
            metrics.append(Metric(metric_name, value))
            return metrics
        except ValueError:
            pass

        # Treat as string value
        metrics.append(Metric(metric_name, payload_str))
        return metrics

    def _is_valid_value(self, value: Any) -> bool:
        """Check if value is valid for a metric."""
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, str):
            return True
        if isinstance(value, bool):
            return True
        if isinstance(value, (dict, list)):
            return True
        return False

    def _run_loop(self) -> None:
        """
        Run loop for MQTT (push-based).

        MQTT uses callbacks so we just need to keep the loop alive.
        """
        try:
            while self.is_connected:
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.disconnect()


# Register the adapter
AdapterRegistry.register(
    "mqtt",
    MQTTAdapter,
    description="Bridge MQTT brokers to Plexus",
    author="Plexus",
    version="1.0.0",
    requires=["paho-mqtt"],
)
