"""
Command-line interface for Plexus Agent.

Simplified CLI - all device control happens through the web UI.

Usage:
    plexus start                   # Set up and stream
    plexus login                   # Sign in with web dashboard
    plexus status                  # Check connection status
    plexus scan                    # List detected sensors
"""

import getpass
import logging
import sys
import time
import threading
import webbrowser
from typing import Optional

import click

from plexus import __version__
from plexus.client import Plexus, AuthenticationError, PlexusError
from plexus.config import (
    load_config,
    save_config,
    get_api_key,
    get_endpoint,
    get_source_id,
    get_config_path,
)

logger = logging.getLogger(__name__)


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


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _validate_api_key(api_key: str, endpoint: str) -> bool:
    """Make a lightweight request to verify the API key is valid.

    Returns True if the key is accepted, False otherwise.
    """
    try:
        import requests
        resp = requests.post(
            f"{endpoint}/api/ingest",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={"points": []},
            timeout=10,
        )
        return resp.status_code < 400
    except Exception:
        return False


def _mask_key(key: str) -> str:
    """Mask an API key for display: plx_a1b2...c3d4"""
    if len(key) <= 12:
        return "****"
    return f"{key[:8]}...{key[-4:]}"


# ─────────────────────────────────────────────────────────────────────────────
# Terminal Auth
# ─────────────────────────────────────────────────────────────────────────────

def _select(label: str, options: list, default: int = 0) -> int:
    """Arrow-key selector. Returns the chosen index."""
    import tty
    import termios

    selected = default
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)

    def _render():
        # Move cursor up to overwrite previous render (except first time)
        for i, opt in enumerate(options):
            prefix = click.style("  ›", fg=Style.SUCCESS) if i == selected else "   "
            text = click.style(f" {opt}", bold=(i == selected))
            click.echo(f"\r{prefix}{text}   ")  # trailing spaces clear leftover chars

    click.echo(click.style(f"  {label}", fg=Style.INFO))
    click.echo()
    _render()

    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == "\r" or ch == "\n":
                break
            if ch == "\x03":  # Ctrl-C
                raise KeyboardInterrupt
            if ch == "\x1b":  # Escape sequence
                seq = sys.stdin.read(2)
                if seq == "[A":  # Up arrow
                    selected = (selected - 1) % len(options)
                elif seq == "[B":  # Down arrow
                    selected = (selected + 1) % len(options)
            # Move cursor up to re-render
            click.echo(f"\x1b[{len(options)}A", nl=False)
            _render()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    click.echo()
    return selected


def _terminal_auth(endpoint: str) -> str:
    """Interactive sign-up / sign-in flow entirely in the terminal.

    Returns the API key on success or exits on failure.
    """
    import requests

    click.echo()
    choice = _select("New to Plexus?", ["Sign up", "Sign in"], default=0)
    mode = "signup" if choice == 0 else "signin"

    email = click.prompt(
        click.style("  Email", fg=Style.INFO),
        type=str,
    ).strip()

    password = getpass.getpass(
        click.style("  Password: ", fg=Style.INFO),
    )

    if mode == "signup":
        first_name = click.prompt(
            click.style("  First name", fg=Style.INFO)
            + click.style(" (optional)", fg=Style.DIM),
            default="",
            show_default=False,
        ).strip() or None

        spinner = Spinner("Creating account...")
        spinner.start()
        try:
            resp = requests.post(
                f"{endpoint}/api/auth/cli/signup",
                json={"email": email, "password": password, "first_name": first_name},
                timeout=30,
            )
        except Exception as e:
            spinner.stop(f"Connection failed: {e}", success_status=False)
            sys.exit(1)
        spinner.stop()

        if resp.status_code == 409:
            # Account already exists — fall through to sign-in
            hint("Account exists, signing in instead...")
            mode = "signin"
        elif not resp.ok:
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            error(data.get("message", data.get("error", "Sign-up failed")))
            sys.exit(1)
        else:
            success("Account created")
            data = resp.json()

            # Prompt for verification code
            click.echo()
            code = click.prompt(
                click.style("  Verification code", fg=Style.INFO)
                + click.style(" (check email)", fg=Style.DIM),
                type=str,
            ).strip()

            spinner = Spinner("Verifying...")
            spinner.start()
            try:
                resp = requests.post(
                    f"{endpoint}/api/auth/cli/verify",
                    json={
                        "sign_up_id": data["sign_up_id"],
                        "client_token": data["client_token"],
                        "code": code,
                    },
                    timeout=30,
                )
            except Exception as e:
                spinner.stop(f"Connection failed: {e}", success_status=False)
                sys.exit(1)

            if not resp.ok:
                vdata = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                spinner.stop(vdata.get("message", vdata.get("error", "Verification failed")), success_status=False)
                sys.exit(1)

            result = resp.json()
            spinner.stop("Welcome to Plexus!", success_status=True)

            api_key = result["api_key"]
            config = load_config()
            config["api_key"] = api_key
            config["org_id"] = result.get("org_id")
            save_config(config)
            click.echo()
            return api_key

    # Sign-in flow (also handles signup → signin fallback)
    if mode == "signin":
        spinner = Spinner("Signing in...")
        spinner.start()
        try:
            resp = requests.post(
                f"{endpoint}/api/auth/cli/signin",
                json={"email": email, "password": password},
                timeout=30,
            )
        except Exception as e:
            spinner.stop(f"Connection failed: {e}", success_status=False)
            sys.exit(1)

        if not resp.ok:
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            err_code = data.get("error", "")
            if err_code == "no_account":
                spinner.stop("No account found. Try signup instead.", success_status=False)
            elif err_code == "invalid_credentials":
                spinner.stop("Wrong password", success_status=False)
            else:
                spinner.stop(data.get("message", "Sign-in failed"), success_status=False)
            sys.exit(1)

        result = resp.json()
        spinner.stop("Welcome back!", success_status=True)

        api_key = result["api_key"]
        config = load_config()
        config["api_key"] = api_key
        config["org_id"] = result.get("org_id")
        save_config(config)
        click.echo()
        return api_key

    # Should not reach here
    error("Authentication failed")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Auto-Dashboard
