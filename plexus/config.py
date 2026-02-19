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

DEFAULT_CONFIG = {
    "api_key": None,
    "source_id": None,
    "org_id": None,
    "source_name": None,
    "endpoint": None,
    "command_allowlist": None,
    "command_denylist": None,
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
    # Environment variable takes precedence
    env_endpoint = os.environ.get("PLEXUS_ENDPOINT")
    if env_endpoint:
        return env_endpoint

    # Check config file (use default if value is None/empty)
    config = load_config()
    return config.get("endpoint") or PLEXUS_ENDPOINT


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


def get_org_id() -> Optional[str]:
    """Get the organization ID from config or environment variable."""
    # Environment variable takes precedence
    env_org = os.environ.get("PLEXUS_ORG_ID")
    if env_org:
        return env_org

    config = load_config()
    return config.get("org_id")


def is_logged_in() -> bool:
    """Check if device is authenticated (has API key)."""
    return get_api_key() is not None


def require_login() -> None:
    """Raise an error if not logged in."""
    if not is_logged_in():
        raise RuntimeError(
            "Not logged in. Run 'plexus pair' to connect your account."
        )


def get_command_allowlist() -> Optional[list]:
    """Get command allowlist from config. If set, only matching commands will execute."""
    config = load_config()
    return config.get("command_allowlist")


def get_command_denylist() -> Optional[list]:
    """Get command denylist from config. Matching commands will be blocked."""
    config = load_config()
    return config.get("command_denylist")
