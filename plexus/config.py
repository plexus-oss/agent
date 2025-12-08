"""
Configuration management for Plexus Agent.

Config is stored in ~/.plexus/config.json
"""

import json
import os
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path.home() / ".plexus"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "api_key": None,
    "endpoint": "https://app.plexusaero.space",
    "device_id": None,
}


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
