"""
Command-line interface for Plexus Agent.

Usage:
    plexus login                   # Authenticate via browser (recommended)
    plexus init                    # Set up API key manually
    plexus send temperature 72.5   # Send a single value
    plexus stream temperature      # Stream from stdin
    plexus import data.csv         # Import from CSV file
    plexus mqtt-bridge             # Bridge MQTT to Plexus
    plexus status                  # Check connection
"""

import csv
import sys
import time
from pathlib import Path
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

        plexus login                   # Authenticate via browser

        plexus send temperature 72.5   # Send a value

        plexus stream temperature      # Stream from stdin
    """
    pass


@main.command()
@click.option("--endpoint", default=None, help="API endpoint (default: https://app.plexusaero.space)")
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically")
def login(endpoint: Optional[str], no_browser: bool):
    """
    Authenticate with Plexus via your browser.

    This is the easiest way to connect. It will:
    1. Open your browser to sign in
    2. Automatically save your API key
    3. You're ready to send data!

    Example:

        plexus login

        # Then immediately start sending data:
        plexus send temperature 72.5
    """
    import webbrowser

    base_endpoint = endpoint or "https://app.plexusaero.space"

    click.echo("\nPlexus Login")
    click.echo("─" * 40)

    # Request device code
    click.echo("  Requesting authorization...")

    try:
        import requests
        response = requests.post(
            f"{base_endpoint}/api/auth/device",
            headers={"Content-Type": "application/json"},
            timeout=10,
        )

        if response.status_code != 200:
            click.secho(f"  ✗ Failed to start login: {response.text}", fg="red")
            sys.exit(1)

        data = response.json()
        device_code = data["device_code"]
        user_code = data["user_code"]
        verification_url = data["verification_uri_complete"]
        interval = data.get("interval", 5)
        expires_in = data.get("expires_in", 900)

    except requests.exceptions.ConnectionError:
        click.secho(f"  ✗ Could not connect to {base_endpoint}", fg="red")
        click.echo("\n  Check your internet connection or try:")
        click.echo(f"    plexus login --endpoint http://localhost:3000")
        sys.exit(1)
    except Exception as e:
        click.secho(f"  ✗ Error: {e}", fg="red")
        sys.exit(1)

    click.echo("─" * 40)
    click.echo(f"\n  Your code: {user_code}\n")

    if not no_browser:
        click.echo("  Opening browser...")
        webbrowser.open(verification_url)
        click.echo(f"  If browser doesn't open, visit:")
    else:
        click.echo(f"  Visit this URL to authorize:")

    click.echo(f"  {verification_url}\n")
    click.secho("  No account? You can sign up from the browser.", fg="cyan")
    click.echo("─" * 40)
    click.echo("  Waiting for authorization...")

    # Poll for token
    start_time = time.time()
    max_wait = expires_in

    while time.time() - start_time < max_wait:
        time.sleep(interval)

        try:
            poll_response = requests.get(
                f"{base_endpoint}/api/auth/device",
                params={"device_code": device_code},
                timeout=10,
            )

            if poll_response.status_code == 200:
                # Success!
                token_data = poll_response.json()
                api_key = token_data.get("api_key")
                final_endpoint = token_data.get("endpoint", base_endpoint)

                if api_key:
                    # Save to config
                    config = load_config()
                    config["api_key"] = api_key
                    config["endpoint"] = final_endpoint

                    # Generate device ID if not present
                    if not config.get("device_id"):
                        import uuid
                        config["device_id"] = f"device-{uuid.uuid4().hex[:8]}"

                    save_config(config)

                    click.echo("")
                    click.secho("  ✓ Authenticated successfully!", fg="green")
                    click.echo("─" * 40)
                    click.echo(f"  Config saved to: {get_config_path()}")
                    click.echo(f"  Device ID: {config['device_id']}")
                    click.echo(f"  Endpoint: {final_endpoint}")
                    click.echo("\n  You're all set! Try:")
                    click.echo("    plexus send temperature 72.5")
                    click.echo("    plexus status")
                    return

            elif poll_response.status_code == 202:
                # Still waiting
                elapsed = int(time.time() - start_time)
                click.echo(f"  Waiting... ({elapsed}s)", nl=False)
                click.echo("\r", nl=False)
                continue

            elif poll_response.status_code == 403:
                click.echo("")
                click.secho("  ✗ Authorization was denied", fg="red")
                sys.exit(1)

            elif poll_response.status_code == 400:
                error = poll_response.json().get("error", "")
                if error == "expired_token":
                    click.echo("")
                    click.secho("  ✗ Authorization expired. Please try again.", fg="red")
                    sys.exit(1)

        except requests.exceptions.RequestException:
            # Network error, keep trying
            continue

    click.echo("")
    click.secho("  ✗ Timed out waiting for authorization", fg="red")
    click.echo("  Please try again: plexus login")
    sys.exit(1)


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
@click.option("--local", is_flag=True, help="Write to local file instead of cloud")
def send(metric: str, value: float, tag: tuple, timestamp: Optional[float], local: bool):
    """
    Send a single metric value.

    Examples:

        plexus send temperature 72.5

        plexus send motor.rpm 3450 -t motor_id=A1

        plexus send pressure 1013.25 --timestamp 1699900000

        plexus send temperature 72.5 --local  # No API key needed
    """
    # Parse tags
    tags = {}
    for t in tag:
        if "=" in t:
            k, v = t.split("=", 1)
            tags[k] = v
        else:
            click.secho(f"Invalid tag format: {t} (expected key=value)", fg="yellow")

    try:
        px = Plexus(local=local)

        # Show mode on first send
        if px.is_local:
            click.secho("📁 Local mode (no API key)", fg="yellow", err=True)

        px.send(metric, value, timestamp=timestamp, tags=tags if tags else None)

        if px.is_local:
            click.secho(f"✓ Saved {metric}={value} → ~/.plexus/data.jsonl", fg="green")
        else:
            click.secho(f"✓ Sent {metric}={value}", fg="green")

        if tags:
            click.echo(f"  Tags: {tags}")
    except AuthenticationError as e:
        click.secho(f"✗ Authentication error: {e}", fg="red")
        click.echo("  Run 'plexus init' to configure your API key")
        click.echo("  Or use --local to save data locally without an account")
        sys.exit(1)
    except PlexusError as e:
        click.secho(f"✗ Error: {e}", fg="red")
        sys.exit(1)


@main.command()
@click.argument("metric")
@click.option("--rate", "-r", type=float, default=None, help="Max samples per second")
@click.option("--tag", "-t", multiple=True, help="Tag in key=value format")
@click.option("--session", "-s", help="Session ID for grouping data")
@click.option("--local", is_flag=True, help="Write to local file instead of cloud")
def stream(metric: str, rate: Optional[float], tag: tuple, session: Optional[str], local: bool):
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

        # Local mode (no API key needed)
        python read_sensor.py | plexus stream temperature --local
    """
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
        px = Plexus(local=local)

        if px.is_local:
            click.secho("📁 Local mode (no API key)", fg="yellow", err=True)
            click.echo(f"Streaming {metric} to ~/.plexus/data.jsonl... (Ctrl+C to stop)", err=True)
        else:
            click.echo(f"Streaming {metric} to cloud... (Ctrl+C to stop)", err=True)

        context = px.session(session) if session else nullcontext()
        with context:
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
                    click.echo(f"{'Saved' if px.is_local else 'Sent'} {count} samples", err=True)

    except KeyboardInterrupt:
        verb = "Saved" if local or not get_api_key() else "Sent"
        click.echo(f"\nStopped. {verb} {count} samples.", err=True)
    except AuthenticationError as e:
        click.secho(f"Authentication error: {e}", fg="red")
        click.echo("  Run 'plexus init' to configure your API key")
        click.echo("  Or use --local to save data locally without an account")
        sys.exit(1)
    except PlexusError as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@main.command()
