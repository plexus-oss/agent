"""
Configuration management for Plexus Agent.

Config is stored in ~/.plexus/config.json
"""

import json
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class RetryConfig:
    """Configuration for retry behavior with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts. Default 3.
        base_delay: Initial delay in seconds before first retry. Default 1.0.
        max_delay: Maximum delay between retries in seconds. Default 30.0.
        exponential_base: Base for exponential backoff calculation. Default 2.
        jitter: Whether to add random jitter to delays. Default True.
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter: bool = True

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for a given retry attempt (0-indexed).

        Uses exponential backoff: delay = base_delay * (exponential_base ** attempt)
        With optional jitter to prevent thundering herd.
        """
        delay = self.base_delay * (self.exponential_base ** attempt)
        delay = min(delay, self.max_delay)

        if self.jitter:
            # Add jitter: random value between 0 and delay
            delay = delay * (0.5 + random.random() * 0.5)

        return delay

CONFIG_DIR = Path.home() / ".plexus"
CONFIG_FILE = CONFIG_DIR / "config.json"

PLEXUS_ENDPOINT = "https://app.plexus.company"
PLEXUS_GATEWAY_URL = "https://plexus-gateway.fly.dev"
PLEXUS_GATEWAY_WS_URL = "wss://plexus-gateway.fly.dev"

DEFAULT_CONFIG = {
    "api_key": None,
    "source_id": None,
    "persistent_buffer": True,
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
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass  # Windows or restricted filesystem
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
    # Environment variable takes precedence
    env_endpoint = os.environ.get("PLEXUS_ENDPOINT")
    if env_endpoint:
        return env_endpoint

    # Check config file (use default if value is None/empty)
    config = load_config()
    return config.get("endpoint") or PLEXUS_ENDPOINT


def get_gateway_url() -> str:
    """Get the ingest gateway base URL (POST /ingest)."""
    env_gateway = os.environ.get("PLEXUS_GATEWAY_URL")
    if env_gateway:
        return env_gateway.rstrip("/")
    config = load_config()
    return (config.get("gateway_url") or PLEXUS_GATEWAY_URL).rstrip("/")


def get_gateway_ws_url() -> str:
    """Get the gateway WebSocket base URL (/ws/device)."""
    env_ws = os.environ.get("PLEXUS_GATEWAY_WS_URL")
    if env_ws:
        return env_ws.rstrip("/")
    config = load_config()
    return (config.get("gateway_ws_url") or PLEXUS_GATEWAY_WS_URL).rstrip("/")


def get_source_id() -> Optional[str]:
    """Get the source ID, generating one if not set."""
    config = load_config()
    source_id = config.get("source_id")

    if not source_id:
        import uuid
        source_id = f"source-{uuid.uuid4().hex[:8]}"
        config["source_id"] = source_id
        save_config(config)

    return source_id


def get_install_id() -> str:
    """Get the device install ID, generating one if not set.

    The install_id is a stable per-installation UUID. It is generated lazily
    on first run (NOT at image-build time) so that cloned SD-card images
    naturally get distinct install_ids on their first boot. The gateway uses
    it to tell "same device reconnecting" from "different device claiming the
    same name" when resolving source_id collisions.

    Resolution order:
      1. ``PLEXUS_INSTALL_ID`` env var — lets ephemeral containers (Fly
         machines, CI runners, Kubernetes pods) pin a stable identity
         across restarts when the config filesystem is ephemeral. Without
         this, every redeploy generates a new install_id and the gateway
         auto-suffixes the source_id to avoid a collision with the prior
         install ("gw-001" → "gw-001_2" → "gw-001_3"…).
      2. ``install_id`` in the on-disk config.
      3. Newly-generated UUID, persisted to config.
    """
    env_id = os.environ.get("PLEXUS_INSTALL_ID", "").strip()
    if env_id:
        return env_id

    config = load_config()
    install_id = config.get("install_id")

    if not install_id:
        import uuid
        install_id = uuid.uuid4().hex
        config["install_id"] = install_id
        save_config(config)

    return install_id


def set_source_id(source_id: str) -> None:
    """Persist an updated source_id to the config file.

    Called by the SDK when the gateway returns an auto-suffixed name so the
    assigned name is stable across reconnects.
    """
    config = load_config()
    if config.get("source_id") == source_id:
        return
    config["source_id"] = source_id
    save_config(config)


def get_persistent_buffer() -> bool:
    """Get persistent buffer setting. Default True (store-and-forward enabled)."""
    config = load_config()
    return config.get("persistent_buffer", True)
