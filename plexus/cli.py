"""
Command-line interface for Plexus Agent.

Usage:
    plexus login                   # Authenticate via browser (recommended)
    plexus init                    # Set up API key manually
    plexus send temperature 72.5   # Send a single value
    plexus stream temperature      # Stream from stdin
    plexus import data.csv         # Import from CSV file
    plexus import-bag data.bag     # Import from ROS bag
    plexus mqtt-bridge             # Bridge MQTT to Plexus
    plexus status                  # Check connection
"""

import csv
import sys
import time
from contextlib import nullcontext
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
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically")
def login(no_browser: bool):
    """
    Sign in to Plexus via your browser.

    Opens your browser to sign in (or create an account).
    Your API key is saved automatically.

    Example:

        plexus login
        plexus send temperature 72.5
    """
    import webbrowser

    base_endpoint = "https://app.plexusaero.space"

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
        click.secho("  ✗ Could not connect to Plexus", fg="red")
        click.echo("\n  Check your internet connection and try again.")
        sys.exit(1)
    except Exception as e:
        click.secho(f"  ✗ Error: {e}", fg="red")
        sys.exit(1)

    click.echo("─" * 40)
    click.echo(f"\n  Your code: {user_code}\n")

    if not no_browser:
        click.echo("  Opening browser...")
        webbrowser.open(verification_url)
        click.echo("  If browser doesn't open, visit:")
    else:
        click.echo("  Visit this URL to authorize:")

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

                if api_key:
                    # Save to config
                    config = load_config()
                    config["api_key"] = api_key

                    # Generate device ID if not present
                    if not config.get("device_id"):
                        import uuid
                        config["device_id"] = f"device-{uuid.uuid4().hex[:8]}"

                    save_config(config)

                    click.echo("")
                    click.secho("  ✓ Logged in!", fg="green")
                    click.echo("─" * 40)
                    click.echo("\n  You're all set! Try:")
                    click.echo("    plexus send temperature 72.5")
                    click.echo("\n  View your data at:")
                    click.echo("    https://app.plexusaero.space")
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
def init(api_key: str):
    """
    Set up Plexus with an API key (for CI/CD environments).

    For interactive use, 'plexus login' is easier.
    Get your API key from https://app.plexusaero.space/settings
    """
    config = load_config()
    config["api_key"] = api_key.strip()

    # Generate device ID if not present
    if not config.get("device_id"):
        import uuid
        config["device_id"] = f"device-{uuid.uuid4().hex[:8]}"

    save_config(config)

    # Test the connection
    click.echo("Testing connection...")
    try:
        px = Plexus(api_key=api_key)
        px.send("plexus.agent.init", 1, tags={"event": "init"})
        click.secho("✓ Connected!", fg="green")
    except AuthenticationError as e:
        click.secho(f"✗ Authentication failed: {e}", fg="red")
        click.echo("Check your API key at: https://app.plexusaero.space/settings")
        sys.exit(1)
    except PlexusError as e:
        click.secho(f"✗ Connection failed: {e}", fg="yellow")
        sys.exit(1)


@main.command()
@click.argument("metric")
@click.argument("value", type=float)
@click.option("--tag", "-t", multiple=True, help="Tag in key=value format")
@click.option("--timestamp", type=float, help="Unix timestamp (default: now)")
def send(metric: str, value: float, tag: tuple, timestamp: Optional[float]):
    """
    Send a single metric value.

    If a recording is active (started with 'plexus record'),
    the data is automatically grouped into that recording.

    Examples:

        plexus send temperature 72.5

        plexus send motor.rpm 3450 -t motor_id=A1

        plexus send pressure 1013.25 --timestamp 1699900000
    """
    # Parse tags
    tags = {}
    for t in tag:
        if "=" in t:
            k, v = t.split("=", 1)
            tags[k] = v
        else:
            click.secho(f"Invalid tag format: {t} (expected key=value)", fg="yellow")

    # Check for active recording
    config = load_config()
    active_recording = config.get("active_recording")
    session_id = active_recording.get("name") if active_recording else None

    try:
        px = Plexus()

        # Use recording context if active
        if session_id:
            with px.session(session_id):
                px.send(metric, value, timestamp=timestamp, tags=tags if tags else None)
            click.secho(f"✓ Sent {metric}={value} [recording: {session_id}]", fg="green")
        else:
            px.send(metric, value, timestamp=timestamp, tags=tags if tags else None)
            click.secho(f"✓ Sent {metric}={value}", fg="green")

        if tags:
            click.echo(f"  Tags: {tags}")
    except RuntimeError as e:
        click.secho(f"✗ {e}", fg="red")
        sys.exit(1)
    except AuthenticationError as e:
        click.secho(f"✗ Authentication error: {e}", fg="red")
        click.echo("  Run 'plexus login' to connect your account")
        sys.exit(1)
    except PlexusError as e:
        click.secho(f"✗ Error: {e}", fg="red")
        sys.exit(1)


