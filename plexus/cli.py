"""
Command-line interface for Plexus Agent.

Usage:
    plexus init                    # Set up API key
    plexus send temperature 72.5   # Send a single value
    plexus stream temperature      # Stream from stdin
    plexus status                  # Check connection
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
    get_endpoint,
    get_device_id,
    get_config_path,
)


@click.group()
@click.version_option(version=__version__, prog_name="plexus")
def main():
    """
    Plexus Agent - Send sensor data to Plexus.

    Quick start:

        plexus init                    # Set up your API key

        plexus send temperature 72.5   # Send a value

        plexus stream temperature      # Stream from stdin
    """
    pass


@main.command()
@click.option("--api-key", prompt="API Key", hide_input=True, help="Your Plexus API key")
@click.option("--endpoint", default=None, help="API endpoint (default: https://app.plexusaero.space)")
def init(api_key: str, endpoint: Optional[str]):
    """
    Initialize Plexus with your API key.

    Get your API key from https://app.plexusaero.space/settings
    """
    config = load_config()
    config["api_key"] = api_key.strip()

    if endpoint:
        config["endpoint"] = endpoint.strip()

    # Generate device ID if not present
    if not config.get("device_id"):
        import uuid
        config["device_id"] = f"device-{uuid.uuid4().hex[:8]}"

    save_config(config)

    click.echo(f"Config saved to {get_config_path()}")
    click.echo(f"Device ID: {config['device_id']}")

    # Test the connection
    click.echo("\nTesting connection...")
    try:
        px = Plexus(api_key=api_key)
        px.send("plexus.agent.init", 1, tags={"event": "init"})
        click.secho("✓ Connected successfully!\n", fg="green")
        click.echo("You're all set! Try these commands:")
        click.echo("  plexus send temperature 72.5       # Send a single value")
        click.echo("  plexus send motor.rpm 3450 -t id=1 # Send with tags")
        click.echo("  plexus stream sensor_name          # Stream from stdin")
        click.echo("  plexus status                      # Check connection")
        click.echo(f"\nEndpoint: {px.endpoint}")
    except AuthenticationError as e:
        click.secho(f"✗ Authentication failed: {e}", fg="red")
        click.echo("\nCheck that your API key is valid at:")
        click.echo(f"  {config.get('endpoint', 'https://app.plexusaero.space')}/settings?tab=connections")
        sys.exit(1)
    except PlexusError as e:
        click.secho(f"✗ Connection failed: {e}", fg="yellow")
        click.echo("\nYour config is saved. Troubleshooting:")
        click.echo("  • Check your network connection")
        click.echo("  • Verify the endpoint is correct")
        click.echo(f"  • Current endpoint: {config.get('endpoint', 'https://app.plexusaero.space')}")


@main.command()
@click.argument("metric")
@click.argument("value", type=float)
@click.option("--tag", "-t", multiple=True, help="Tag in key=value format")
@click.option("--timestamp", type=float, help="Unix timestamp (default: now)")
def send(metric: str, value: float, tag: tuple, timestamp: Optional[float]):
    """
    Send a single metric value.

    Examples:

        plexus send temperature 72.5

        plexus send motor.rpm 3450 -t motor_id=A1

        plexus send pressure 1013.25 --timestamp 1699900000
    """
    api_key = get_api_key()
    if not api_key:
        click.secho("No API key configured. Run 'plexus init' first.", fg="red")
        sys.exit(1)

    # Parse tags
    tags = {}
    for t in tag:
        if "=" in t:
            k, v = t.split("=", 1)
            tags[k] = v
        else:
            click.secho(f"Invalid tag format: {t} (expected key=value)", fg="yellow")

    try:
        px = Plexus()
        px.send(metric, value, timestamp=timestamp, tags=tags if tags else None)
        click.secho(f"✓ Sent {metric}={value}", fg="green")
        if tags:
            click.echo(f"  Tags: {tags}")
    except AuthenticationError as e:
        click.secho(f"✗ Authentication error: {e}", fg="red")
        click.echo("  Run 'plexus init' to reconfigure your API key")
        sys.exit(1)
    except PlexusError as e:
        click.secho(f"✗ Error: {e}", fg="red")
        sys.exit(1)


@main.command()
@click.argument("metric")
@click.option("--rate", "-r", type=float, default=None, help="Max samples per second")
@click.option("--tag", "-t", multiple=True, help="Tag in key=value format")
@click.option("--session", "-s", help="Session ID for grouping data")
def stream(metric: str, rate: Optional[float], tag: tuple, session: Optional[str]):
    """
    Stream values from stdin.

    Reads numeric values from stdin (one per line) and sends them to Plexus.

    Examples:

        # Stream from a sensor script
        python read_sensor.py | plexus stream temperature

        # Rate-limited to 100 samples/sec
        cat data.txt | plexus stream pressure -r 100

        # With session tracking
        python read_motor.py | plexus stream motor.rpm -s test-001
    """
    api_key = get_api_key()
    if not api_key:
        click.secho("No API key configured. Run 'plexus init' first.", fg="red")
        sys.exit(1)

    # Parse tags
    tags = {}
    for t in tag:
        if "=" in t:
            k, v = t.split("=", 1)
            tags[k] = v

    min_interval = 1.0 / rate if rate else 0
    last_send = 0
    count = 0

    try:
        px = Plexus()

        context = px.session(session) if session else nullcontext()
        with context:
            click.echo(f"Streaming {metric} from stdin... (Ctrl+C to stop)", err=True)

            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue

                try:
                    value = float(line)
                except ValueError:
                    click.echo(f"Skipping non-numeric: {line}", err=True)
                    continue

                # Rate limiting
                now = time.time()
                if min_interval and (now - last_send) < min_interval:
                    time.sleep(min_interval - (now - last_send))

                px.send(metric, value, tags=tags if tags else None)
                count += 1
                last_send = time.time()

                # Progress indicator every 100 samples
                if count % 100 == 0:
                    click.echo(f"Sent {count} samples", err=True)

    except KeyboardInterrupt:
        click.echo(f"\nStopped. Sent {count} samples.", err=True)
    except AuthenticationError as e:
        click.secho(f"Authentication error: {e}", fg="red")
        sys.exit(1)
    except PlexusError as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@main.command()
def status():
    """
    Check connection status and configuration.
    """
    config = load_config()
    api_key = get_api_key()

    click.echo("\nPlexus Agent Status")
    click.echo("─" * 40)
    click.echo(f"  Config:    {get_config_path()}")
    click.echo(f"  Endpoint:  {get_endpoint()}")
    click.echo(f"  Device ID: {get_device_id()}")

    if api_key:
        # Show only prefix of API key
        masked = api_key[:12] + "..." if len(api_key) > 12 else "****"
        click.echo(f"  API Key:   {masked}")
        click.echo("─" * 40)

        # Test connection
        click.echo("  Testing connection...")
        try:
            px = Plexus()
            px.send("plexus.agent.status", 1, tags={"event": "status_check"})
            click.secho("  Status:    ✓ Connected\n", fg="green")
        except AuthenticationError as e:
            click.secho(f"  Status:    ✗ Auth failed - {e}\n", fg="red")
        except PlexusError as e:
            click.secho(f"  Status:    ✗ Connection failed - {e}\n", fg="yellow")
    else:
        click.secho("  API Key:   Not configured", fg="yellow")
        click.echo("─" * 40)
        click.echo("\n  Run 'plexus init' to set up your API key.\n")


@main.command()
def config():
    """
    Show current configuration.
    """
    cfg = load_config()
    click.echo(f"Config file: {get_config_path()}\n")

    for key, value in cfg.items():
        if key == "api_key" and value:
            # Mask API key
            value = value[:8] + "..." + value[-4:] if len(value) > 12 else "****"
        click.echo(f"  {key}: {value}")


@main.command()
def connect():
    """
    Connect to Plexus for remote terminal access.

    This opens a persistent connection to the Plexus server, allowing
    you to run commands on this machine from the web UI.

    Example:

        plexus connect
    """
    from plexus.connector import run_connector

    api_key = get_api_key()
    if not api_key:
        click.secho("No API key configured. Run 'plexus init' first.", fg="red")
        sys.exit(1)

    endpoint = get_endpoint()
    device_id = get_device_id()

    click.echo("\nPlexus Remote Terminal")
    click.echo("─" * 40)
    click.echo(f"  Device ID: {device_id}")
    click.echo(f"  Endpoint:  {endpoint}")
    click.echo("─" * 40)

    def status_callback(msg: str):
        click.echo(f"  {msg}")

    click.echo("\n  Press Ctrl+C to disconnect\n")

    try:
        run_connector(api_key=api_key, endpoint=endpoint, on_status=status_callback)
    except KeyboardInterrupt:
        click.echo("\n  Disconnected.")


# Null context manager for Python 3.8 compatibility
class nullcontext:
    def __enter__(self):
        return None
    def __exit__(self, *args):
        return False


if __name__ == "__main__":
    main()
