"""
OPC-UA Protocol Adapter - Bridge OPC-UA servers to Plexus

This adapter connects to OPC-UA servers, browses or reads configured nodes,
and forwards their values as Plexus metrics. Supports both polling and
subscription-based modes.

Requirements:
    pip install plexus-agent[opcua]
    # or
    pip install asyncua

Usage:
    from plexus.adapters import OPCUAAdapter

    # Basic usage - browse all nodes under Objects
    adapter = OPCUAAdapter(endpoint="opc.tcp://localhost:4840")
    adapter.connect()
    for metric in adapter.poll():
        print(f"{metric.name}: {metric.value}")

    # Read specific nodes
    adapter = OPCUAAdapter(
        endpoint="opc.tcp://plc.factory.local:4840",
        namespace=2,
        node_ids=["Temperature", "Pressure", "FlowRate"],
        poll_interval=0.5,
    )

    # With authentication and security
    adapter = OPCUAAdapter(
        endpoint="opc.tcp://secure-server:4840",
        username="operator",
        password="secret",
        security_policy="Basic256Sha256",
    )

    # Subscription-based (push) mode
    def handle_data(metrics):
        for m in metrics:
            print(f"{m.name}: {m.value}")

    adapter = OPCUAAdapter(
        endpoint="opc.tcp://localhost:4840",
        node_ids=["Temperature", "Pressure"],
    )
    adapter.run(on_data=handle_data)

Emitted metrics:
    - opcua.{NodeName} - Node value with optional engineering unit tag
    - Custom prefix can be set via the prefix parameter

Requires: pip install asyncua
"""

import asyncio
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
    from asyncua import Client as OPCUAClient
    from asyncua import ua
except ImportError:
    OPCUAClient = None  # type: ignore[assignment, misc]
    ua = None  # type: ignore[assignment]