def status():
    """
    Check connection status and configuration.
    """
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
@click.option("--auto-configure", "-a", is_flag=True, help="Automatically configure if found")
def discover(auto_configure: bool):
    """
    Discover local Plexus instances on your network.

    Useful for self-hosted setups. Checks common local addresses
    for a running Plexus server.

    Examples:

        # Just discover
        plexus discover

        # Discover and auto-configure
        plexus discover --auto-configure
    """
    from plexus.config import discover_local_instance, LOCAL_ENDPOINTS

    click.echo("\nPlexus Discovery")
    click.echo("─" * 40)
    click.echo("  Scanning for local instances...")

    found = None
    for endpoint in LOCAL_ENDPOINTS:
        click.echo(f"    Checking {endpoint}...", nl=False)
        try:
            import requests
            response = requests.get(
                f"{endpoint}/api/ingest",
                timeout=1.0,
            )
            if response.status_code in [200, 401, 405]:
                click.secho(" found!", fg="green")
                found = endpoint
                break
            else:
                click.secho(" no", fg="yellow")
        except Exception:
            click.secho(" no", fg="yellow")

    click.echo("─" * 40)

    if found:
        click.secho(f"\n  ✓ Found Plexus at: {found}\n", fg="green")

        if auto_configure:
            config = load_config()
            config["endpoint"] = found
            save_config(config)
            click.echo(f"  Configuration updated!")
            click.echo(f"  Now run: plexus login --endpoint {found}")
        else:
            click.echo(f"  To use this instance, run:")
            click.echo(f"    plexus login --endpoint {found}")
            click.echo(f"\n  Or with --auto-configure to save automatically")
    else:
        click.secho("\n  No local Plexus instance found.\n", fg="yellow")
        click.echo("  To self-host, see: https://docs.plexusaero.space/self-host")
        click.echo("  Or use the cloud: plexus login")


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


