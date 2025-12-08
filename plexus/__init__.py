"""
Plexus Agent - Send sensor data to Plexus in one line of code.

Usage:
    from plexus import Plexus

    px = Plexus()
    px.send("temperature", 72.5)
"""

from plexus.client import Plexus
from plexus.config import load_config, save_config

__version__ = "0.1.0"
__all__ = ["Plexus", "load_config", "save_config"]