# ─────────────────────────────────────────────────────────────────────────────

def _build_panels(source_id: str, sensors: list, cameras: list) -> list:
    """Map detected hardware to dashboard panel definitions."""
    panels = []
    y = 0

    def _add(panel_type, title, metrics, w=12, h=6, config=None):
        nonlocal y
        panels.append({
            "id": f"auto-{len(panels) + 1}",
            "type": panel_type,
            "title": title,
            "metrics": [f"{source_id}:{m}" for m in metrics],
            "config": config or {"decimals": 2, "showLegend": True, "showGrid": True},
            "layout": {"x": 0, "y": y, "w": w, "h": h},
        })
        y += h

    # Group metrics by kind
    all_metrics = []
    for s in sensors:
        metrics = getattr(s, 'metrics', None) or (
            getattr(s.driver, 'metrics', None) if hasattr(s, 'driver') else None
        )
        if metrics:
            all_metrics.extend(metrics)

    metric_set = set(all_metrics)

    # Acceleration (multi-series)
    accel = [m for m in all_metrics if m.startswith("accel")]
    if accel:
        _add("line", "Acceleration", accel, config={
            "unit": "m/s²", "decimals": 3, "showLegend": True, "showGrid": True,
        })

    # Gyroscope (multi-series)
    gyro = [m for m in all_metrics if m.startswith("gyro")]
    if gyro:
        _add("line", "Gyroscope", gyro, config={
            "unit": "°/s", "decimals": 3, "showLegend": True, "showGrid": True,
        })

    # Environment
    for metric, title, unit in [
        ("temperature", "Temperature", "°C"),
        ("humidity", "Humidity", "%"),
        ("pressure", "Pressure", "hPa"),
    ]:
        if metric in metric_set:
            _add("line", title, [metric], config={
                "unit": unit, "decimals": 1, "showLegend": True, "showGrid": True,
            })

    # Power
    for metric, title, unit in [
        ("voltage", "Voltage", "V"),
        ("current", "Current", "A"),
        ("power", "Power", "W"),
    ]:
        if metric in metric_set:
            _add("line", title, [metric], config={
                "unit": unit, "decimals": 2, "showLegend": True, "showGrid": True,
            })

    # Battery
    if "battery" in metric_set:
        _add("stat", "Battery", ["battery"], w=6, h=4, config={
            "unit": "%", "decimals": 0, "showProgressBar": True,
        })

    # System stats
    for metric, title in [
        ("cpu.usage_pct", "CPU Usage"),
        ("memory.used_pct", "Memory Usage"),
    ]:
        if metric in metric_set:
            _add("stat", title, [metric], w=6, h=4, config={
                "unit": "%", "decimals": 1, "showProgressBar": True,
            })

    if "cpu.temperature" in metric_set:
        _add("line", "CPU Temperature", ["cpu.temperature"], config={
            "unit": "°C", "decimals": 1, "showLegend": True, "showGrid": True,
        })

    # GPS
    if "latitude" in metric_set and "longitude" in metric_set:
        _add("map", "Location", ["latitude", "longitude"], h=8, config={
            "latMetric": f"{source_id}:latitude",
            "lngMetric": f"{source_id}:longitude",
            "showPath": True,
        })

    # Cameras
    for i, cam in enumerate(cameras):
        cam_name = getattr(cam, "name", f"Camera {i + 1}")
        _add("video", cam_name, [], h=8, config={
            "cameraId": f"{source_id}:camera_{i}",
        })

    # Remaining metrics that weren't already covered
    covered = set()
    for p in panels:
        for m in p["metrics"]:
            covered.add(m.split(":")[-1])
    remaining = [m for m in all_metrics if m not in covered]
    for metric in remaining:
        _add("line", metric.replace("_", " ").replace(".", " ").title(), [metric])

    return panels


def _launch_auto_dashboard(api_key: str, endpoint: str, source_id: str, sensors: list, cameras: list):
    """Launch auto-dashboard creation in a background thread."""
    panels = _build_panels(source_id, sensors, cameras)
    if not panels:
        return

    def _create():
        import requests
        try:
            time.sleep(3)  # Wait for first data to land

            headers = {
                "x-api-key": api_key,
                "Content-Type": "application/json",
            }

            # Create dashboard
            resp = requests.post(
                f"{endpoint}/api/dashboards",
                headers=headers,
                json={"name": f"{source_id} Dashboard"},
                timeout=15,
            )
            if not resp.ok:
                logger.debug("Dashboard create failed: %s", resp.text)
                return

            dashboard = resp.json().get("dashboard", {})
            dashboard_id = dashboard.get("id")
            if not dashboard_id:
                return

            # Update with panels
            resp = requests.put(
                f"{endpoint}/api/dashboards/{dashboard_id}",
                headers=headers,
                json={
                    "config": {
                        "panels": panels,
                        "timeRange": {"type": "relative", "value": "5m"},
                    }
                },
                timeout=15,
            )
            if not resp.ok:
                logger.debug("Dashboard update failed: %s", resp.text)
                return

            dashboard_url = f"{endpoint}/dashboards/{dashboard_id}"
            click.echo()
            success(f"Dashboard ready {Style.ARROW} {dashboard_url}")
            click.echo()

            # Open in browser
            webbrowser.open(dashboard_url)

        except Exception as e:
            logger.debug("Auto-dashboard failed: %s", e)

    thread = threading.Thread(target=_create, daemon=True)
    thread.start()


# ─────────────────────────────────────────────────────────────────────────────
# CLI Commands
# ─────────────────────────────────────────────────────────────────────────────