# Use 'import_' to avoid Python keyword conflict, but expose as 'import' in CLI
@main.command("import")
@click.argument("file", type=click.Path(exists=True))
@click.option("--session", "-s", help="Session ID to group imported data")
@click.option("--timestamp-col", "-t", default="timestamp", help="Name of timestamp column")
@click.option("--timestamp-format", default="auto", help="Timestamp format (auto, unix, unix_ms, iso)")
@click.option("--batch-size", "-b", default=100, type=int, help="Batch size for uploads")
@click.option("--dry-run", is_flag=True, help="Parse file but don't upload")
@click.option("--local", is_flag=True, help="Write to local file instead of cloud")
def import_file(
    file: str,
    session: Optional[str],
    timestamp_col: str,
    timestamp_format: str,
    batch_size: int,
    dry_run: bool,
    local: bool,
):
    """
    Import data from a CSV file.

    The CSV should have a timestamp column and one or more metric columns.
    Each non-timestamp column becomes a metric.

    Examples:

        # Basic import
        plexus import sensor_data.csv

        # With session grouping
        plexus import flight_log.csv -s "flight-001"

        # Custom timestamp column
        plexus import data.csv -t time_ms --timestamp-format unix_ms

        # Preview without uploading
        plexus import data.csv --dry-run

    Supported timestamp formats:

        auto     - Auto-detect (default)
        unix     - Unix seconds (e.g., 1699900000)
        unix_ms  - Unix milliseconds (e.g., 1699900000000)
        iso      - ISO 8601 (e.g., 2024-01-15T10:30:00Z)
    """
    filepath = Path(file)

    # Detect file type
    suffix = filepath.suffix.lower()
    if suffix not in [".csv", ".tsv"]:
        click.secho(f"Unsupported file type: {suffix}. Currently only CSV/TSV supported.", fg="red")
        sys.exit(1)

    delimiter = "\t" if suffix == ".tsv" else ","

    click.echo(f"\nImporting: {filepath.name}")
    click.echo("─" * 40)

    # Read and parse the CSV
    try:
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            # Detect if there's a header
            sample = f.read(4096)
            f.seek(0)

            sniffer = csv.Sniffer()
            has_header = sniffer.has_header(sample)

            if not has_header:
                click.secho("Warning: No header detected. First row will be used as data.", fg="yellow")

            reader = csv.DictReader(f, delimiter=delimiter)
            headers = reader.fieldnames or []

            if not headers:
                click.secho("Error: Could not read CSV headers", fg="red")
                sys.exit(1)

            # Find timestamp column
            ts_col = None
            for col in headers:
                if col.lower() in [timestamp_col.lower(), "timestamp", "time", "ts", "date", "datetime"]:
                    ts_col = col
                    break

            if not ts_col:
                click.secho(f"Warning: No timestamp column found. Using row index.", fg="yellow")

            # Metric columns are all non-timestamp columns
            metric_cols = [h for h in headers if h != ts_col]

            if not metric_cols:
                click.secho("Error: No metric columns found", fg="red")
                sys.exit(1)

            click.echo(f"  Timestamp column: {ts_col or '(none - using index)'}")
            click.echo(f"  Metric columns:   {', '.join(metric_cols)}")
            if session:
                click.echo(f"  Session:          {session}")
            click.echo("─" * 40)

            # Parse rows
            rows = list(reader)
            total_rows = len(rows)
            click.echo(f"  Found {total_rows} rows")

            if dry_run:
                click.secho("\n  --dry-run: No data uploaded", fg="yellow")
                # Show preview
                click.echo("\n  Preview (first 5 rows):")
                for i, row in enumerate(rows[:5]):
                    ts = _parse_timestamp(row.get(ts_col, ""), timestamp_format, i)
                    metrics = {m: row.get(m, "") for m in metric_cols[:3]}
                    click.echo(f"    {ts:.2f}: {metrics}")
                return

            # Upload/save data
            px = Plexus(local=local)

            if px.is_local:
                click.secho("\n📁 Local mode (no API key)", fg="yellow")
                click.echo("  Saving to ~/.plexus/data.jsonl...")
            else:
                click.echo("\n  Uploading to cloud...")

            # Use session context if provided
            context = px.session(session) if session else nullcontext()

            with context:
                batch = []
                uploaded = 0
                errors = 0

                with click.progressbar(rows, label="  Progress") as bar:
                    for i, row in enumerate(bar):
                        try:
                            ts = _parse_timestamp(row.get(ts_col, ""), timestamp_format, i)

                            for metric in metric_cols:
                                val_str = row.get(metric, "").strip()
                                if not val_str:
                                    continue
                                try:
                                    value = float(val_str)
                                    batch.append((metric, value, ts))
                                except ValueError:
                                    continue  # Skip non-numeric values

                            # Send batch
                            if len(batch) >= batch_size:
                                _send_batch(px, batch)
                                uploaded += len(batch)
                                batch = []

                        except Exception as e:
                            errors += 1
                            if errors <= 5:
                                click.echo(f"\n  Row {i} error: {e}", err=True)

                # Send remaining
                if batch:
                    _send_batch(px, batch)
                    uploaded += len(batch)

            click.echo("─" * 40)
            verb = "Saved" if px.is_local else "Uploaded"
            click.secho(f"  ✓ {verb} {uploaded} data points", fg="green")
            if errors:
                click.secho(f"  ⚠ {errors} rows had errors", fg="yellow")
            if session and not px.is_local:
                click.echo(f"\n  View session: {get_endpoint()}/sessions/{session}")
            if px.is_local:
                click.echo(f"\n  Data saved to: ~/.plexus/data.jsonl")
                click.echo("  To upload later, run 'plexus init' and re-import")

    except FileNotFoundError:
        click.secho(f"File not found: {file}", fg="red")
        sys.exit(1)
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


