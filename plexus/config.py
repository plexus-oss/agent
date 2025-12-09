"""
Configuration management for Plexus Agent.

Config is stored in ~/.plexus/config.json
"""

import json
import os
from pathlib import Path
from typing import Optional, List

CONFIG_DIR = Path.home() / ".plexus"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "api_key": None,
    "endpoint": "https://app.plexusaero.space",
    "device_id": None,
}

# Common local endpoints to check for self-hosted instances
LOCAL_ENDPOINTS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://plexus.local:3000",
    "http://plexus:3000",
]


def get_config_path() -> Path:
    """Get the path to the config file."""
    return CONFIG_FILE


def load_config() -> dict:
    """Load config from file, creating defaults if needed."""
    if not CONFIG_FILE.exists():
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
            # Merge with defaults to handle missing keys
            return {**DEFAULT_CONFIG, **config}
    except (json.JSONDecodeError, IOError):
        return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    """Save config to file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    # Set restrictive permissions (API key is sensitive)
    os.chmod(CONFIG_FILE, 0o600)


def get_api_key() -> Optional[str]:
    """Get API key from config or environment variable."""
    # Environment variable takes precedence
    env_key = os.environ.get("PLEXUS_API_KEY")
    if env_key:
        return env_key

    config = load_config()
    return config.get("api_key")


def get_endpoint() -> str:
    """Get the API endpoint URL."""
    env_endpoint = os.environ.get("PLEXUS_ENDPOINT")
    if env_endpoint:
        return env_endpoint.rstrip("/")

    config = load_config()
    return config.get("endpoint", DEFAULT_CONFIG["endpoint"]).rstrip("/")


def get_device_id() -> Optional[str]:
    """Get the device ID, generating one if not set."""
    config = load_config()
    device_id = config.get("device_id")

    if not device_id:
        import uuid
        device_id = f"device-{uuid.uuid4().hex[:8]}"
        config["device_id"] = device_id
        save_config(config)

    return device_id


def discover_local_instance(timeout: float = 1.0) -> Optional[str]:
    """
    Try to discover a local Plexus instance.

    Checks common local endpoints for a running Plexus server.
    Returns the endpoint URL if found, None otherwise.

    Args:
        timeout: Timeout in seconds for each endpoint check

    Returns:
        The discovered endpoint URL or None
    """
    try:
        import requests
    except ImportError:
        return None

    for endpoint in LOCAL_ENDPOINTS:
        try:
            # Try to hit the health/ingest endpoint
            response = requests.get(
                f"{endpoint}/api/ingest",
                timeout=timeout,
            )
            # Any response (even 405 Method Not Allowed) means server is running
            if response.status_code in [200, 401, 405]:
                return endpoint
        except requests.exceptions.RequestException:
            continue

    return None


def discover_and_configure() -> Optional[str]:
    """
    Discover local instance and update config if found.

    Returns the discovered endpoint or None.
    """
    discovered = discover_local_instance()
    if discovered:
        config = load_config()
        # Only update if no endpoint is configured or it's the default cloud
        if config.get("endpoint") == DEFAULT_CONFIG["endpoint"]:
            config["endpoint"] = discovered
            save_config(config)
    return discovered