class OPCUAAdapter(ProtocolAdapter):
    """
    OPC-UA protocol adapter.

    Connects to an OPC-UA server and reads node values as Plexus metrics.
    Supports both polling mode (read nodes on demand) and subscription mode
    (receive data change notifications via OPC-UA subscriptions).

    In polling mode, call poll() to read the current values of all configured
    nodes. If no node_ids are provided, the adapter browses children of the
    Objects folder (ns=0;i=85) and reads all variable nodes it finds.

    In subscription mode (via run()), the adapter creates an OPC-UA
    subscription and monitors configured nodes for data changes, emitting
    metrics through the callback as values change.

    Args:
        endpoint: OPC-UA server endpoint URL (e.g., "opc.tcp://localhost:4840")
        namespace: Default namespace index for node IDs (default: 2)
        node_ids: List of node identifier strings to read. If None, the adapter
                  browses the Objects folder for variable nodes.
        username: Optional username for authentication
        password: Optional password for authentication
        security_policy: Optional security policy string (e.g., "Basic256Sha256").
                         When set, the connection uses Sign & Encrypt mode.
        poll_interval: Seconds between polls / subscription publish interval
                       (default: 1.0)
        prefix: Prefix prepended to all metric names (default: "opcua.")
        source_id: Optional source identifier attached to all emitted metrics

    Example:
        adapter = OPCUAAdapter(
            endpoint="opc.tcp://localhost:4840",
            namespace=2,
            node_ids=["Temperature", "Pressure"],
        )

        with adapter:
            while True:
                for metric in adapter.poll():
                    print(f"{metric.name} = {metric.value}")
    """

    def __init__(
        self,
        endpoint: str = "opc.tcp://localhost:4840",
        namespace: int = 2,
        node_ids: Optional[List[str]] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        security_policy: Optional[str] = None,
        poll_interval: float = 1.0,
        prefix: str = "opcua.",
        source_id: Optional[str] = None,
        **kwargs,
    ):
        config = AdapterConfig(
            name="opcua",
            params={
                "endpoint": endpoint,
                "namespace": namespace,
                "node_ids": node_ids,
                "username": username,
                "security_policy": security_policy,
                "poll_interval": poll_interval,
                "prefix": prefix,
                **kwargs,
            },
        )
        super().__init__(config)

        self.endpoint = endpoint
        self.namespace = namespace
        self.node_ids = node_ids
        self.username = username
        self.password = password
        self.security_policy = security_policy
        self.poll_interval = poll_interval
        self.prefix = prefix
        self._source_id = source_id

        self._client: Optional[Any] = None  # asyncua.Client instance
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._resolved_nodes: List[Any] = []  # Resolved Node objects
        self._node_names: Dict[str, str] = {}  # node_id -> display name
        self._node_units: Dict[str, str] = {}  # node_id -> engineering unit
        self._subscription: Optional[Any] = None
        self._pending_metrics: List[Metric] = []

    def validate_config(self) -> bool:
        """Validate adapter configuration."""
        if not self.endpoint:
            raise ValueError("OPC-UA endpoint URL is required")
        if not self.endpoint.startswith("opc.tcp://"):
            raise ValueError(
                f"Invalid OPC-UA endpoint: '{self.endpoint}'. "
                "Must start with 'opc.tcp://'"
            )
        if self.namespace < 0:
            raise ValueError("Namespace index must be non-negative")
        if self.poll_interval <= 0:
            raise ValueError("Poll interval must be positive")
        return True

    def _get_or_create_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create an event loop for running async code."""
        if self._loop is not None and not self._loop.is_closed():
            return self._loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        self._loop = loop
        return loop

    def _run_async(self, coro):
        """Run an async coroutine synchronously."""
        loop = self._get_or_create_loop()
        if loop.is_running():
            # We're inside an already-running loop (e.g., Jupyter).
            # Create a new loop in this case.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)

    def connect(self) -> bool:
        """
        Connect to the OPC-UA server.

        Creates an asyncua Client, optionally configures authentication and
        security, connects, and resolves the target nodes.

        Returns:
            True if connection successful, False otherwise.

        Raises:
            ConnectionError: If asyncua is not installed or connection fails.
        """
        if OPCUAClient is None:
            self._set_state(AdapterState.ERROR, "asyncua not installed")
            raise ConnectionError(
                "asyncua is required. Install with: pip install plexus-agent[opcua] "
                "or pip install asyncua"
            )

        try:
            self._set_state(AdapterState.CONNECTING)
            logger.info(f"Connecting to OPC-UA server: {self.endpoint}")
            self._run_async(self._async_connect())
            self._set_state(AdapterState.CONNECTED)
            logger.info(
                f"Connected to OPC-UA server: {self.endpoint} "
                f"({len(self._resolved_nodes)} nodes resolved)"
            )
            return True

        except Exception as e:
            self._set_state(AdapterState.ERROR, str(e))
            logger.error(f"Failed to connect to OPC-UA server: {e}")
            raise ConnectionError(f"OPC-UA connection failed: {e}")

    async def _async_connect(self) -> None:
        """Async implementation of connect."""
        self._client = OPCUAClient(url=self.endpoint)

        # Authentication
        if self.username:
            self._client.set_user(self.username)
        if self.password:
            self._client.set_password(self.password)

        # Security policy
        if self.security_policy:
            await self._client.set_security_string(
                f"{self.security_policy},SignAndEncrypt"
            )

        await self._client.connect()

        # Resolve nodes
        await self._resolve_nodes()

    async def _resolve_nodes(self) -> None:
        """
        Resolve configured node IDs to Node objects.

        If node_ids were provided, resolve each one under the configured
        namespace. Otherwise, browse the Objects folder and discover all
        variable nodes.
        """
        self._resolved_nodes = []
        self._node_names = {}
        self._node_units = {}

        if self.node_ids:
            # Resolve explicitly configured nodes
            for node_id in self.node_ids:
                try:
                    node = self._client.get_node(
                        ua.NodeId(node_id, self.namespace)
                    )
                    display_name = await node.read_display_name()
                    name_text = display_name.Text if hasattr(display_name, 'Text') else str(display_name)
                    node_str = node.nodeid.to_string()

                    self._resolved_nodes.append(node)
                    self._node_names[node_str] = name_text

                    # Try to read engineering units
                    await self._read_engineering_unit(node, node_str)

                    logger.debug(f"Resolved node: {node_id} -> {name_text}")
                except Exception as e:
                    logger.warning(f"Failed to resolve node '{node_id}': {e}")
        else:
            # Browse Objects folder for variable nodes
            await self._browse_objects()

    async def _browse_objects(self) -> None:
        """Browse the Objects folder and find all readable variable nodes."""
        objects_node = self._client.nodes.objects
        await self._browse_recursive(objects_node, max_depth=3)

    async def _browse_recursive(self, node, max_depth: int = 3, depth: int = 0) -> None:
        """Recursively browse children of a node up to max_depth."""
        if depth >= max_depth:
            return

        try:
            children = await node.get_children()
        except Exception:
            return

        for child in children:
            try:
                node_class = await child.read_node_class()
                # ua.NodeClass.Variable == 2
                if node_class == ua.NodeClass.Variable:
                    display_name = await child.read_display_name()
                    name_text = display_name.Text if hasattr(display_name, 'Text') else str(display_name)
                    node_str = child.nodeid.to_string()

                    self._resolved_nodes.append(child)
                    self._node_names[node_str] = name_text

                    await self._read_engineering_unit(child, node_str)

                    logger.debug(f"Discovered variable node: {name_text}")
                elif node_class == ua.NodeClass.Object:
                    # Recurse into object nodes
                    await self._browse_recursive(child, max_depth, depth + 1)
            except Exception as e:
                logger.debug(f"Error browsing child node: {e}")

    async def _read_engineering_unit(self, node, node_str: str) -> None:
        """
        Try to read the EngineeringUnits property of a node.

        OPC-UA analog items may expose an EUInformation structure containing
        a display name for the engineering unit (e.g., "degC", "bar").
        """
        try:
            # EUInformation is typically in property node with BrowseName "EngineeringUnits"
            eu_props = await node.get_properties()
            for prop in eu_props:
                browse_name = await prop.read_browse_name()
                if browse_name.Name == "EngineeringUnits":
                    eu_value = await prop.read_value()
                    # EUInformation has a DisplayName field
                    if hasattr(eu_value, 'DisplayName') and eu_value.DisplayName:
                        unit_text = eu_value.DisplayName.Text
                        if unit_text:
                            self._node_units[node_str] = unit_text
                    break
        except Exception:
            # Engineering units are optional; silently skip
            pass

    def disconnect(self) -> None:
        """Disconnect from the OPC-UA server."""
        if self._client:
            try:
                self._run_async(self._async_disconnect())
                logger.info("Disconnected from OPC-UA server")
            except Exception as e:
                logger.warning(f"Error disconnecting from OPC-UA server: {e}")
            finally:
                self._client = None
                self._resolved_nodes = []
                self._subscription = None

        self._set_state(AdapterState.DISCONNECTED)

    async def _async_disconnect(self) -> None:
        """Async implementation of disconnect."""
        if self._subscription:
            try:
                await self._subscription.delete()
            except Exception:
                pass
            self._subscription = None

        if self._client:
            await self._client.disconnect()

    def poll(self) -> List[Metric]:
        """
        Poll all resolved nodes and return their current values as metrics.

        For each resolved node, reads the current value from the OPC-UA server
        and creates a Metric with:
        - name: prefix + node display name (e.g., "opcua.Temperature")
        - value: the current node value
        - tags: includes "unit" if engineering units are available

        Returns:
            List of Metric objects. Empty list if no nodes or not connected.
        """
        if not self._client or not self._resolved_nodes:
            # Also drain any pending metrics from subscription mode
            if self._pending_metrics:
                metrics = self._pending_metrics.copy()
                self._pending_metrics.clear()
                return metrics
            return []

        try:
            return self._run_async(self._async_poll())
        except Exception as e:
            logger.error(f"Error polling OPC-UA nodes: {e}")
            raise ProtocolError(f"OPC-UA poll error: {e}")

    async def _async_poll(self) -> List[Metric]:
        """Async implementation of poll."""
        metrics: List[Metric] = []
        now = time.time()

        for node in self._resolved_nodes:
            try:
                value = await node.read_value()
                node_str = node.nodeid.to_string()
                display_name = self._node_names.get(node_str, node_str)

                # Build metric name
                metric_name = f"{self.prefix}{display_name}"

                # Build tags
                tags: Dict[str, str] = {
                    "node_id": node_str,
                }
                unit = self._node_units.get(node_str)
                if unit:
                    tags["unit"] = unit

                # Coerce value to a type Metric supports
                coerced = self._coerce_value(value)
                if coerced is not None:
                    metrics.append(
                        Metric(
                            name=metric_name,
                            value=coerced,
                            timestamp=now,
                            tags=tags,
                            source_id=self._source_id,
                        )
                    )

            except Exception as e:
                logger.debug(f"Error reading node {node}: {e}")

        return metrics

    def _coerce_value(self, value: Any) -> Any:
        """
        Coerce an OPC-UA value to a type that Metric supports.

        Handles numeric types, booleans, strings, lists, and dicts.
        Returns None for unsupported types.
        """
        if isinstance(value, (int, float, bool, str)):
            return value
        if isinstance(value, (list, dict)):
            return value
        # numpy-like or OPC-UA numeric variant types
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
        try:
            return str(value)
        except Exception:
            return None

    def _run_loop(self) -> None:
        """
        Run loop using OPC-UA subscription-based mode.

        Instead of polling, this creates an OPC-UA subscription that monitors
        all resolved nodes for data changes. When a value changes on the server,
        the subscription callback fires and emits metrics immediately.

        Falls back to polling if subscription setup fails.
        """
        try:
            self._run_async(self._async_subscription_loop())
        except KeyboardInterrupt:
            pass
        except Exception as e:
            logger.warning(
                f"Subscription mode failed ({e}), falling back to polling"
            )
            self._polling_fallback()
        finally:
            self.disconnect()

    async def _async_subscription_loop(self) -> None:
        """Async implementation of subscription-based run loop."""
        if not self._client or not self._resolved_nodes:
            return

        # Create subscription
        handler = _SubscriptionHandler(self)
        self._subscription = await self._client.create_subscription(
            period=int(self.poll_interval * 1000),  # ms
            handler=handler,
        )

        # Subscribe to data changes for all resolved nodes
        handles = await self._subscription.subscribe_data_change(
            self._resolved_nodes
        )
        logger.info(
            f"OPC-UA subscription active with {len(handles)} monitored items"
        )

        # Keep alive until disconnected
        try:
            while self._state == AdapterState.CONNECTED:
                # Drain pending metrics from subscription handler
                if self._pending_metrics:
                    metrics = self._pending_metrics.copy()
                    self._pending_metrics.clear()
                    if metrics:
                        self._emit_data(metrics)
                        self.on_data(metrics)
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass

    def _polling_fallback(self) -> None:
        """Fallback to simple polling if subscriptions are not supported."""
        try:
            while self.is_connected:
                metrics = self.poll()
                if metrics:
                    self._emit_data(metrics)
                    self.on_data(metrics)
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            pass

    @property
    def stats(self) -> Dict[str, Any]:
        """Get adapter statistics including OPC-UA-specific info."""
        base_stats = super().stats
        base_stats.update({
            "endpoint": self.endpoint,
            "namespace": self.namespace,
            "resolved_nodes": len(self._resolved_nodes),
            "has_subscription": self._subscription is not None,
            "poll_interval": self.poll_interval,
        })
        return base_stats


class _SubscriptionHandler:
    """
    Internal handler for OPC-UA subscription data change notifications.

    When a monitored node's value changes, the OPC-UA client library calls
    datachange_notification() on this handler. We convert the notification
    into a Metric and append it to the adapter's pending metrics list.
    """

    def __init__(self, adapter: OPCUAAdapter):
        self._adapter = adapter

    def datachange_notification(self, node, val, data) -> None:
        """Handle a data change notification from the OPC-UA subscription."""
        try:
            node_str = node.nodeid.to_string()
            display_name = self._adapter._node_names.get(node_str, node_str)
            metric_name = f"{self._adapter.prefix}{display_name}"

            tags: Dict[str, str] = {"node_id": node_str}
            unit = self._adapter._node_units.get(node_str)
            if unit:
                tags["unit"] = unit

            coerced = self._adapter._coerce_value(val)
            if coerced is not None:
                metric = Metric(
                    name=metric_name,
                    value=coerced,
                    timestamp=time.time(),
                    tags=tags,
                    source_id=self._adapter._source_id,
                )
                self._adapter._pending_metrics.append(metric)

        except Exception as e:
            logger.debug(f"Error in subscription handler: {e}")


# Register the adapter
AdapterRegistry.register(
    "opcua",
    OPCUAAdapter,
    description="OPC-UA client adapter for industrial automation servers",
    author="Plexus",
    version="1.0.0",
    requires=["asyncua"],
)