def _parse_timestamp(value: str, format: str, row_index: int) -> float:
    """Parse a timestamp string into Unix seconds."""
    if not value:
        return time.time() - (row_index * 0.01)  # Fake timestamps 10ms apart

    value = value.strip()

    if format == "unix":
        return float(value)
    elif format == "unix_ms":
        return float(value) / 1000.0
    elif format == "iso":
        from datetime import datetime
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp()
    else:  # auto
        # Try to auto-detect
        try:
            num = float(value)
            # If it's a huge number, probably milliseconds
            if num > 1e12:
                return num / 1000.0
            return num
        except ValueError:
            pass

        # Try ISO format
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.timestamp()
        except ValueError:
            pass

        # Fallback
        return time.time() - (row_index * 0.01)


def _send_batch(px: Plexus, batch: list):
    """Send a batch of (metric, value, timestamp) tuples."""
    for metric, value, ts in batch:
        px.send(metric, value, timestamp=ts)


@main.command("mqtt-bridge")
@click.option("--broker", "-b", default="localhost", help="MQTT broker hostname")
@click.option("--port", "-p", default=1883, type=int, help="MQTT broker port")
@click.option("--topic", "-t", default="#", help="MQTT topic pattern to subscribe to")
@click.option("--username", "-u", help="MQTT username")
@click.option("--password", help="MQTT password")
@click.option("--session", "-s", help="Session ID for all bridged data")
@click.option("--prefix", default="", help="Prefix to strip from topic names")
def mqtt_bridge(
    broker: str,
    port: int,
    topic: str,
    username: Optional[str],
    password: Optional[str],
    session: Optional[str],
    prefix: str,
):
    """
    Bridge MQTT messages to Plexus.

    Subscribes to MQTT topics and forwards numeric values to Plexus.
    Topic names become metric names (e.g., sensors/temp → sensors.temp).

    Examples:

        # Connect to local broker, all topics
        plexus mqtt-bridge

        # Specific broker and topic
        plexus mqtt-bridge -b mqtt.example.com -t "sensors/#"

        # With authentication
        plexus mqtt-bridge -b broker.hivemq.com -u user -p pass

        # Strip topic prefix
        plexus mqtt-bridge -t "home/sensors/#" --prefix "home/"

    Requires: pip install plexus-agent[mqtt]
    """
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        click.secho("MQTT support not installed. Run:", fg="red")
        click.echo("  pip install plexus-agent[mqtt]")
        sys.exit(1)

    api_key = get_api_key()
    if not api_key:
        click.secho("No API key configured. Run 'plexus init' first.", fg="red")
        sys.exit(1)

    click.echo("\nPlexus MQTT Bridge")
    click.echo("─" * 40)
    click.echo(f"  Broker:  {broker}:{port}")
    click.echo(f"  Topic:   {topic}")
    if session:
        click.echo(f"  Session: {session}")
    click.echo("─" * 40)
    click.echo("\n  Press Ctrl+C to stop\n")

    px = Plexus()
    count = [0]  # Use list for closure

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            click.secho("  ✓ Connected to MQTT broker", fg="green")
            client.subscribe(topic)
            click.echo(f"  Subscribed to: {topic}")
        else:
            click.secho(f"  ✗ Connection failed: {rc}", fg="red")

    def on_message(client, userdata, msg):
        try:
            # Convert topic to metric name
            metric = msg.topic
            if prefix and metric.startswith(prefix):
                metric = metric[len(prefix):]
            metric = metric.replace("/", ".")

            # Try to parse value
            payload = msg.payload.decode("utf-8").strip()

            # Handle JSON payloads
            if payload.startswith("{"):
                import json
                data = json.loads(payload)
                # Send each numeric field
                for key, value in data.items():
                    if isinstance(value, (int, float)):
                        full_metric = f"{metric}.{key}" if metric else key
                        px.send(full_metric, value)
                        count[0] += 1
            else:
                # Try as simple numeric value
                value = float(payload)
                px.send(metric, value)
                count[0] += 1

            if count[0] % 100 == 0:
                click.echo(f"  Forwarded {count[0]} messages", err=True)

        except (ValueError, json.JSONDecodeError):
            pass  # Skip non-numeric messages
        except PlexusError as e:
            click.echo(f"  Error sending: {e}", err=True)

    def on_disconnect(client, userdata, rc, properties=None):
        if rc != 0:
            click.secho(f"  Disconnected unexpectedly: {rc}", fg="yellow")

    # Set up MQTT client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    if username:
        client.username_pw_set(username, password)

    try:
        # Use session context if provided
        context = px.session(session) if session else nullcontext()

        with context:
            client.connect(broker, port, keepalive=60)
            client.loop_forever()
    except KeyboardInterrupt:
        click.echo(f"\n  Stopped. Forwarded {count[0]} messages total.")
        client.disconnect()
    except Exception as e:
        click.secho(f"\n  Error: {e}", fg="red")
        sys.exit(1)


# Null context manager for Python 3.8 compatibility
class nullcontext:
    def __enter__(self):
        return None
    def __exit__(self, *args):
        return False


if __name__ == "__main__":
    main()
