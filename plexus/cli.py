"""
Command-line interface for Plexus Agent.

Simplified CLI - all device control happens through the web UI.

Usage:
    plexus run                     # Start agent daemon (primary command)
    plexus pair                    # Pair device with web dashboard
    plexus status                  # Check connection status
    plexus scan                    # List detected sensors
"""

import sys
import time
from typing import Optional

import click

from plexus import __version__
from plexus.client import Plexus, AuthenticationError, PlexusError
from plexus.config import (
    load_config,
    save_config,
    get_api_key,
    get_device_token,
    get_endpoint,
    get_device_id,
    get_config_path,
    is_logged_in,
)


@click.group()
@click.version_option(version=__version__, prog_name="plexus")
def main():
    """
    Plexus Agent - Connect your hardware to Plexus.

    Quick start:

        plexus pair                    # Pair with dashboard (one-time)
        plexus run                     # Start agent daemon

    All device control happens through the web dashboard at:
    https://app.plexus.company
    """
    pass


@main.command()
@click.option("--name", "-n", help="Device name for identification")
@click.option("--no-sensors", is_flag=True, help="Disable sensor auto-detection")
@click.option("--bus", "-b", default=1, type=int, help="I2C bus number for sensors")
def run(name: Optional[str], no_sensors: bool, bus: int):
    """
    Start Plexus agent in daemon mode.

    Connects to Plexus and waits for commands from the web dashboard.
    All device control (streaming, sessions, commands) happens through the UI.

    This is the primary way to run the agent. Typically set up as a
    systemd service to start on boot.

    Examples:

        plexus run                     # Start daemon
        plexus run --name "robot-01"   # With custom name
        plexus run --no-sensors        # Without sensor detection
    """
    from plexus.connector import run_connector

    device_token = get_device_token()
    api_key = get_api_key()

    if not device_token and not api_key:
        click.echo()
        click.secho("Not paired yet!", fg="yellow")
        click.echo()
        click.echo("  Run 'plexus pair' to connect this device to your account.")
        click.echo()
        sys.exit(1)

    endpoint = get_endpoint()
    device_id = get_device_id()

    # Update device name if provided
    if name:
        config = load_config()
        config["device_name"] = name
        save_config(config)

    # Clear, minimal startup banner
    click.echo()
    click.echo("┌─────────────────────────────────────────┐")
    click.echo("│  Plexus Agent                           │")
    click.echo("└─────────────────────────────────────────┘")
    click.echo()
    click.echo(f"  Device:    {name or device_id}")
    click.echo(f"  Dashboard: {endpoint}")

    # Auto-detect sensors
    sensor_hub = None
    if not no_sensors:
        try:
            from plexus.sensors import scan_sensors, auto_sensors
            sensors = scan_sensors(bus)
            if sensors:
                sensor_hub = auto_sensors(bus=bus)
                click.echo(f"  Sensors:   {len(sensors)} detected")
                for s in sensors:
                    click.echo(f"             • {s.name}")
            else:
                click.echo("  Sensors:   None detected")
        except ImportError:
            click.echo("  Sensors:   Not available (install smbus2)")
        except Exception as e:
            click.echo(f"  Sensors:   Error ({e})")

    click.echo()
    click.echo("─" * 43)
    click.echo()

    def status_callback(msg: str):
        timestamp = time.strftime("%H:%M:%S")
        click.echo(f"  [{timestamp}] {msg}")

    status_callback("Connecting...")

    try:
        run_connector(
            api_key=api_key,
            device_token=device_token,
            endpoint=endpoint,
            on_status=status_callback,
            sensor_hub=sensor_hub,
        )
    except KeyboardInterrupt:
        click.echo()
        status_callback("Disconnected")
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

    click.echo()
    click.echo("┌─────────────────────────────────────────┐")
    click.echo("│  Plexus Device Pairing                  │")
    click.echo("└─────────────────────────────────────────┘")
    click.echo()

    if code:
        # Pairing with code from dashboard
        click.echo(f"  Pairing with code: {code}")
        click.echo()

        try:
            import requests
            response = requests.post(
                f"{base_endpoint}/api/devices/pair/complete",
                headers={"Content-Type": "application/json"},
                json={"code": code.upper().strip()},
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                device_token = data.get("device_token")
                device_id = data.get("device_id")

                if device_token:
                    # Save credentials
                    config = load_config()
                    config["device_token"] = device_token

                    # Use device_id from server, or generate if not present
                    if device_id:
                        config["device_id"] = device_id
                    elif not config.get("device_id"):
                        import uuid
                        config["device_id"] = f"device-{uuid.uuid4().hex[:8]}"

                    # Store org info if provided
                    if data.get("org_id"):
                        config["org_id"] = data["org_id"]
                    if data.get("device_name"):
                        config["device_name"] = data["device_name"]
                    # Store endpoint for API calls
                    config["endpoint"] = data.get("endpoint", base_endpoint)

                    save_config(config)

                    click.secho("  ✓ Paired successfully!", fg="green")
                    click.echo()
                    click.echo("  Your device is now connected.")
                    click.echo("  Start the agent with:")
                    click.echo()
                    click.echo("    plexus run")
                    click.echo()
                    return
                else:
                    click.secho("  ✗ Pairing failed: No device token returned", fg="red")
                    sys.exit(1)

            elif response.status_code == 404:
                click.secho("  ✗ Invalid or expired code", fg="red")
                click.echo()
                click.echo("  Get a new code from the dashboard:")
                click.echo("  https://app.plexus.company/fleet")
                sys.exit(1)

            elif response.status_code == 410:
                click.secho("  ✗ Code has already been used", fg="red")
                click.echo()
                click.echo("  Get a new code from the dashboard:")
                click.echo("  https://app.plexus.company/fleet")
                sys.exit(1)

            else:
                click.secho(f"  ✗ Pairing failed: {response.text}", fg="red")
                sys.exit(1)

        except Exception as e:
            click.secho(f"  ✗ Error: {e}", fg="red")
            sys.exit(1)

    else:
        # OAuth device flow (existing login logic)
        click.echo("  Requesting authorization...")

        try:
            import requests
            response = requests.post(
                f"{base_endpoint}/api/auth/device",
                headers={"Content-Type": "application/json"},
                timeout=10,
            )

            if response.status_code != 200:
                click.secho(f"  ✗ Failed to start pairing: {response.text}", fg="red")
                sys.exit(1)

            data = response.json()
            device_code = data["device_code"]
            user_code = data["user_code"]
            verification_url = data["verification_uri_complete"]
            interval = data.get("interval", 5)
            expires_in = data.get("expires_in", 900)

        except Exception as e:
            click.secho(f"  ✗ Error: {e}", fg="red")
            sys.exit(1)

        click.echo()
        click.echo(f"  Your code: {user_code}")
        click.echo()
        click.echo("  Opening browser...")
        webbrowser.open(verification_url)
        click.echo()
        click.echo("  If browser doesn't open, visit:")
        click.echo(f"  {verification_url}")
        click.echo()
        click.secho("  No account? Sign up from the browser.", fg="cyan")
        click.echo()
        click.echo("─" * 43)
        click.echo()
        click.echo("  Waiting for authorization...")

        # Poll for token
        start_time = time.time()
        max_wait = expires_in

        while time.time() - start_time < max_wait:
            time.sleep(interval)

            try:
                import requests
                poll_response = requests.get(
                    f"{base_endpoint}/api/auth/device",
                    params={"device_code": device_code},
                    timeout=10,
                )

                if poll_response.status_code == 200:
                    # Success!
                    token_data = poll_response.json()
                    api_key = token_data.get("api_key")

                    if api_key:
                        # Save to config
                        config = load_config()
                        config["api_key"] = api_key

                        # Generate device ID if not present
                        if not config.get("device_id"):
                            import uuid
                            config["device_id"] = f"device-{uuid.uuid4().hex[:8]}"

                        save_config(config)

                        click.echo()
                        click.secho("  ✓ Paired successfully!", fg="green")
                        click.echo()
                        click.echo("  Your device is now connected.")
                        click.echo("  Start the agent with:")
                        click.echo()
                        click.echo("    plexus run")
                        click.echo()
                        return

                elif poll_response.status_code == 202:
                    # Still waiting
                    elapsed = int(time.time() - start_time)
                    click.echo(f"\r  Waiting... ({elapsed}s)  ", nl=False)
                    continue

                elif poll_response.status_code == 403:
                    click.echo()
                    click.secho("  ✗ Authorization was denied", fg="red")
                    sys.exit(1)

                elif poll_response.status_code == 400:
                    error = poll_response.json().get("error", "")
                    if error == "expired_token":
                        click.echo()
                        click.secho("  ✗ Authorization expired. Please try again.", fg="red")
                        sys.exit(1)

            except Exception:
                # Network error, keep trying
                continue

        click.echo()
        click.secho("  ✗ Timed out waiting for authorization", fg="red")
        click.echo("  Please try again: plexus pair")
        sys.exit(1)


@main.command()
def status():
    """
    Check connection status and configuration.

    Shows whether the device is paired and can connect to Plexus.
    """
    device_token = get_device_token()
    api_key = get_api_key()
    device_id = get_device_id()
    config = load_config()
    device_name = config.get("device_name")

    click.echo()
    click.echo("┌─────────────────────────────────────────┐")
    click.echo("│  Plexus Agent Status                    │")
    click.echo("└─────────────────────────────────────────┘")
    click.echo()
    click.echo(f"  Config:    {get_config_path()}")
    click.echo(f"  Device ID: {device_id}")
    if device_name:
        click.echo(f"  Name:      {device_name}")
    click.echo(f"  Endpoint:  {get_endpoint()}")

    if device_token:
        # Show only prefix of device token
        masked = device_token[:12] + "..." if len(device_token) > 12 else "****"
        click.echo(f"  Auth:      {masked} (device token)")
        click.echo()
        click.echo("─" * 43)
        click.echo()
        click.secho("  Status:    ✓ Paired", fg="green")
        click.echo()
        click.echo("  Ready to run: plexus run")
        click.echo()
    elif api_key:
        # Show only prefix of API key (legacy)
        masked = api_key[:12] + "..." if len(api_key) > 12 else "****"
        click.echo(f"  Auth:      {masked} (API key - legacy)")
        click.echo()
        click.echo("─" * 43)
        click.echo()

        # Test connection
        click.echo("  Testing connection...")
        try:
            px = Plexus()
            px.send("plexus.agent.status", 1, tags={"event": "status_check"})
            click.secho("  Status:    ✓ Connected", fg="green")
            click.echo()
            click.echo("  Ready to run: plexus run")
            click.echo()
        except AuthenticationError:
            click.secho("  Status:    ✗ Auth failed", fg="red")
            click.echo()
            click.echo("  Re-pair with: plexus pair")
            click.echo()
        except PlexusError:
            click.secho("  Status:    ✗ Connection failed", fg="yellow")
            click.echo()
    else:
        click.echo("  Auth:      Not configured")
        click.echo()
        click.echo("─" * 43)
        click.echo()
        click.secho("  Not paired yet!", fg="yellow")
        click.echo()
        click.echo("  Run 'plexus pair' to connect this device.")
        click.echo()


@main.command()
@click.option("--bus", "-b", default=1, type=int, help="I2C bus number")
@click.option("--all", "-a", "show_all", is_flag=True, help="Show all I2C addresses")
def scan(bus: int, show_all: bool):
    """
    Scan for connected sensors.

    Detects sensors on the I2C bus and shows what's available.

    Examples:

        plexus scan                    # Scan for known sensors
        plexus scan -b 0               # Scan different I2C bus
        plexus scan --all              # Show all I2C addresses
    """
    try:
        from plexus.sensors import scan_sensors, scan_i2c, get_sensor_info
    except ImportError as e:
        click.echo()
        click.secho("Sensor support not available", fg="yellow")
        click.echo()
        click.echo("  Install with: pip install smbus2")
        click.echo()
        sys.exit(1)

    click.echo()
    click.echo(f"Scanning I2C bus {bus}...")
    click.echo("─" * 43)
    click.echo()

    if show_all:
        # Show all I2C addresses
        try:
            addresses = scan_i2c(bus)
            if addresses:
                click.echo(f"  Found {len(addresses)} I2C device(s):")
                click.echo()
                for addr in addresses:
                    click.echo(f"    0x{addr:02X}")
                click.echo()
            else:
                click.secho("  No I2C devices found", fg="yellow")
                click.echo()
        except Exception as e:
            click.secho(f"  Error scanning: {e}", fg="red")
            click.echo()
        return

    # Scan for known sensors
    try:
        sensors = scan_sensors(bus)

        if sensors:
            click.secho(f"  Found {len(sensors)} sensor(s):", fg="green")
            click.echo()
            for s in sensors:
                click.echo(f"  • {s.name}")
                click.echo(f"    Address: 0x{s.address:02X}")
                click.echo(f"    {s.description}")
                click.echo()
        else:
            click.secho("  No known sensors detected", fg="yellow")
            click.echo()
            click.echo("  Try 'plexus scan --all' to see all I2C addresses")
            click.echo()
            click.echo("  Supported sensors:")
            for name, info in get_sensor_info().items():
                click.echo(f"    • {name}: {info['description']}")
            click.echo()

    except Exception as e:
        click.secho(f"  Error: {e}", fg="red")
        click.echo()
        click.echo("  Make sure I2C is enabled:")
        click.echo("    sudo raspi-config  # Interface Options → I2C")
        click.echo()


# Keep 'connect' as hidden alias for backwards compatibility
@main.command(hidden=True)
@click.option("--no-sensors", is_flag=True)
@click.option("--bus", "-b", default=1, type=int)
@click.pass_context
def connect(ctx, no_sensors: bool, bus: int):
    """Alias for 'run' (deprecated, use 'plexus run' instead)."""
    click.secho("Note: 'plexus connect' is deprecated. Use 'plexus run' instead.", fg="yellow")
    ctx.invoke(run, no_sensors=no_sensors, bus=bus)


# Keep 'login' as hidden alias for backwards compatibility
@main.command(hidden=True)
@click.pass_context
def login(ctx):
    """Alias for 'pair' (deprecated, use 'plexus pair' instead)."""
    click.secho("Note: 'plexus login' is deprecated. Use 'plexus pair' instead.", fg="yellow")
    ctx.invoke(pair)


if __name__ == "__main__":
    main()