@click.group()
@click.version_option(version=__version__, prog_name="plexus")
def main():
    """
    Plexus Agent - Connect your hardware to Plexus.

    Quick start:

        plexus start --key plx_xxx     # Set up and stream (one command)

    Or step by step:

        plexus login                   # Sign in (one-time)
        plexus start                   # Detect and stream

    Add capabilities:

        plexus add sensors camera      # Install extras

    All device control happens through the web dashboard at:
    https://app.plexus.company
    """
    pass


# ─────────────────────────────────────────────────────────────────────────────
# plexus start
# ─────────────────────────────────────────────────────────────────────────────

@main.command()
@click.option("--key", "-k", help="API key from dashboard")
@click.option("--name", "-n", help="Device name for identification")
@click.option("--slug", help="Device slug (source ID) from dashboard")
@click.option("--org", help="Organization ID from dashboard")
@click.option("--bus", "-b", default=1, type=int, help="I2C bus number for sensors")
@click.option("--no-sensors", is_flag=True, help="Disable sensor auto-detection")
@click.option("--no-cameras", is_flag=True, help="Disable camera auto-detection")
@click.option("--sensor", "-s", "sensor_types", multiple=True, help="Sensor type to use (e.g. system). Repeatable.")
@click.option("--mqtt", "mqtt_broker", default=None, help="MQTT broker to bridge (e.g. localhost:1883)")
@click.option("--mqtt-topic", default="sensors/#", help="MQTT topic to subscribe to")
@click.option("--auto-install", is_flag=True, help="Auto-install missing dependencies")
@click.option("--live", is_flag=True, help="Show live terminal dashboard with real-time metrics")
def start(key: Optional[str], name: Optional[str], slug: Optional[str], org: Optional[str], bus: int, no_sensors: bool, no_cameras: bool, sensor_types: tuple, mqtt_broker: Optional[str], mqtt_topic: str, auto_install: bool, live: bool):
    """
    Set up and start streaming in one command.

    Handles auth, hardware detection, dependency installation, and sensor
    selection. Interactive when run without flags, non-interactive with flags.

    Examples:

        plexus start                                        # Interactive setup
        plexus start --key plx_xxx                          # Non-interactive auth
        plexus start --key plx_xxx --slug my-drone --org org_123  # Full setup
        plexus start --sensor system                        # System health metrics
        plexus start --no-sensors --no-cameras              # Skip hardware detection
        plexus start --mqtt localhost:1883                   # Bridge MQTT data
        plexus start --live                                  # Live terminal dashboard
    """
    from plexus.connector import run_connector
    from plexus.detect import detect_sensors, detect_cameras, detect_can

    # Enable auto-install if requested
    if auto_install:
        from plexus.deps import enable_auto_install
        enable_auto_install()

    # Determine if we should run in non-interactive mode:
    # skip sensor selection prompt when explicit flags are used
    non_interactive = bool(no_sensors or no_cameras or sensor_types or mqtt_broker)

    # ── Welcome ───────────────────────────────────────────────────────────
    header(f"Plexus Agent v{__version__}")

    # ── Auth ──────────────────────────────────────────────────────────────
    api_key = get_api_key()
    endpoint = get_endpoint()

    if key:
        # --key flag: save and use
        config = load_config()
        config["api_key"] = key
        save_config(config)
        api_key = key
        success(f"API key saved ({_mask_key(api_key)})")
    elif api_key:
        # Key already in config
        success(f"API key: {_mask_key(api_key)}")
    else:
        # Terminal sign-up / sign-in flow
        api_key = _terminal_auth(endpoint)

    # Validate key
    spinner = Spinner("Validating API key...")
    spinner.start()
    key_valid = _validate_api_key(api_key, endpoint)
    if key_valid:
        spinner.stop("Connected to Plexus", success_status=True)
    else:
        spinner.stop("API key invalid or server unreachable", success_status=False)
        click.echo()
        hint("Check your key at app.plexus.company/devices")
        click.echo()
        sys.exit(1)

    # ── Slug / Org ──────────────────────────────────────────────────────
    if slug or org:
        config = load_config()
        if slug:
            config["source_id"] = slug
            success(f"Source ID: {slug}")
        if org:
            config["org_id"] = org
            success(f"Org ID: {org}")
        save_config(config)

    click.echo()

    # ── Hardware scan ─────────────────────────────────────────────────────
    info("Scanning hardware...")
    click.echo()

    # Sensors
    sensor_hub = None
    sensors = []
    i2c_error = None

    if sensor_types:
        # Named sensor types (e.g. --sensor system)
        from plexus.detect import detect_named_sensors
        try:
            sensor_hub, sensors = detect_named_sensors(list(sensor_types))
        except ValueError as e:
            i2c_error = str(e)
    elif not no_sensors:
        try:
            sensor_hub, sensors = detect_sensors(bus)
        except PermissionError:
            i2c_error = "I2C permission denied (run: sudo usermod -aG i2c $USER)"
        except ImportError:
            from plexus.deps import prompt_install
            if prompt_install("smbus2", extra="sensors"):
                try:
                    sensor_hub, sensors = detect_sensors(bus)
                except Exception as e:
                    i2c_error = str(e)
            else:
                i2c_error = None  # User declined, not an error — just skip
        except Exception as e:
            i2c_error = str(e)

    if i2c_error:
        warning(i2c_error)

    # Cameras
    camera_hub = None
    cameras = []
    if not no_cameras:
        try:
            camera_hub, cameras = detect_cameras()
        except ImportError:
            if non_interactive:
                from plexus.deps import prompt_install
                if prompt_install("cv2", extra="camera"):
                    try:
                        camera_hub, cameras = detect_cameras()
                    except Exception as e:
                        logger.debug("Camera detection failed after install: %s", e)
                        cameras = []
                else:
                    cameras = []
            else:
                logger.debug("Camera support not installed (opencv-python missing)")
        except Exception as e:
            click.echo(click.style(f"  {Style.CROSS} Camera detection failed: {e}", fg=Style.WARNING))

    # CAN
    can_adapters, up_can, down_can = detect_can()

    # MQTT
    mqtt_adapter = None
    mqtt_error = None
    if mqtt_broker:
        try:
            from plexus.adapters.mqtt import MQTTAdapter
            parts = mqtt_broker.split(":")
            broker_host = parts[0]
            broker_port = int(parts[1]) if len(parts) > 1 else 1883
            mqtt_adapter = MQTTAdapter(broker=broker_host, port=broker_port, topic=mqtt_topic)
        except ImportError:
            from plexus.deps import prompt_install
            if prompt_install("paho", extra="mqtt"):
                try:
                    from plexus.adapters.mqtt import MQTTAdapter
                    parts = mqtt_broker.split(":")
                    broker_host = parts[0]
                    broker_port = int(parts[1]) if len(parts) > 1 else 1883
                    mqtt_adapter = MQTTAdapter(broker=broker_host, port=broker_port, topic=mqtt_topic)
                except Exception as e:
                    mqtt_error = f"MQTT setup failed: {e}"
            else:
                mqtt_error = "paho-mqtt not installed (run: pip install plexus-agent[mqtt])"
        except Exception as e:
            mqtt_error = f"MQTT setup failed: {e}"

    # ── Sensor selection (interactive only) ────────────────────────────────
    if sensors and not non_interactive:
        click.echo(f"  Found {len(sensors)} sensor{'s' if len(sensors) != 1 else ''} on I2C bus {bus}:")
        click.echo()

        for idx, s in enumerate(sensors):
            metrics = getattr(s, 'metrics', None) or (getattr(s.driver, 'metrics', None) if hasattr(s, 'driver') else None)
            metrics_str = ", ".join(metrics) if metrics else getattr(s, 'description', "")
            click.echo(
                f"    [{idx + 1}] "
                + click.style(Style.CHECK, fg=Style.SUCCESS)
                + f" {s.name:<12}"
                + click.style(metrics_str, fg=Style.DIM)
            )

        click.echo()
        selection = click.prompt(
            "  Stream all? [Y/n] or enter numbers to select (e.g., 1,3)",
            default="Y",
            show_default=False,
        ).strip()

        if selection.lower() not in ("y", "yes", ""):
            # Parse selection
            try:
                indices = [int(x.strip()) - 1 for x in selection.split(",")]
                selected_sensors = [sensors[i] for i in indices if 0 <= i < len(sensors)]
            except (ValueError, IndexError):
                warning("Invalid selection, streaming all sensors")
                selected_sensors = sensors
        else:
            selected_sensors = sensors

        # Build a SensorHub with only selected sensors
        if selected_sensors and len(selected_sensors) != len(sensors):
            from plexus.sensors.base import SensorHub
            sensor_hub = SensorHub()
            for s in selected_sensors:
                sensor_instance = s.driver(address=s.address, bus=s.bus)
                sensor_hub.add(sensor_instance)
            sensors = selected_sensors

        click.echo()

    # ── Summary ───────────────────────────────────────────────────────────
    metric_count = 0
    for s in sensors:
        metrics = getattr(s, 'metrics', None) or (getattr(s.driver, 'metrics', None) if hasattr(s, 'driver') else None)
        metric_count += len(metrics) if metrics else 1

    if cameras:
        info(f"Cameras: {len(cameras)} detected")
    if up_can:
        info(f"CAN: {len(up_can)} active interface{'s' if len(up_can) != 1 else ''}")
    if mqtt_adapter:
        info(f"MQTT: {mqtt_broker} ({mqtt_topic})")
    elif mqtt_error:
        warning(mqtt_error)

    click.echo()

    # Update source name if provided
    source_id = get_source_id()
    if name:
        config = load_config()
        config["source_name"] = name
        save_config(config)

    if i2c_error and sensor_types:
        error(i2c_error)
        sys.exit(1)

    stream_label = f"Streaming {metric_count} metrics" if metric_count else "Connected"
    click.secho(f"  {Style.CHECK} {stream_label} {Style.ARROW} {source_id}", fg=Style.SUCCESS)
    click.echo()

    # ── Auto-dashboard (background) ──────────────────────────────────────
    _launch_auto_dashboard(
        api_key=api_key,
        endpoint=endpoint,
        source_id=source_id,
        sensors=sensors,
        cameras=cameras,
    )

    # ── Start MQTT bridge in background if configured ─────────────────────
    mqtt_thread = None
    if mqtt_adapter:
        px = Plexus(api_key=api_key, endpoint=endpoint)

        def _mqtt_forwarder(metrics):
            for m in metrics:
                try:
                    px.send(m.name, m.value, tags=m.tags or {})
                except Exception as e:
                    logger.warning(f"MQTT forward failed: {e}")

        mqtt_adapter._on_data_callback = _mqtt_forwarder
        try:
            mqtt_adapter.connect()
            mqtt_thread = threading.Thread(target=mqtt_adapter._run_loop, daemon=True)
            mqtt_thread.start()
            status_line(f"MQTT bridge active: {mqtt_broker}")
        except Exception as e:
            warning(f"MQTT connect failed: {e}")

    # ── Start connector ───────────────────────────────────────────────────
    if live:
        # Live TUI mode
        try:
            from plexus.tui import LiveDashboard
            dashboard = LiveDashboard()

            def _connector_fn():
                run_connector(
                    api_key=api_key,
                    endpoint=endpoint,
                    on_status=dashboard.wrap_status_callback(status_line),
                    sensor_hub=sensor_hub,
                    camera_hub=camera_hub,
                    can_adapters=can_adapters,
                )

            dashboard.run(_connector_fn)
        except ImportError as e:
            warning(str(e).strip())
            hint("Install with: pip install plexus-agent[tui]")
            click.echo()
            sys.exit(1)
        except KeyboardInterrupt:
            pass
        finally:
            if mqtt_adapter:
                mqtt_adapter.disconnect()
    else:
        # Standard mode
        try:
            run_connector(
                api_key=api_key,
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
        finally:
            if mqtt_adapter:
                mqtt_adapter.disconnect()


# ─────────────────────────────────────────────────────────────────────────────
# plexus add
# ─────────────────────────────────────────────────────────────────────────────

EXTRAS = [
    ("sensors", "I2C sensors (IMU, environmental, current)", "smbus2"),
    ("camera", "USB webcam support (OpenCV)", "cv2"),
    ("picamera", "Raspberry Pi Camera Module", "picamera2"),
    ("mqtt", "MQTT broker bridging", "paho"),
    ("can", "CAN bus with DBC signal decoding", "can"),
    ("mavlink", "MAVLink for drones/UAVs", "pymavlink"),
    ("serial", "Serial/UART communication", "serial"),
    ("system", "System health monitoring (psutil)", "psutil"),
    ("tui", "Live terminal dashboard", "rich"),
    ("ros", "ROS bag file import", "rosbags"),
]

@main.command()
@click.argument("capabilities", nargs=-1)
def add(capabilities: tuple):
    """
    Install capabilities (like 'shadcn add' for hardware).

    Without arguments, shows an interactive picker. With arguments,
    installs the specified extras directly.

    Examples:

        plexus add                     # Interactive picker
        plexus add can                 # Add CAN bus support
        plexus add sensors camera      # Add multiple
    """
    from plexus.deps import is_available
    import subprocess

    if not capabilities:
        # Interactive picker
        header("Add Capabilities")

        click.echo("  Available capabilities:")
        click.echo()

        for idx, (extra_name, desc, check_pkg) in enumerate(EXTRAS):
            installed = is_available(check_pkg)
            status_icon = click.style(Style.CHECK, fg=Style.SUCCESS) if installed else " "
            installed_label = click.style(" (installed)", fg=Style.DIM) if installed else ""
            click.echo(f"    [{idx + 1}] {status_icon} {extra_name:<12}{desc}{installed_label}")

        click.echo()
        selection = click.prompt(
            "  Select (e.g., 1,2,4)",
            default="",
            show_default=False,
        ).strip()

        if not selection:
            return

        try:
            indices = [int(x.strip()) - 1 for x in selection.split(",")]
            capabilities = tuple(EXTRAS[i][0] for i in indices if 0 <= i < len(EXTRAS))
        except (ValueError, IndexError):
            error("Invalid selection")
            return

    # Install each capability
    click.echo()
    for cap in capabilities:
        # Find the extra name
        extra_match = next((e for e in EXTRAS if e[0] == cap), None)
        if not extra_match:
            warning(f"Unknown capability: {cap}")
            hint(f"  Available: {', '.join(e[0] for e in EXTRAS)}")
            continue

        extra_name, desc, check_pkg = extra_match

        if is_available(check_pkg):
            success(f"{extra_name}: already installed")
            continue

        spinner = Spinner(f"Installing {extra_name}...")
        spinner.start()

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", f"plexus-agent[{extra_name}]"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                spinner.stop(f"{extra_name}: installed", success_status=True)
            else:
                spinner.stop(f"{extra_name}: install failed", success_status=False)
                if result.stderr:
                    dim(f"    {result.stderr.strip()[:200]}")
        except subprocess.TimeoutExpired:
            spinner.stop(f"{extra_name}: timed out", success_status=False)
        except Exception as e:
            spinner.stop(f"{extra_name}: {e}", success_status=False)

    click.echo()


@main.command()
def login():
    """
    Sign in to your Plexus account via the browser.

    Opens a browser window to authenticate with your Plexus account.
    This is a one-time setup - after signing in, use 'plexus start' to run.

    For API key authentication, use 'plexus start --key plx_xxx' instead.

    Examples:

        plexus login                   # Opens browser to sign in
        plexus start --key plx_xxx     # Or use an API key directly
    """
    import webbrowser

    base_endpoint = "https://app.plexus.company"

    header("Plexus Login")

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
            spinner.stop(f"Failed to start login: {response.text}", success_status=False)
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
    click.secho("  Your code:  ", fg=Style.DIM, nl=False)
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
                    save_config(config)

                    spinner.stop("Logged in successfully!", success_status=True)
                    click.echo()
                    hint("Start the agent with: plexus start --slug <your-device-slug>")
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
                    hint("Try again: plexus login")
                    click.echo()
                    sys.exit(1)

        except Exception:
            continue

    spinner.stop("Timed out waiting for authorization", success_status=False)
    click.echo()
    hint("Try again: plexus login")
    click.echo()
    sys.exit(1)


@main.command()
def status():
    """
    Check connection status and configuration.

    Shows whether the device is paired and can connect to Plexus.
    """
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

    if api_key:
        masked = _mask_key(api_key)
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
            hint("Ready to run: plexus start")
            click.echo()
        except AuthenticationError:
            spinner.stop("Auth failed", success_status=False)
            click.echo()
            hint("Re-authenticate: plexus login")
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
        hint("Run 'plexus login' to connect this device")
        click.echo()


@main.command()
@click.option("--bus", "-b", default=1, type=int, help="I2C bus number")
@click.option("--all", "-a", "show_all", is_flag=True, help="Show all I2C addresses")
@click.option("--setup", is_flag=True, help="Auto-configure detected interfaces")
@click.option("--json", "output_json", is_flag=True, help="Output results as JSON")
def scan(bus: int, show_all: bool, setup: bool, output_json: bool):
    """
    Scan for all connected hardware.

    Detects I2C sensors, cameras, serial ports, USB devices, network
    interfaces, GPIO, Bluetooth, and system information.

    Examples:

        plexus scan                    # Full hardware scan
        plexus scan -b 0               # Scan different I2C bus
        plexus scan --all              # Show all I2C addresses
        plexus scan --setup            # Auto-configure CAN interfaces
        plexus scan --json             # Machine-readable JSON output
    """
    import json as json_mod
    from plexus.detect import (
        detect_sensors,
        detect_cameras,
        detect_serial,
        detect_usb,
        detect_gpio,
        detect_bluetooth,
        detect_network,
        detect_system,
        detect_all,
    )

    # ── JSON output mode ──────────────────────────────────────────────────
    if output_json:
        data = detect_all(bus=bus)
        click.echo(json_mod.dumps(data, indent=2))
        return

    # ── Header ────────────────────────────────────────────────────────────
    header("Plexus Device Scan")

    # ── System info ───────────────────────────────────────────────────────
    sys_info = detect_system()

    info("System")
    label("Hostname", sys_info.hostname, key_width=16)
    label("Platform", f"{sys_info.platform} {sys_info.arch}", key_width=16)

    cpu_str = sys_info.cpu
    if sys_info.cpu_cores:
        cpu_str += f" ({sys_info.cpu_cores} cores)"
    label("CPU", cpu_str, key_width=16)

    if sys_info.ram_mb:
        if sys_info.ram_mb >= 1024:
            ram_display = f"{sys_info.ram_mb / 1024:.1f} GB"
        else:
            ram_display = f"{sys_info.ram_mb} MB"
        label("Memory", ram_display, key_width=16)

    if sys_info.disk_gb:
        label("Storage", f"{sys_info.disk_gb} GB available", key_width=16)

    if sys_info.os_version:
        label("OS", sys_info.os_version, key_width=16)

    label("Python", sys_info.python_version, key_width=16)

    click.echo()
    divider()
    click.echo()

    found_hardware = False

    # ── I2C Sensors ───────────────────────────────────────────────────────
    if show_all:
        # Show raw I2C addresses with driver matching
        try:
            from plexus.sensors import scan_i2c
            addresses = scan_i2c(bus)
            if addresses:
                info(f"I2C Bus {bus}")
                # Get known sensors for matching
                _, known_sensors = detect_sensors(bus)
                known_addrs = {}
                for s in known_sensors:
                    known_addrs[s.address] = s

                for addr in addresses:
                    if addr in known_addrs:
                        s = known_addrs[addr]
                        click.secho(
                            f"    {Style.CHECK} 0x{addr:02X}  {s.name:<12}{s.description}",
                            fg=Style.SUCCESS,
                        )
                    else:
                        click.secho(
                            f"    {Style.BULLET} 0x{addr:02X}  {'Unknown':<12}(no driver)",
                            fg=Style.WARNING,
                        )
                click.echo()
                found_hardware = True
        except ImportError:
            info(f"I2C Bus {bus}")
            click.secho(
                f"    {Style.BULLET} smbus2 not installed",
                fg=Style.WARNING,
            )
            hint("    Install: pip install plexus-agent[sensors]")
            click.echo()
        except PermissionError:
            info(f"I2C Bus {bus}")
            click.secho(
                f"    {Style.CROSS} Permission denied",
                fg=Style.ERROR,
            )
            hint("    Fix: sudo usermod -aG i2c $USER && logout")
            click.echo()
        except Exception as e:
            dim(f"  I2C error: {e}")
            click.echo()
    else:
        try:
            _, sensors = detect_sensors(bus)
            if sensors:
                info(f"I2C Bus {bus}")
                for s in sensors:
                    click.secho(
                        f"    {Style.CHECK} 0x{s.address:02X}  {s.name:<12}{s.description}",
                        fg=Style.SUCCESS,
                    )
                click.echo()
                found_hardware = True
        except ImportError:
            import os
            if os.path.exists(f"/dev/i2c-{bus}"):
                info(f"I2C Bus {bus}")
                click.secho(
                    f"    {Style.BULLET} smbus2 not installed",
                    fg=Style.WARNING,
                )
                hint("    Install: pip install plexus-agent[sensors]")
                click.echo()
        except PermissionError:
            info(f"I2C Bus {bus}")
            click.secho(
                f"    {Style.CROSS} Permission denied",
                fg=Style.ERROR,
            )
            hint("    Fix: sudo usermod -aG i2c $USER && logout")
            click.echo()
        except Exception:
            pass

    # ── Serial Ports ──────────────────────────────────────────────────────
    try:
        serial_devices = detect_serial()
    except ImportError:
        serial_devices = None
    if serial_devices is None:
        import os
        import glob as _glob
        serial_paths = _glob.glob("/dev/ttyUSB*") + _glob.glob("/dev/ttyACM*")
        if serial_paths:
            info("Serial Ports")
            click.secho(
                f"    {Style.BULLET} pyserial not installed",
                fg=Style.WARNING,
            )
            hint("    Install: pip install plexus-agent[serial]")
            click.echo()
    elif serial_devices:
        info("Serial Ports")
        for d in serial_devices:
            desc_parts = []
            if d.manufacturer:
                desc_parts.append(d.manufacturer)
            if d.description:
                desc_parts.append(d.description)
            desc_str = "    ".join(desc_parts) if desc_parts else ""
            click.secho(
                f"    {Style.CHECK} {d.port:<20}{desc_str}",
                fg=Style.SUCCESS,
            )
        click.echo()
        found_hardware = True

    # ── Cameras ───────────────────────────────────────────────────────────
    try:
        _, cameras = detect_cameras()
    except ImportError:
        cameras = None
    if cameras is None:
        import os
        import glob as _glob
        video_devs = _glob.glob("/dev/video*")
        if video_devs:
            info("Cameras")
            click.secho(
                f"    {Style.BULLET} opencv-python not installed",
                fg=Style.WARNING,
            )
            hint("    Install: pip install plexus-agent[camera]")
            click.echo()
    elif cameras:
        info("Cameras")
        for c in cameras:
            click.secho(
                f"    {Style.CHECK} {c.device_id:<20}{c.name:<16}{c.description}",
                fg=Style.SUCCESS,
            )
        click.echo()
        found_hardware = True

    # ── Network Interfaces ────────────────────────────────────────────────
    net_interfaces = detect_network()
    # Filter to interesting interfaces (skip loopback, veth, airdrop, tunnels)
    interesting_types = {"ethernet", "wifi", "can", "bridge", "other"}
    interesting_net = [
        n for n in net_interfaces
        if n.type in interesting_types and n.state == "up"
    ]
    if interesting_net:
        info("Network")
        for n in interesting_net:
            type_label = n.type.capitalize()
            if n.type == "can":
                type_label = "CAN"
            detail_parts = []
            if n.ip:
                detail_parts.append(n.ip)

            # WiFi extras
            if n.type == "wifi":
                ssid = n.extra.get("ssid", "")
                signal = n.extra.get("signal_dbm")
                if ssid:
                    detail_parts.append(ssid)
                if signal is not None:
                    detail_parts.append(f"({signal} dBm)")
            # Ethernet speed
            elif n.type == "ethernet":
                speed = n.extra.get("speed_mbps")
                if speed:
                    detail_parts.append(f"{speed} Mbps")
            # CAN bitrate
            elif n.type == "can":
                bitrate = n.extra.get("bitrate")
                if bitrate:
                    detail_parts.append(f"{bitrate} bps")

            detail_str = "    ".join(detail_parts)
            click.secho(
                f"    {Style.CHECK} {n.name:<10}{type_label:<12}{detail_str}",
                fg=Style.SUCCESS,
            )
        click.echo()
        found_hardware = True

    # ── CAN Interfaces (down, needing setup) ──────────────────────────────
    try:
        from plexus.adapters.can_detect import scan_can, setup_can, DEFAULT_BITRATE
        detected_can = scan_can()
        down_can = [c for c in detected_can if not c.is_up]
        if down_can:
            info("CAN Interfaces (down)")
            for c in down_can:
                if setup and c.interface == "socketcan":
                    spinner = Spinner(f"Configuring {c.channel}...")
                    spinner.start()
                    ok = setup_can(c)
                    if ok:
                        spinner.stop(
                            f"{c.channel} (up, {DEFAULT_BITRATE} bps)",
                            success_status=True,
                        )
                    else:
                        spinner.stop(
                            f"Failed to configure {c.channel} -- try manually with sudo",
                            success_status=False,
                        )
                else:
                    click.secho(
                        f"    {Style.BULLET} {c.channel} ({c.interface}, down)",
                        fg=Style.WARNING,
                    )
                    if c.interface == "socketcan":
                        hint("      Run: plexus scan --setup")
                    elif c.interface == "slcan":
                        dim("      Serial CAN adapter -- configure with slcand")
            click.echo()
            found_hardware = True
    except ImportError:
        # Check if CAN interfaces exist in network list
        can_nets = [n for n in net_interfaces if n.type == "can"] if interesting_net else []
        if can_nets:
            info("CAN Interfaces")
            click.secho(
                f"    {Style.BULLET} python-can not installed",
                fg=Style.WARNING,
            )
            hint("    Install: pip install plexus-agent[can]")
            click.echo()
    except Exception as e:
        logger.debug(f"CAN scan error: {e}")

    # ── GPIO ──────────────────────────────────────────────────────────────
    gpio_chips = detect_gpio()
    if gpio_chips:
        info("GPIO")
        for g in gpio_chips:
            lines_str = f"{g.num_lines} lines available" if g.num_lines else ""
            click.secho(
                f"    {Style.BULLET} {g.chip:<14}{lines_str}",
                fg=Style.WARNING,
            )
        click.echo()
        found_hardware = True

    # ── USB Devices ───────────────────────────────────────────────────────
    usb_devices = detect_usb()
    if usb_devices:
        info("USB Devices")
        for u in usb_devices:
            id_str = ""
            if u.vendor_id and u.product_id:
                id_str = f"{u.vendor_id}:{u.product_id}"
            mfr_str = u.manufacturer if u.manufacturer else ""
            click.secho(
                f"    {Style.CHECK} {u.name:<28}{id_str:<14}{mfr_str}",
                fg=Style.SUCCESS,
            )
        click.echo()
        found_hardware = True

    # ── Bluetooth ─────────────────────────────────────────────────────────
    bt_devices = detect_bluetooth()
    if bt_devices:
        info("Bluetooth")
        for b in bt_devices:
            rssi_str = f"{b.rssi} dBm" if b.rssi is not None else ""
            click.secho(
                f"    {Style.CHECK} {b.name:<24}{b.address:<20}{rssi_str}",
                fg=Style.SUCCESS,
            )
        click.echo()
        found_hardware = True

    # ── No hardware found ─────────────────────────────────────────────────
    if not found_hardware:
        dim("  No hardware detected")
        click.echo()

    # ── Footer with recommendations ───────────────────────────────────────
    divider()
    click.echo()
    click.secho(
        f"  {Style.ARROW} Run 'plexus start' to stream all detected hardware",
        fg=Style.INFO,
    )
    click.secho(
        f"  {Style.ARROW} Run 'plexus start --sensor system' for system metrics only",
        fg=Style.INFO,
    )
    click.echo()


@main.command()
def doctor():
    """
    Diagnose connectivity, configuration, and dependency issues.

    Checks everything needed for the Plexus agent to work:
    configuration, authentication, network, dependencies,
    and hardware permissions.

    Examples:

        plexus doctor                  # Run all diagnostics
    """
    import platform as _platform
    import socket

    header("Plexus Doctor")

    checks_passed = 0
    checks_failed = 0
    checks_warned = 0

    def _pass(msg: str):
        nonlocal checks_passed
        checks_passed += 1
        success(msg)

    def _fail(msg: str):
        nonlocal checks_failed
        checks_failed += 1
        error(msg)

    def _warn(msg: str):
        nonlocal checks_warned
        checks_warned += 1
        warning(msg)

    # ── 1. Configuration ──────────────────────────────────────────────────

    info("Configuration")
    click.echo()

    config_path = get_config_path()
    if config_path.exists():
        _pass(f"Config file: {config_path}")
    else:
        _warn(f"No config file at {config_path}")

    api_key_val = get_api_key()

    if api_key_val:
        masked = _mask_key(api_key_val)
        _pass(f"API key: {masked}")
    else:
        _fail("No credentials configured")
        dim("    Run: plexus start --key YOUR_API_KEY")

    endpoint_val = get_endpoint()
    _pass(f"Endpoint: {endpoint_val}")

    source_id_val = get_source_id()
    if source_id_val:
        _pass(f"Source ID: {source_id_val}")
    else:
        _warn("No source ID (will be auto-generated)")

    click.echo()
    divider()
    click.echo()

    # ── 2. Network ────────────────────────────────────────────────────────

    info("Network")
    click.echo()

    # DNS resolution
    try:
        host = endpoint_val.replace("https://", "").replace("http://", "").split("/")[0]
        socket.getaddrinfo(host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
        _pass(f"DNS resolves: {host}")
    except socket.gaierror:
        _fail(f"DNS resolution failed for {host}")

    # HTTP connectivity
    try:
        import requests
        resp = requests.get(f"{endpoint_val}/api/ingest", timeout=10)
        if resp.status_code in (200, 405, 401):
            _pass(f"HTTP reachable: {endpoint_val}")
        else:
            _warn(f"HTTP status {resp.status_code} from {endpoint_val}")
    except requests.exceptions.SSLError:
        _fail("TLS/SSL certificate error")
        dim("    Check system clock and CA certificates")
    except requests.exceptions.ConnectionError:
        _fail(f"Cannot connect to {endpoint_val}")
    except requests.exceptions.Timeout:
        _fail("Connection timed out (10s)")
    except Exception as e:
        _fail(f"HTTP error: {e}")

    # Auth test
    if api_key_val:
        try:
            import requests
            auth_token = api_key_val
            resp = requests.post(
                f"{endpoint_val}/api/ingest",
                headers={"x-api-key": auth_token, "Content-Type": "application/json"},
                json={"points": []},
                timeout=10,
            )
            if resp.status_code < 400:
                _pass("Authentication: valid")
            elif resp.status_code == 401:
                _fail("Authentication: invalid key")
                dim("    Re-authenticate: plexus start --key YOUR_API_KEY")
            elif resp.status_code == 403:
                _fail("Authentication: key lacks write permission")
            else:
                _warn(f"Auth check returned HTTP {resp.status_code}")
        except Exception:
            _warn("Could not verify authentication")

    click.echo()
    divider()
    click.echo()

    # ── 3. Dependencies ──────────────────────────────────────────────────

    info("Dependencies")
    click.echo()

    from plexus.deps import check_extras_for_scan
    extras = check_extras_for_scan()

    core_deps = ["requests", "click", "websockets"]
    for dep in core_deps:
        try:
            __import__(dep)
            _pass(f"{dep}: installed")
        except ImportError:
            _fail(f"{dep}: MISSING (core dependency)")

    click.echo()
    dim("  Optional extras:")

    for extra_name, installed in extras.items():
        if installed:
            dim(f"    {Style.CHECK} {extra_name}")
        else:
            dim(f"    {Style.BULLET} {extra_name} (not installed)")

    click.echo()
    divider()
    click.echo()

    # ── 4. Hardware Permissions ──────────────────────────────────────────

    info("Hardware Permissions")
    click.echo()

    import os

    # I2C bus
    for bus_num in [0, 1]:
        bus_path = f"/dev/i2c-{bus_num}"
        if os.path.exists(bus_path):
            if os.access(bus_path, os.R_OK | os.W_OK):
                _pass(f"I2C bus {bus_num}: accessible")
            else:
                _fail(f"I2C bus {bus_num}: permission denied")
                dim("    Fix: sudo usermod -aG i2c $USER")

    # Serial ports
    import glob
    serial_ports = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
    for port in serial_ports[:3]:
        if os.access(port, os.R_OK | os.W_OK):
            _pass(f"{port}: accessible")
        else:
            _fail(f"{port}: permission denied")
            dim("    Fix: sudo usermod -aG dialout $USER")

    # Camera
    video_devs = glob.glob("/dev/video*")
    for dev in video_devs[:2]:
        if os.access(dev, os.R_OK | os.W_OK):
            _pass(f"{dev}: accessible")
        else:
            _fail(f"{dev}: permission denied")
            dim("    Fix: sudo usermod -aG video $USER")

    if not any(os.path.exists(p) for p in ["/dev/i2c-0", "/dev/i2c-1"]) and not serial_ports and not video_devs:
        dim("  No hardware devices detected on this system")

    click.echo()
    divider()
    click.echo()

    # ── 5. System Info ───────────────────────────────────────────────────

    info("System")
    click.echo()

    label("Platform", f"{_platform.system()} {_platform.machine()}")
    label("Python", _platform.python_version())
    label("Hostname", socket.gethostname())

    try:
        from plexus import __version__
        label("Agent", f"v{__version__}")
    except Exception:
        pass

    click.echo()
    divider()
    click.echo()

    # ── Summary ──────────────────────────────────────────────────────────

    if checks_failed == 0:
        click.secho(
            f"  {Style.CHECK} All {checks_passed} checks passed",
            fg=Style.SUCCESS,
            bold=True,
        )
    else:
        click.secho(
            f"  {checks_passed} passed, {checks_failed} failed, {checks_warned} warnings",
            fg=Style.ERROR if checks_failed > 0 else Style.WARNING,
        )

    click.echo()

    if checks_failed > 0:
        hint("Fix the issues above and run 'plexus doctor' again")
        click.echo()


if __name__ == "__main__":
    main()
