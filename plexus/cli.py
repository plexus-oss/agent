"""
Command-line interface for Plexus Agent.

Simplified CLI - all device control happens through the web UI.

Usage:
    plexus run                     # Start the agent
    plexus pair                    # Pair device with web dashboard
    plexus status                  # Check connection status
    plexus scan                    # List detected sensors
"""

import sys
import time
import threading
from typing import Optional, Callable

import click

from plexus import __version__
from plexus.client import Plexus, AuthenticationError, PlexusError
from plexus.config import (
    load_config,
    save_config,
    get_api_key,
    get_device_token,
    get_endpoint,
    get_source_id,
    get_config_path,
    is_logged_in,
)


# ─────────────────────────────────────────────────────────────────────────────
# Console Styling
# ─────────────────────────────────────────────────────────────────────────────

class Style:
    """Consistent styling for CLI output."""

    # Colors
    SUCCESS = "green"
    ERROR = "red"
    WARNING = "yellow"
    INFO = "cyan"
    DIM = "bright_black"

    # Symbols
    CHECK = "✓"
    CROSS = "✗"
    BULLET = "•"
    ARROW = "→"

    # Layout
    WIDTH = 45
    INDENT = "  "

    # Spinner frames
    SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def header(title: str):
    """Print a styled header box."""
    click.echo()
    click.secho(f"  ┌{'─' * (Style.WIDTH - 2)}┐", fg=Style.DIM)
    click.secho(f"  │  {title:<{Style.WIDTH - 6}}│", fg=Style.DIM)
    click.secho(f"  └{'─' * (Style.WIDTH - 2)}┘", fg=Style.DIM)
    click.echo()


def divider():
    """Print a subtle divider."""
    click.secho(f"  {'─' * (Style.WIDTH - 2)}", fg=Style.DIM)


def success(msg: str):
    """Print a success message."""
    click.secho(f"  {Style.CHECK} {msg}", fg=Style.SUCCESS)


def error(msg: str):
    """Print an error message."""
    click.secho(f"  {Style.CROSS} {msg}", fg=Style.ERROR)


def warning(msg: str):
    """Print a warning message."""
    click.secho(f"  {Style.BULLET} {msg}", fg=Style.WARNING)


def info(msg: str):
    """Print an info message."""
    click.echo(f"  {msg}")


def dim(msg: str):
    """Print dimmed text."""
    click.secho(f"  {msg}", fg=Style.DIM)


def label(key: str, value: str, key_width: int = 12):
    """Print a key-value pair."""
    click.echo(f"  {key:<{key_width}} {value}")


def hint(msg: str):
    """Print a hint/help message."""
    click.secho(f"  {msg}", fg=Style.INFO)


class Spinner:
    """Animated spinner for long-running operations."""

    def __init__(self, message: str):
        self.message = message
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.frame = 0

    def _spin(self):
        while self.running:
            frame = Style.SPINNER[self.frame % len(Style.SPINNER)]
            click.echo(f"\r  {frame} {self.message}", nl=False)
            self.frame += 1
            time.sleep(0.08)

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()

    def stop(self, final_message: str = None, success_status: bool = True):
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.2)
        # Clear the line
        click.echo(f"\r{' ' * (Style.WIDTH + 10)}\r", nl=False)
        if final_message:
            if success_status:
                success(final_message)
            else:
                error(final_message)

    def update(self, message: str):
        self.message = message


def status_line(msg: str):
    """Print a timestamped status line."""
    timestamp = time.strftime("%H:%M:%S")
    click.secho(f"  {timestamp}", fg=Style.DIM, nl=False)
    click.echo(f"  {msg}")


@click.group()
@click.version_option(version=__version__, prog_name="plexus")
def main():
    """
    Plexus Agent - Connect your hardware to Plexus.

    Quick start:

        plexus pair                    # Pair with dashboard (one-time)
        plexus run                     # Start the agent

    All device control happens through the web dashboard at:
    https://app.plexus.company
    """
    pass