@main.command()
@click.argument("metric")
@click.option("--rate", "-r", type=float, default=None, help="Max samples per second")
@click.option("--tag", "-t", multiple=True, help="Tag in key=value format")
@click.option("--session", "-s", help="Session ID for grouping data (overrides active recording)")
def stream(metric: str, rate: Optional[float], tag: tuple, session: Optional[str]):
    """
    Stream values from stdin.

    Reads numeric values from stdin (one per line) and sends them to Plexus.
    If a recording is active, data is automatically grouped into it.

    Examples:

        # Stream from a sensor script
        python read_sensor.py | plexus stream temperature

        # Rate-limited to 100 samples/sec
        cat data.txt | plexus stream pressure -r 100

        # With session tracking
        python read_motor.py | plexus stream motor.rpm -s test-001

        # Works with active recordings
        plexus record "test-001"
        python read_sensor.py | plexus stream temperature
        plexus stop
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

    # Use explicit session, or fall back to active recording
    active_session = session
    if not active_session:
        config = load_config()
        active_recording = config.get("active_recording")
        if active_recording:
            active_session = active_recording.get("name")

    try:
        px = Plexus()

        if active_session:
            click.echo(f"Streaming {metric} [recording: {active_session}]... (Ctrl+C to stop)", err=True)
        else:
            click.echo(f"Streaming {metric}... (Ctrl+C to stop)", err=True)

        context = px.session(active_session) if active_session else nullcontext()
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
                    click.echo(f"Sent {count} samples", err=True)

    except KeyboardInterrupt:
        click.echo(f"\nStopped. Sent {count} samples.", err=True)
    except RuntimeError as e:
        click.secho(f"✗ {e}", fg="red")
        sys.exit(1)
    except AuthenticationError as e:
        click.secho(f"✗ Authentication error: {e}", fg="red")
        click.echo("  Run 'plexus login' to connect your account")
        sys.exit(1)
    except PlexusError as e:
        click.secho(f"✗ Error: {e}", fg="red")
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
        click.secho("\n  Not logged in. Run 'plexus login' to connect your account.\n", fg="yellow")


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
        click.secho("Not logged in. Run 'plexus login' to connect your account.", fg="red")
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
def import_file(
    file: str,
    session: Optional[str],
    timestamp_col: str,
    timestamp_format: str,
    batch_size: int,
    dry_run: bool,
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
                click.secho("Warning: No timestamp column found. Using row index.", fg="yellow")

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

            # Upload data
            try:
                px = Plexus()
            except RuntimeError as e:
                click.secho(f"\n✗ {e}", fg="red")
                sys.exit(1)

            click.echo("\n  Uploading...")

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
            click.secho(f"  ✓ Uploaded {uploaded} data points", fg="green")
            if errors:
                click.secho(f"  ⚠ {errors} rows had errors", fg="yellow")
            if session:
                click.echo(f"\n  View session: {get_endpoint()}/sessions/{session}")

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

    Subscribes to MQTT topics and forwards values to Plexus.
    Topic names become metric names (e.g., sensors/temp → sensors.temp).

    Now supports flexible value types:
        - Numbers: 72.5 → sent as number
        - Strings: "RUNNING" → sent as string
        - JSON objects: {"x": 1, "y": 2} → sent as object or expanded
        - Arrays: [1, 2, 3] → sent as array

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
    # Try using the new adapter system
    try:
        from plexus.adapters import MQTTAdapter, AdapterState
        use_adapter = True
    except ImportError:
        use_adapter = False

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

    if use_adapter:
        # Use new adapter system
        try:
            adapter = MQTTAdapter(
                broker=broker,
                port=port,
                topic=topic,
                username=username,
                password=password,
                prefix=prefix,
            )

            def on_data(metrics):
                for m in metrics:
                    px.send(
                        m.name,
                        m.value,
                        timestamp=m.timestamp,
                        tags=m.tags,
                    )
                    count[0] += 1

                if count[0] % 100 == 0:
                    click.echo(f"  Forwarded {count[0]} messages", err=True)

            def on_state_change(state):
                if state == AdapterState.CONNECTED:
                    click.secho("  ✓ Connected to MQTT broker", fg="green")
                    click.echo(f"  Subscribed to: {topic}")
                elif state == AdapterState.ERROR:
                    click.secho(f"  ✗ Error: {adapter.error}", fg="red")
                elif state == AdapterState.RECONNECTING:
                    click.secho("  Reconnecting...", fg="yellow")

            context = px.session(session) if session else nullcontext()
            with context:
                adapter.run(on_data=on_data, on_state_change=on_state_change)

        except KeyboardInterrupt:
            click.echo(f"\n  Stopped. Forwarded {count[0]} messages total.")
        except ImportError as e:
            click.secho(f"MQTT support not installed. Run:", fg="red")
            click.echo("  pip install plexus-agent[mqtt]")
            sys.exit(1)
        except Exception as e:
            click.secho(f"\n  Error: {e}", fg="red")
            sys.exit(1)
    else:
        # Fallback to direct paho-mqtt usage
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            click.secho("MQTT support not installed. Run:", fg="red")
            click.echo("  pip install plexus-agent[mqtt]")
            sys.exit(1)

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
                    # Send each field (now supports any JSON type!)
                    for key, value in data.items():
                        if isinstance(value, (int, float, str, bool, dict, list)):
                            full_metric = f"{metric}.{key}" if metric else key
                            px.send(full_metric, value)
                            count[0] += 1
                elif payload.startswith("["):
                    # Array payload - send as array value
                    import json
                    data = json.loads(payload)
                    px.send(metric, data)
                    count[0] += 1
                else:
                    # Try as numeric value first
                    try:
                        value = float(payload)
                        px.send(metric, value)
                    except ValueError:
                        # Send as string value
                        px.send(metric, payload)
                    count[0] += 1

                if count[0] % 100 == 0:
                    click.echo(f"  Forwarded {count[0]} messages", err=True)

            except (ValueError, json.JSONDecodeError):
                pass  # Skip unparseable messages
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


@main.command("record")
@click.argument("name", required=False)
@click.option("--label", "-l", multiple=True, help="Labels for this recording (can repeat)")
@click.option("--description", "-d", help="Description of the recording")
def record(name: Optional[str], label: tuple, description: Optional[str]):
    """
    Start a new recording session.

    All data sent while recording is grouped together for easy
    playback, labeling, and export.

    Examples:

        # Start recording with auto-generated name
        plexus record

        # Start recording with a name
        plexus record "grasp-attempt-047"

        # Start recording with labels
        plexus record "test-001" -l success -l indoor

        # Then send data (in another terminal or script)
        plexus send motor.torque 1.5
        python my_robot.py | plexus stream robot.state

        # Stop recording
        plexus stop

    The recording can be labeled, played back, and exported from
    the Plexus dashboard.
    """
    import uuid

    # Generate name if not provided
    if not name:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"recording_{timestamp}"

    # Store recording state in config
    config = load_config()
    config["active_recording"] = {
        "name": name,
        "labels": list(label),
        "description": description,
        "started_at": time.time(),
    }
    save_config(config)

    click.echo("\n🔴 Recording Started")
    click.echo("─" * 40)
    click.echo(f"  Name: {name}")
    if label:
        click.echo(f"  Labels: {', '.join(label)}")
    if description:
        click.echo(f"  Description: {description}")
    click.echo("─" * 40)
    click.echo("\n  All data sent will be grouped in this recording.")
    click.echo("  Run 'plexus stop' when finished.\n")

    # Notify API that recording started
    api_key = get_api_key()
    if api_key:
        try:
            import requests
            requests.post(
                f"{get_endpoint()}/api/sessions",
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "session_id": name,
                    "device_id": get_device_id(),
                    "status": "started",
                    "labels": list(label),
                    "description": description,
                    "timestamp": time.time(),
                },
                timeout=5,
            )
        except Exception:
            pass  # Recording tracking is best-effort


@main.command("stop")
@click.option("--label", "-l", multiple=True, help="Add labels to the recording")
@click.option("--notes", "-n", help="Add notes to the recording")
def stop(label: tuple, notes: Optional[str]):
    """
    Stop the current recording.

    Ends the active recording session and optionally adds labels.

    Examples:

        # Stop recording
        plexus stop

        # Stop and add a label
        plexus stop -l success

        # Stop with notes
        plexus stop -n "Good grasp, clean motion"
    """
    config = load_config()
    recording = config.get("active_recording")

    if not recording:
        click.secho("No active recording to stop.", fg="yellow")
        click.echo("Start one with: plexus record")
        return

    name = recording["name"]
    started_at = recording.get("started_at", time.time())
    duration = time.time() - started_at

    # Merge labels
    all_labels = list(recording.get("labels", [])) + list(label)

    # Clear active recording
    del config["active_recording"]
    save_config(config)

    click.echo("\n⏹️  Recording Stopped")
    click.echo("─" * 40)
    click.echo(f"  Name: {name}")
    click.echo(f"  Duration: {duration:.1f} seconds")
    if all_labels:
        click.echo(f"  Labels: {', '.join(all_labels)}")
    if notes:
        click.echo(f"  Notes: {notes}")
    click.echo("─" * 40)

    # Notify API that recording stopped
    api_key = get_api_key()
    if api_key:
        try:
            import requests
            requests.post(
                f"{get_endpoint()}/api/sessions",
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "session_id": name,
                    "device_id": get_device_id(),
                    "status": "ended",
                    "labels": all_labels,
                    "notes": notes,
                    "duration": duration,
                    "timestamp": time.time(),
                },
                timeout=5,
            )
            click.echo(f"\n  View recording: {get_endpoint()}/recordings/{name}\n")
        except Exception:
            pass


@main.command("recordings")
@click.option("--limit", "-n", default=10, type=int, help="Number of recordings to show")
@click.option("--label", "-l", help="Filter by label")
def recordings(limit: int, label: Optional[str]):
    """
    List recent recordings.

    Shows recordings from this device with their labels and duration.

    Examples:

        # Show last 10 recordings
        plexus recordings

        # Show recordings with a specific label
        plexus recordings -l success

        # Show more recordings
        plexus recordings -n 50
    """
    api_key = get_api_key()
    if not api_key:
        click.secho("Not logged in. Run 'plexus login' first.", fg="yellow")
        return

    try:
        import requests
        params = {"limit": limit, "device_id": get_device_id()}
        if label:
            params["label"] = label

        response = requests.get(
            f"{get_endpoint()}/api/sessions",
            headers={"x-api-key": api_key},
            params=params,
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            sessions = data.get("sessions", [])

            if not sessions:
                click.echo("\nNo recordings found.")
                click.echo("Start one with: plexus record")
                return

            click.echo(f"\nRecordings ({len(sessions)})")
            click.echo("─" * 60)

            for s in sessions:
                name = s.get("session_id", "unknown")
                labels = s.get("labels", [])
                duration = s.get("duration", 0)
                status = s.get("status", "unknown")

                # Format duration
                if duration > 3600:
                    dur_str = f"{duration/3600:.1f}h"
                elif duration > 60:
                    dur_str = f"{duration/60:.1f}m"
                else:
                    dur_str = f"{duration:.0f}s"

                # Status icon
                icon = "🔴" if status == "started" else "✓"

                click.echo(f"  {icon} {name} ({dur_str})")
                if labels:
                    click.echo(f"     Labels: {', '.join(labels)}")

            click.echo("─" * 60)
            click.echo(f"\n  View all: {get_endpoint()}/recordings\n")

        elif response.status_code == 401:
            click.secho("Authentication failed. Run 'plexus login' again.", fg="red")
        else:
            click.secho(f"Error: {response.text}", fg="red")

    except requests.exceptions.ConnectionError:
        click.secho("Could not connect to Plexus.", fg="red")
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")


@main.command("import-bag")
@click.argument("file", type=click.Path(exists=True))
@click.option("--session", "-s", help="Session ID to group imported data")
@click.option("--topic", "-t", multiple=True, help="Specific topics to import (can repeat)")
@click.option("--skip-images", is_flag=True, help="Skip image/video topics")
@click.option("--extract-video", is_flag=True, help="Extract video frames to disk")
@click.option("--video-dir", default=None, help="Directory for extracted video frames")
@click.option("--dry-run", is_flag=True, help="Detect schema without importing")
@click.option("--batch-size", "-b", default=100, type=int, help="Batch size for uploads")
def import_bag(
    file: str,
    session: Optional[str],
    topic: tuple,
    skip_images: bool,
    extract_video: bool,
    video_dir: Optional[str],
    dry_run: bool,
    batch_size: int,
):
    """
    Import data from a ROS bag file.

    Supports ROS1 bags (.bag), ROS2 bags (.db3), and MCAP files (.mcap).
    Automatically detects bag format and extracts schema.

    Examples:

        # Basic import
        plexus import-bag robot_data.bag

        # With session grouping
        plexus import-bag flight.bag -s "flight-001"

        # Import specific topics only
        plexus import-bag data.bag -t /imu/data -t /joint_states

        # Preview without uploading
        plexus import-bag data.bag --dry-run

        # Extract video frames
        plexus import-bag data.bag --extract-video --video-dir ./frames

    Requires: pip install plexus-agent[ros]
    """
    try:
        from plexus.importers.rosbag import RosbagImporter
    except ImportError:
        click.secho("ROS bag support not installed. Run:", fg="red")
        click.echo("  pip install plexus-agent[ros]")
        sys.exit(1)

    filepath = Path(file)
    topics_filter = list(topic) if topic else None

    click.echo(f"\nImporting ROS bag: {filepath.name}")
    click.echo("─" * 50)

    try:
        importer = RosbagImporter(
            filepath,
            topics=topics_filter,
            skip_images=skip_images,
        )

        # Detect schema
        click.echo("  Detecting schema...")
        schema = importer.detect_schema()

        click.echo(f"  Bag type:      {schema.bag_type.upper()}")
        click.echo(f"  Duration:      {schema.duration_sec:.2f} seconds")
        click.echo(f"  Total messages: {schema.message_count:,}")
        click.echo(f"  Topics found:  {len(schema.topics)}")

        # Show topics
        click.echo("\n  Topics:")
        for t in schema.topics[:10]:  # Show first 10
            icon = "🎥" if t.is_image else "📊"
            click.echo(f"    {icon} {t.name}")
            click.echo(f"       → {t.plexus_name} ({t.message_count:,} msgs, {t.frequency_hz:.1f} Hz)")

        if len(schema.topics) > 10:
            click.echo(f"    ... and {len(schema.topics) - 10} more topics")

        click.echo("─" * 50)

        if dry_run:
            click.secho("\n  --dry-run: No data uploaded", fg="yellow")

            # Show schema summary
            click.echo("\n  Schema summary (JSON):")
            import json
            click.echo(json.dumps(schema.to_dict(), indent=2)[:1000])
            if len(json.dumps(schema.to_dict())) > 1000:
                click.echo("  ... (truncated)")
            return

        # Upload telemetry
        click.echo("\n  Uploading telemetry...")

        try:
            px = Plexus()
        except RuntimeError as e:
            click.secho(f"\n✗ {e}", fg="red")
            sys.exit(1)

        uploaded = [0]
        total = schema.message_count

        def progress(count, total):
            uploaded[0] = count
            pct = (count / total * 100) if total > 0 else 0
            click.echo(f"\r  Progress: {count:,} / {total:,} ({pct:.1f}%)", nl=False)

        stats = importer.upload_to_plexus(
            px,
            session_id=session,
            batch_size=batch_size,
            progress_callback=progress,
        )

        click.echo("")  # New line after progress
        click.echo("─" * 50)

        click.secho(f"  ✓ Uploaded {stats['metrics_uploaded']:,} metrics", fg="green")

        if stats.get("errors"):
            click.secho(f"  ⚠ {stats['errors']} errors occurred", fg="yellow")

        if session:
            click.echo(f"\n  View session: {get_endpoint()}/sessions/{session}")

        # Extract video if requested
        if extract_video and schema.image_topics:
            click.echo("\n  Extracting video frames...")

            video_output = video_dir or f"./frames_{filepath.stem}"
            video_stats = importer.extract_images(video_output)

            click.secho(
                f"  ✓ Extracted {video_stats['frames_extracted']:,} frames → {video_output}",
                fg="green"
            )

        elif extract_video and not schema.image_topics:
            click.secho("  No image topics found in bag", fg="yellow")

    except FileNotFoundError:
        click.secho(f"File not found: {file}", fg="red")
        sys.exit(1)
    except ImportError as e:
        click.secho(f"Missing dependency: {e}", fg="red")
        click.echo("  Try: pip install plexus-agent[ros]")
        sys.exit(1)
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@main.command("schema")
@click.argument("file", type=click.Path(exists=True))
@click.option("--format", "-f", type=click.Choice(["text", "json", "yaml"]), default="text")
@click.option("--output", "-o", type=click.Path(), help="Write schema to file")
def schema_cmd(file: str, format: str, output: Optional[str]):
    """
    Detect and display schema from a ROS bag or data file.

    Useful for understanding what data is in a bag before importing.

    Examples:

        # Show schema as text
        plexus schema robot_data.bag

        # Export as JSON
        plexus schema data.bag -f json -o schema.json

    Requires: pip install plexus-agent[ros]
    """
    filepath = Path(file)
    suffix = filepath.suffix.lower()

    if suffix in [".bag", ".mcap", ".db3"] or filepath.is_dir():
        # ROS bag
        try:
            from plexus.importers.rosbag import RosbagImporter
        except ImportError:
            click.secho("ROS bag support not installed. Run:", fg="red")
            click.echo("  pip install plexus-agent[ros]")
            sys.exit(1)

        try:
            importer = RosbagImporter(filepath)
            schema = importer.detect_schema()
            schema_dict = schema.to_dict()
        except Exception as e:
            click.secho(f"Error reading bag: {e}", fg="red")
            sys.exit(1)

    elif suffix in [".csv", ".tsv"]:
        # CSV file - detect columns as schema
        delimiter = "\t" if suffix == ".tsv" else ","
        try:
            with open(filepath, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f, delimiter=delimiter)
                headers = reader.fieldnames or []
                row_count = sum(1 for _ in reader)

            schema_dict = {
                "file_path": str(filepath),
                "file_type": "csv" if suffix == ".csv" else "tsv",
                "columns": headers,
                "row_count": row_count,
            }
        except Exception as e:
            click.secho(f"Error reading file: {e}", fg="red")
            sys.exit(1)

    else:
        click.secho(f"Unsupported file type: {suffix}", fg="red")
        click.echo("Supported: .bag, .mcap, .db3, .csv, .tsv")
        sys.exit(1)

    # Format output
    if format == "json":
        import json
        output_str = json.dumps(schema_dict, indent=2)
    elif format == "yaml":
        try:
            import yaml
            output_str = yaml.dump(schema_dict, default_flow_style=False)
        except ImportError:
            click.secho("YAML output requires PyYAML: pip install pyyaml", fg="red")
            sys.exit(1)
    else:
        # Text format
        lines = [f"\nSchema: {filepath.name}", "─" * 50]

        if "bag_type" in schema_dict:
            lines.append(f"  Type:     {schema_dict['bag_type'].upper()} bag")
            lines.append(f"  Duration: {schema_dict['duration_sec']:.2f}s")
            lines.append(f"  Messages: {schema_dict['message_count']:,}")
            lines.append(f"\n  Topics ({len(schema_dict['topics'])}):")

            for t in schema_dict["topics"]:
                icon = "🎥" if t["is_image"] else "📊"
                lines.append(f"    {icon} {t['name']}")
                lines.append(f"       Type: {t['message_type']}")
                lines.append(f"       → {t['plexus_name']}")
                lines.append(f"       {t['message_count']:,} msgs @ {t['frequency_hz']:.1f} Hz")
        else:
            lines.append(f"  Type:    {schema_dict.get('file_type', 'unknown')}")
            lines.append(f"  Rows:    {schema_dict.get('row_count', 'unknown'):,}")
            lines.append(f"\n  Columns ({len(schema_dict.get('columns', []))}):")
            for col in schema_dict.get("columns", []):
                lines.append(f"    📊 {col}")

        output_str = "\n".join(lines)

    # Output
    if output:
        with open(output, "w") as f:
            f.write(output_str)
        click.echo(f"Schema written to: {output}")
    else:
        click.echo(output_str)


if __name__ == "__main__":
    main()
