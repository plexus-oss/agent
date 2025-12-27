"""
Adapter Registry - Plugin system for protocol adapters

The registry allows dynamic registration of protocol adapters,
enabling community contributions without modifying core code.

Usage:
    from plexus.adapters import AdapterRegistry, ProtocolAdapter

    # Register a custom adapter
    AdapterRegistry.register("my-protocol", MyProtocolAdapter)

    # List available adapters
    print(AdapterRegistry.list())

    # Create an adapter by name
    adapter = AdapterRegistry.create("mqtt", broker="localhost")

    # Check if adapter is available
    if AdapterRegistry.has("modbus"):
        adapter = AdapterRegistry.create("modbus", port="/dev/ttyUSB0")
"""

from typing import Any, Dict, List, Optional, Type
from plexus.adapters.base import ProtocolAdapter


class AdapterRegistryMeta(type):
    """Metaclass for singleton registry"""
    _adapters: Dict[str, Type[ProtocolAdapter]] = {}
    _metadata: Dict[str, Dict[str, Any]] = {}

    def register(
        cls,
        name: str,
        adapter_class: Type[ProtocolAdapter],
        *,
        description: str = "",
        author: str = "",
        version: str = "1.0.0",
        requires: Optional[List[str]] = None,
    ) -> None:
        """
        Register a protocol adapter.

        Args:
            name: Unique adapter name (e.g., "mqtt", "modbus", "can")
            adapter_class: The adapter class (must inherit from ProtocolAdapter)
            description: Human-readable description
            author: Author name or organization
            version: Adapter version
            requires: List of required pip packages

        Example:
            AdapterRegistry.register(
                "modbus",
                ModbusAdapter,
                description="Modbus RTU/TCP adapter",
                requires=["pymodbus"],
            )
        """
        if not issubclass(adapter_class, ProtocolAdapter):
            raise TypeError(
                f"Adapter class must inherit from ProtocolAdapter, "
                f"got {adapter_class.__name__}"
            )

        cls._adapters[name] = adapter_class
        cls._metadata[name] = {
            "name": name,
            "class": adapter_class.__name__,
            "description": description,
            "author": author,
            "version": version,
            "requires": requires or [],
        }

    def unregister(cls, name: str) -> bool:
        """
        Unregister a protocol adapter.

        Args:
            name: Adapter name to remove

        Returns:
            True if adapter was removed, False if not found
        """
        if name in cls._adapters:
            del cls._adapters[name]
            del cls._metadata[name]
            return True
        return False

    def has(cls, name: str) -> bool:
        """Check if an adapter is registered."""
        return name in cls._adapters

    def get(cls, name: str) -> Optional[Type[ProtocolAdapter]]:
        """Get an adapter class by name."""
        return cls._adapters.get(name)

    def create(cls, name: str, **kwargs) -> ProtocolAdapter:
        """
        Create an adapter instance by name.

        Args:
            name: Adapter name
            **kwargs: Arguments passed to adapter constructor

        Returns:
            Configured adapter instance

        Raises:
            KeyError: If adapter not found
            ImportError: If required packages not installed

        Example:
            adapter = AdapterRegistry.create(
                "mqtt",
                broker="localhost",
                topic="sensors/#"
            )
        """
        if name not in cls._adapters:
            available = ", ".join(cls._adapters.keys()) or "none"
            raise KeyError(
                f"Unknown adapter: '{name}'. Available: {available}"
            )

        # Check dependencies
        metadata = cls._metadata[name]
        missing = []
        for pkg in metadata.get("requires", []):
            try:
                __import__(pkg.replace("-", "_"))
            except ImportError:
                missing.append(pkg)

        if missing:
            raise ImportError(
                f"Adapter '{name}' requires: pip install {' '.join(missing)}"
            )

        adapter_class = cls._adapters[name]
        return adapter_class(**kwargs)

    def list(cls) -> List[str]:
        """List all registered adapter names."""
        return list(cls._adapters.keys())

    def info(cls, name: str) -> Optional[Dict[str, Any]]:
        """Get metadata for an adapter."""
        return cls._metadata.get(name)

    def all_info(cls) -> List[Dict[str, Any]]:
        """Get metadata for all registered adapters."""
        return list(cls._metadata.values())


class AdapterRegistry(metaclass=AdapterRegistryMeta):
    """
    Registry for protocol adapters.

    This is a class-level registry (singleton pattern via metaclass).
    All methods are class methods accessed directly on AdapterRegistry.

    Example:
        # Register
        AdapterRegistry.register("my-adapter", MyAdapter)

        # Create
        adapter = AdapterRegistry.create("my-adapter", param=value)

        # List
        print(AdapterRegistry.list())  # ["mqtt", "my-adapter"]
    """
    pass


def register_adapter(
    name: str,
    *,
    description: str = "",
    author: str = "",
    version: str = "1.0.0",
    requires: Optional[List[str]] = None,
):
    """
    Decorator to register an adapter class.

    Usage:
        @register_adapter("my-protocol", description="My custom protocol")
        class MyProtocolAdapter(ProtocolAdapter):
            ...
    """
    def decorator(cls: Type[ProtocolAdapter]) -> Type[ProtocolAdapter]:
        AdapterRegistry.register(
            name,
            cls,
            description=description,
            author=author,
            version=version,
            requires=requires,
        )
        return cls
    return decorator