@main.command()
@click.option("--name", "-n", help="Device name for identification")
@click.option("--no-sensors", is_flag=True, help="Disable sensor auto-detection")
@click.option("--no-cameras", is_flag=True, help="Disable camera auto-detection")
@click.option("--bus", "-b", default=1, type=int, help="I2C bus number for sensors")
def run(name: Optional[str], no_sensors: bool, no_cameras: bool, bus: int):
    """
    Start the Plexus agent.

    Connects to Plexus and waits for commands from the web dashboard.
    All device control (streaming, sessions, commands) happens through the UI.

    Press Ctrl+C to stop.

    Examples:

        plexus run                     # Start the agent
        plexus run --name "robot-01"   # With custom name
        plexus run --no-sensors        # Without sensor detection
        plexus run --no-cameras        # Without camera detection
    """
    from plexus.connector import run_connector
    import socket
    import platform
    import requests

    device_token = get_device_token()
    api_key = get_api_key()

    if not device_token and not api_key:
        header("Plexus Agent")
        warning("Not paired yet")
        click.echo()
        hint("Set PLEXUS_API_KEY environment variable or run 'plexus pair'")
        click.echo()
        sys.exit(1)

    endpoint = get_endpoint()

    # Auto-register if we have an API key but no device token
    if api_key and not device_token:
        header("Plexus Agent")
        info("Auto-registering device...")

        try:
            # Get device info for registration
            hostname = socket.gethostname()
            platform_info = f"{platform.system()} {platform.machine()}"

            # Call registration endpoint
            response = requests.post(
                f"{endpoint}/api/sources/register",
                headers={
                    "x-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "name": name,
                    "hostname": hostname,
                    "platform": platform_info,
                },
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                device_token = data["device_token"]
                source_id = data["source_id"]
                org_id = data.get("org_id")

                # Save credentials
                config = load_config()
                config["device_token"] = device_token
                config["source_id"] = source_id
                if org_id:
                    config["org_id"] = org_id
                save_config(config)

                if data.get("existing"):
                    success(f"Reconnected as {source_id}")
                else:
                    success(f"Registered as {source_id}")
                click.echo()
            else:
                error_msg = response.json().get("error", "Registration failed")
                error(f"Registration failed: {error_msg}")
                sys.exit(1)

        except requests.exceptions.RequestException as e:
            error(f"Could not connect to {endpoint}: {e}")
            sys.exit(1)
        except Exception as e:
            error(f"Registration error: {e}")
            sys.exit(1)
    else:
        header("Plexus Agent")

    source_id = get_source_id()

    # Update source name if provided
    if name:
        config = load_config()
        config["source_name"] = name
        save_config(config)

    header("Plexus Agent")

    label("Source", name or source_id)
    label("Endpoint", endpoint)

    # Auto-detect sensors
    sensor_hub = None
    if not no_sensors:
        try:
            from plexus.sensors import scan_sensors, auto_sensors
            sensors = scan_sensors(bus)
            if sensors:
                sensor_hub = auto_sensors(bus=bus)
                label("Sensors", f"{len(sensors)} detected")
                for s in sensors:
                    dim(f"             {Style.BULLET} {s.name}")
            else:
                label("Sensors", "None detected")
        except ImportError:
            dim("Sensors      Not available")
        except Exception as e:
            dim(f"Sensors      Error: {e}")

    # Auto-detect cameras
    camera_hub = None
    if not no_cameras:
        try:
            from plexus.cameras import scan_cameras, auto_cameras
            cameras = scan_cameras()
            if cameras:
                camera_hub = auto_cameras()
                label("Cameras", f"{len(cameras)} detected")
                for c in cameras:
                    dim(f"             {Style.BULLET} {c.name}")
            else:
                label("Cameras", "None detected")
        except ImportError:
            dim("Cameras      Not available")
        except Exception as e:
            dim(f"Cameras      Error: {e}")

    # Auto-detect CAN interfaces
    can_adapters = None
    try:
        from plexus.adapters.can_detect import scan_can
        detected_can = scan_can()
        up_can = [c for c in detected_can if c.is_up]
        down_can = [c for c in detected_can if not c.is_up]

        if up_can:
            can_adapters = up_can
            label("CAN", f"{len(up_can)} interface{'s' if len(up_can) != 1 else ''} active")
            for c in up_can:
                bitrate_str = f" ({c.bitrate} bps)" if c.bitrate else ""
                dim(f"             {Style.BULLET} {c.channel}{bitrate_str}")
        elif down_can:
            label("CAN", f"{len(down_can)} found (not configured)")
            for c in down_can:
                dim(f"             {Style.BULLET} {c.channel} (down)")
            click.secho(
                f"             Run: plexus scan --setup",
                fg=Style.INFO,
            )
        else:
            label("CAN", "None detected")
    except Exception as e:
        dim(f"CAN          Error: {e}")

    click.echo()
    divider()
    click.echo()

    try:
        run_connector(
            api_key=api_key,
            device_token=device_token,
            endpoint=endpoint,
            on_status=status_line,
            sensor_hub=sensor_hub,
            camera_hub=camera_hub,
            can_adapters=can_adapters,
        )
    except KeyboardInterrupt:
        click.echo()
        status_line("Disconnected")
        click.echo()


@main.command()
@click.option("--code", "-c", help="Pairing code from dashboard (if you have one)")
def pair(code: Optional[str]):
    """
    Pair this device with your Plexus account.

    Opens your browser to complete pairing, or enter a code from the dashboard.
    This is a one-time setup - after pairing, just run 'plexus run'.

    Two ways to pair:

    1. From dashboard (recommended):
       - Go to app.plexus.company/fleet
       - Click "Add Device"
       - Run: plexus pair --code ABC123

    2. Direct login:
       - Run: plexus pair
       - Opens browser to sign in

    Examples:

        plexus pair                    # Opens browser to sign in
        plexus pair --code ABC123      # Use code from dashboard
    """
    import webbrowser

    base_endpoint = "https://app.plexus.company"

    header("Device Pairing")

    if code:
        # ─────────────────────────────────────────────────────────────────────
        # Code-based pairing (from dashboard)
        # ─────────────────────────────────────────────────────────────────────
        info(f"Code: {code.upper().strip()}")
        click.echo()

        spinner = Spinner("Connecting to Plexus...")
        spinner.start()

        try:
            import requests
            response = requests.post(
                f"{base_endpoint}/api/sources/pair/complete",
                headers={"Content-Type": "application/json"},
                json={"code": code.upper().strip()},
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                device_token = data.get("device_token")
                source_id = data.get("source_id")

                if device_token:
                    config = load_config()
                    config["device_token"] = device_token

                    if source_id:
                        config["source_id"] = source_id
                    elif not config.get("source_id"):
                        import uuid
                        config["source_id"] = f"source-{uuid.uuid4().hex[:8]}"

                    if data.get("org_id"):
                        config["org_id"] = data["org_id"]
                    if data.get("source_name"):
                        config["source_name"] = data["source_name"]
                    config["endpoint"] = data.get("endpoint", base_endpoint)

                    save_config(config)

                    spinner.stop("Paired successfully!", success_status=True)
                    click.echo()
                    hint("Start the agent with: plexus run")
                    click.echo()
                    return
                else:
                    spinner.stop("No device token returned", success_status=False)
                    sys.exit(1)

            elif response.status_code == 404:
                spinner.stop("Invalid or expired code", success_status=False)
                click.echo()
                dim("Get a new code from the dashboard:")
                hint("https://app.plexus.company/fleet")
                click.echo()
                sys.exit(1)

            elif response.status_code == 410:
                spinner.stop("Code has already been used", success_status=False)
                click.echo()
                dim("Get a new code from the dashboard:")
                hint("https://app.plexus.company/fleet")
                click.echo()
                sys.exit(1)

            else:
                spinner.stop(f"Pairing failed: {response.text}", success_status=False)
                sys.exit(1)

        except Exception as e:
            spinner.stop(f"Error: {e}", success_status=False)
            sys.exit(1)

    else:
        # ─────────────────────────────────────────────────────────────────────
        # OAuth device flow
        # ─────────────────────────────────────────────────────────────────────
        spinner = Spinner("Requesting authorization...")
        spinner.start()

        try:
            import requests
            response = requests.post(
                f"{base_endpoint}/api/auth/device",
                headers={"Content-Type": "application/json"},
                timeout=10,
            )

            if response.status_code != 200:
                spinner.stop(f"Failed to start pairing: {response.text}", success_status=False)
                sys.exit(1)

            data = response.json()
            device_code = data["device_code"]
            user_code = data["user_code"]
            verification_url = data["verification_uri_complete"]
            interval = data.get("interval", 5)
            expires_in = data.get("expires_in", 900)

            spinner.stop()

        except Exception as e:
            spinner.stop(f"Error: {e}", success_status=False)
            sys.exit(1)

        # Display the code prominently
        click.echo()
        click.secho(f"  Your code:  ", fg=Style.DIM, nl=False)
        click.secho(user_code, fg=Style.INFO, bold=True)
        click.echo()

        webbrowser.open(verification_url)

        dim("Browser opened. If not, visit:")
        hint(verification_url)
        click.echo()
        dim("No account? Sign up from the browser.")
        click.echo()
        divider()
        click.echo()

        # Poll for token with spinner
        spinner = Spinner("Waiting for authorization...")
        spinner.start()

        start_time = time.time()
        max_wait = expires_in

        while time.time() - start_time < max_wait:
            time.sleep(interval)
            elapsed = int(time.time() - start_time)
            spinner.update(f"Waiting for authorization... ({elapsed}s)")

            try:
                import requests
                poll_response = requests.get(
                    f"{base_endpoint}/api/auth/device",
                    params={"device_code": device_code},
                    timeout=10,
                )

                if poll_response.status_code == 200:
                    token_data = poll_response.json()
                    api_key = token_data.get("api_key")

                    if api_key:
                        config = load_config()
                        config["api_key"] = api_key

                        if not config.get("source_id"):
                            import uuid
                            config["source_id"] = f"source-{uuid.uuid4().hex[:8]}"

                        save_config(config)

                        spinner.stop("Paired successfully!", success_status=True)
                        click.echo()
                        hint("Start the agent with: plexus run")
                        click.echo()
                        return

                elif poll_response.status_code == 202:
                    continue

                elif poll_response.status_code == 403:
                    spinner.stop("Authorization was denied", success_status=False)
                    sys.exit(1)

                elif poll_response.status_code == 400:
                    err = poll_response.json().get("error", "")
                    if err == "expired_token":
                        spinner.stop("Authorization expired", success_status=False)
                        click.echo()
                        hint("Try again: plexus pair")
                        click.echo()
                        sys.exit(1)

            except Exception:
                continue

        spinner.stop("Timed out waiting for authorization", success_status=False)
        click.echo()
        hint("Try again: plexus pair")
        click.echo()
        sys.exit(1)


@main.command()
def status():
    """
    Check connection status and configuration.

    Shows whether the device is paired and can connect to Plexus.
    """
    device_token = get_device_token()
    api_key = get_api_key()
    source_id = get_source_id()
    config = load_config()
    source_name = config.get("source_name")

    header("Agent Status")

    label("Config", str(get_config_path()))
    label("Source ID", source_id or "Not set")
    if source_name:
        label("Name", source_name)
    label("Endpoint", get_endpoint())

    if device_token:
        masked = device_token[:12] + "..." if len(device_token) > 12 else "****"
        label("Auth", f"{masked} (device token)")
        click.echo()
        divider()
        click.echo()
        success("Paired")
        click.echo()
        hint("Ready to run: plexus run")
        click.echo()

    elif api_key:
        masked = api_key[:12] + "..." if len(api_key) > 12 else "****"
        label("Auth", f"{masked} (API key)")
        click.echo()
        divider()
        click.echo()

        spinner = Spinner("Testing connection...")
        spinner.start()

        try:
            px = Plexus()
            px.send("plexus.agent.status", 1, tags={"event": "status_check"})
            spinner.stop("Connected", success_status=True)
            click.echo()
            hint("Ready to run: plexus run")
            click.echo()
        except AuthenticationError:
            spinner.stop("Auth failed", success_status=False)
            click.echo()
            hint("Re-pair with: plexus pair")
            click.echo()
        except PlexusError:
            spinner.stop("Connection failed", success_status=False)
            click.echo()

    else:
        label("Auth", "Not configured")
        click.echo()
        divider()
        click.echo()
        warning("Not paired yet")
        click.echo()
        hint("Run 'plexus pair' to connect this device")
        click.echo()


@main.command()
@click.option("--bus", "-b", default=1, type=int, help="I2C bus number")
@click.option("--all", "-a", "show_all", is_flag=True, help="Show all I2C addresses")
@click.option("--setup", is_flag=True, help="Auto-configure detected interfaces")
def scan(bus: int, show_all: bool, setup: bool):
    """
    Scan for connected sensors and cameras.

    Detects sensors on the I2C bus and cameras connected to the device.

    Examples:

        plexus scan                    # Scan for sensors and cameras
        plexus scan -b 0               # Scan different I2C bus
        plexus scan --all              # Show all I2C addresses
        plexus scan --setup            # Auto-configure CAN interfaces
    """
    header("Device Scan")

    # Scan for cameras
    info("Cameras")
    try:
        from plexus.cameras import scan_cameras
        detected_cameras = scan_cameras()
        if detected_cameras:
            for c in detected_cameras:
                click.secho(f"    {Style.CHECK} {c.name}", fg=Style.SUCCESS)
                dim(f"      {c.description}")
        else:
            dim("    None detected")
    except ImportError:
        dim("    Not available (install opencv-python)")
    except Exception as e:
        dim(f"    Error: {e}")

    click.echo()

    # Scan for sensors
    info("Sensors")
    try:
        from plexus.sensors import scan_sensors, scan_i2c, get_sensor_info
    except ImportError:
        dim("    Not available (install smbus2)")
        click.echo()
        return

    if show_all:
        try:
            addresses = scan_i2c(bus)
            if addresses:
                dim(f"    I2C devices on bus {bus}:")
                for addr in addresses:
                    info(f"      0x{addr:02X}")
            else:
                dim(f"    No I2C devices on bus {bus}")
        except Exception as e:
            dim(f"    Error: {e}")
        click.echo()
        return

    try:
        sensors = scan_sensors(bus)
        if sensors:
            for s in sensors:
                click.secho(f"    {Style.CHECK} {s.name}", fg=Style.SUCCESS)
                dim(f"      {s.description}")
        else:
            dim("    None detected")
    except Exception as e:
        dim(f"    Error: {e}")

    click.echo()

    # Scan for CAN interfaces
    info("CAN Interfaces")
    try:
        from plexus.adapters.can_detect import scan_can, setup_can, DEFAULT_BITRATE
        detected_can = scan_can()
        if detected_can:
            for c in detected_can:
                if c.is_up:
                    bitrate_str = f", {c.bitrate} bps" if c.bitrate else ""
                    click.secho(
                        f"    {Style.CHECK} {c.channel} (up{bitrate_str})",
                        fg=Style.SUCCESS,
                    )
                elif setup and c.interface == "socketcan":
                    spinner = Spinner(f"Configuring {c.channel}...")
                    spinner.start()
                    ok = setup_can(c)
                    if ok:
                        spinner.stop(f"{c.channel} (up, {DEFAULT_BITRATE} bps)", success_status=True)
                    else:
                        spinner.stop(f"Failed to configure {c.channel} — try manually with sudo", success_status=False)
                else:
                    click.secho(
                        f"    {Style.BULLET} {c.channel} (down)",
                        fg=Style.WARNING,
                    )
                    if c.interface == "socketcan":
                        hint(f"      Run: plexus scan --setup")
                    elif c.interface == "slcan":
                        dim(f"      Serial CAN adapter — configure with slcand")
        else:
            dim("    None detected")
    except Exception as e:
        dim(f"    Error: {e}")

    click.echo()


# Keep 'connect' as hidden alias for backwards compatibility
@main.command(hidden=True)
@click.option("--no-sensors", is_flag=True)
@click.option("--bus", "-b", default=1, type=int)
@click.pass_context
def connect(ctx, no_sensors: bool, bus: int):
    """Alias for 'run' (deprecated, use 'plexus run' instead)."""
    warning("'plexus connect' is deprecated. Use 'plexus run' instead.")
    click.echo()
    ctx.invoke(run, no_sensors=no_sensors, bus=bus)


# Keep 'login' as hidden alias for backwards compatibility
@main.command(hidden=True)
@click.pass_context
def login(ctx):
    """Alias for 'pair' (deprecated, use 'plexus pair' instead)."""
    warning("'plexus login' is deprecated. Use 'plexus pair' instead.")
    click.echo()
    ctx.invoke(pair)


if __name__ == "__main__":
    main()
