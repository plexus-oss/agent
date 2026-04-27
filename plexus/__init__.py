"""
Plexus — thin Python SDK for sending telemetry to the Plexus gateway.

    from plexus import Plexus

    px = Plexus(api_key="plx_xxx", source_id="device-001")
    px.send("temperature", 72.5)
"""

from plexus.client import Plexus
from plexus.ws import WebSocketTransport

__version__ = "0.4.2"
__all__ = ["Plexus", "WebSocketTransport"]
