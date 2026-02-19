"""
Lazy dependency management for Plexus optional extras.

Provides helpful error messages when optional packages are missing,
and auto-install support for CLI usage.

Usage in adapter/sensor code:
    from plexus.deps import require

    # In a function that needs an optional dependency:
    smbus2 = require("smbus2", extra="sensors")
    bus = smbus2.SMBus(1)

    # Or check availability without raising:
    if is_available("opencv-python"):
        import cv2

CLI auto-install:
    When running via CLI with --auto-install, missing dependencies
    are installed automatically instead of raising errors.
"""

import importlib
import logging
import subprocess
import sys
from typing import Optional

logger = logging.getLogger(__name__)

# Maps import names to pip package names and the plexus extra that includes them
DEPENDENCY_MAP = {
    # import_name: (pip_package, plexus_extra, description)
    "smbus2": ("smbus2>=0.4.0", "sensors", "I2C sensor communication"),
    "cv2": ("opencv-python>=4.8.0", "camera", "USB webcam support"),
    "numpy": ("numpy>=1.20.0", "camera", "Numerical arrays for camera frames"),
    "picamera2": ("picamera2>=0.3.12", "picamera", "Raspberry Pi Camera Module"),
    "paho": ("paho-mqtt>=1.6.0", "mqtt", "MQTT broker bridging"),
    "can": ("python-can>=4.0.0", "can", "CAN bus interface"),
    "cantools": ("cantools>=39.0.0", "can", "DBC file parsing"),
    "rosbags": ("rosbags>=0.9.0", "ros", "ROS bag file reading"),
    "mcap": ("mcap>=1.0.0", "ros", "MCAP format support"),
    "serial": ("pyserial>=3.5", "serial", "Serial port communication"),
    "rich": ("rich>=13.0.0", "tui", "Rich terminal output"),
    "textual": ("textual>=0.40.0", "tui", "Terminal user interface"),
    "bluetooth": ("pybluez>=0.23", "bluetooth", "Bluetooth device scanning"),
    "psutil": ("psutil>=5.9.0", "system", "System resource monitoring"),
}

# Global flag for CLI auto-install mode
_auto_install_enabled = False


def enable_auto_install():
    """Enable automatic installation of missing dependencies (CLI mode)."""
    global _auto_install_enabled
    _auto_install_enabled = True


def disable_auto_install():
    """Disable automatic installation."""
    global _auto_install_enabled
    _auto_install_enabled = False


def is_available(import_name: str) -> bool:
    """Check if a package is importable without raising."""
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False


def require(import_name: str, extra: Optional[str] = None):
    """
    Import and return a module, with helpful errors and optional auto-install.

    Args:
        import_name: The Python import name (e.g., "smbus2", "cv2")
        extra: The plexus extra that provides this (e.g., "sensors")

    Returns:
        The imported module

    Raises:
        ImportError: With a helpful message including the pip install command
    """
    try:
        return importlib.import_module(import_name)
    except ImportError:
        pass

    # Look up package info
    dep_info = DEPENDENCY_MAP.get(import_name)
    if dep_info:
        pip_package, plexus_extra, description = dep_info
    else:
        pip_package = import_name
        plexus_extra = extra
        description = import_name

    # Try auto-install if enabled
    if _auto_install_enabled:
        if _pip_install(pip_package, description):
            return importlib.import_module(import_name)

    # Build helpful error message
    if plexus_extra:
        msg = (
            f"\n"
            f"  Missing dependency: {import_name} ({description})\n"
            f"\n"
            f"  Install with:\n"
            f"    pip install plexus-agent[{plexus_extra}]\n"
            f"\n"
            f"  Or install directly:\n"
            f"    pip install {pip_package}\n"
        )
    else:
        msg = (
            f"\n"
            f"  Missing dependency: {import_name} ({description})\n"
            f"\n"
            f"  Install with:\n"
            f"    pip install {pip_package}\n"
        )

    raise ImportError(msg)


def prompt_install(import_name: str, extra: Optional[str] = None) -> bool:
    """
    Check if a dependency is available; if not, prompt user to install.

    For CLI interactive use. Returns True if the dependency is available
    (either already installed or just installed).

    Args:
        import_name: The Python import name
        extra: The plexus extra that provides this

    Returns:
        True if the module is now importable
    """
    if is_available(import_name):
        return True

    dep_info = DEPENDENCY_MAP.get(import_name)
    if dep_info:
        pip_package, plexus_extra, description = dep_info
    else:
        pip_package = import_name
        plexus_extra = extra
        description = import_name

    # Auto-install mode: don't prompt
    if _auto_install_enabled:
        return _pip_install(pip_package, description)

    # Interactive prompt
    try:
        import click

        click.echo()
        click.secho(f"  {description} requires '{import_name}' which is not installed.", fg="yellow")
        click.echo()

        if plexus_extra:
            click.echo(f"  Install with: pip install plexus-agent[{plexus_extra}]")
        else:
            click.echo(f"  Install with: pip install {pip_package}")

        click.echo()

        if click.confirm("  Install now?", default=True):
            return _pip_install(pip_package, description)
        return False

    except (ImportError, EOFError):
        return False


def _pip_install(package: str, description: str) -> bool:
    """Install a package via pip. Returns True on success."""
    try:
        import click
        click.secho(f"  Installing {description}...", fg="cyan", nl=False)
    except ImportError:
        pass

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", package],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode == 0:
            try:
                import click
                click.secho(" done", fg="green")
            except ImportError:
                pass
            logger.info(f"Installed {package}")
            return True
        else:
            try:
                import click
                click.secho(" failed", fg="red")
                if result.stderr:
                    click.secho(f"    {result.stderr.strip()[:200]}", fg="bright_black")
            except ImportError:
                pass
            logger.warning(f"Failed to install {package}: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        try:
            import click
            click.secho(" timed out", fg="red")
        except ImportError:
            pass
        return False
    except Exception as e:
        logger.warning(f"pip install failed: {e}")
        return False


def check_extras_for_scan() -> dict:
    """
    Check which optional extras are installed.
    Returns a dict of {extra_name: bool}.
    Used by `plexus doctor` and `plexus scan`.
    """
    extras = {
        "sensors": is_available("smbus2"),
        "camera": is_available("cv2"),
        "picamera": is_available("picamera2"),
        "mqtt": is_available("paho"),
        "can": is_available("can"),
        "ros": is_available("rosbags"),
        "serial": is_available("serial"),
        "tui": is_available("rich"),
    }
    return extras
