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

PLEXUS_ENDPOINT = "https://app.plexus.company"

DEFAULT_CONFIG = {
    "api_key": None,
    "device_token": None,  # New: device token from pairing
    "device_id": None,
    "org_id": None,
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


def get_device_token() -> Optional[str]:
    """Get device token from config or environment variable."""
    # Environment variable takes precedence
    env_token = os.environ.get("PLEXUS_DEVICE_TOKEN")
    if env_token:
        return env_token

    config = load_config()
    return config.get("device_token")


def get_endpoint() -> str:
    """Get the API endpoint URL."""
    # Environment variable takes precedence
    env_endpoint = os.environ.get("PLEXUS_ENDPOINT")
    if env_endpoint:
        return env_endpoint

    # Check config file
    config = load_config()
    return config.get("endpoint", PLEXUS_ENDPOINT)


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


def get_org_id() -> Optional[str]:
    """Get the organization ID from config or environment variable."""
    # Environment variable takes precedence
    env_org = os.environ.get("PLEXUS_ORG_ID")
    if env_org:
        return env_org

    config = load_config()
    return config.get("org_id")


def is_logged_in() -> bool:
    """Check if device is authenticated (has device token or API key)."""
    return get_device_token() is not None or get_api_key() is not None


def require_login() -> None:
    """Raise an error if not logged in."""
    if not is_logged_in():
        raise RuntimeError(
            "Not logged in. Run 'plexus login' to connect your account."
        )
