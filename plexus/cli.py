"""
Command-line interface for Plexus Agent.

Usage:
    plexus start                   # Set up and stream
    plexus reset                   # Clear config and start over
"""

import getpass
import logging
import sys
import time
import threading
from typing import Optional

import click

from plexus import __version__

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


def _print_status_block(device_name: str, sensors: list, dashboard_url: Optional[str]):
    """Print the compact post-connect status block."""
    click.echo()
    success(f"{device_name} connected")
    click.echo()

    # Metrics preview — first 3 names + "N more"
    all_metrics = []
    for s in sensors:
        m = getattr(s, 'metrics', None) or (
            getattr(s.driver, 'metrics', None) if hasattr(s, 'driver') else None
        )
        if m:
            all_metrics.extend(m)

    if all_metrics:
        preview = ", ".join(all_metrics[:3])
        remaining = len(all_metrics) - 3
        metrics_str = preview + (f" + {remaining} more" if remaining > 0 else "")
    else:
        metrics_str = "none"

    label("Metrics", metrics_str)
    label("Mode", "Live (record from dashboard)")
    if dashboard_url:
        label("Dashboard", dashboard_url)
    click.echo()


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


def _detect_device_type() -> str:
    """Detect the type of device we're running on."""
    import platform

    system = platform.system().lower()
    machine = platform.machine().lower()

    # Check for Raspberry Pi
    try:
        with open("/proc/device-tree/model", "r") as f:
            model = f.read().strip()
            if "raspberry pi" in model.lower():
                return model
    except (FileNotFoundError, PermissionError):
        pass

    # Check for Jetson
    try:
        with open("/proc/device-tree/model", "r") as f:
            model = f.read().strip()
            if "jetson" in model.lower():
                return model
    except (FileNotFoundError, PermissionError):
        pass

    if system == "darwin":
        mac_model = platform.machine()
        return f"macOS ({mac_model})"
    elif system == "linux":
        if "aarch64" in machine or "arm" in machine:
            return f"Linux ({machine})"
        return f"Linux ({machine})"
    elif system == "windows":
        return f"Windows ({machine})"

    return f"{platform.system()} ({machine})"


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

        if resp.status_code == 409:
            # Account already exists — fall through to sign-in
            spinner.stop()
            hint("Account exists, signing in instead...")
            mode = "signin"
        elif not resp.ok:
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            spinner.stop(data.get("message", data.get("error", "Sign-up failed")), success_status=False)
            sys.exit(1)
        else:
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


def _startup_wizard(endpoint: str) -> str:
    """Interactive first-time setup. Returns API key."""
    import socket

    # ── Welcome box ──
    device_type = _detect_device_type()
    click.echo()
    click.secho(f"  ┌{'─' * (Style.WIDTH - 2)}┐", fg=Style.DIM)
    click.secho(f"  │{'Welcome to Plexus':^{Style.WIDTH - 2}}│", fg="white", bold=True)
    click.secho(f"  │{f'Device: {device_type}':^{Style.WIDTH - 2}}│", fg=Style.DIM)
    click.secho(f"  │{f'Version: {__version__}':^{Style.WIDTH - 2}}│", fg=Style.DIM)
    click.secho(f"  └{'─' * (Style.WIDTH - 2)}┘", fg=Style.DIM)
    click.echo()

    # ── Device name ──
    default_name = socket.gethostname().lower().replace(" ", "-")
    device_name = click.prompt(
        click.style("  Device name", fg=Style.INFO),
        default=default_name,
    ).strip().lower().replace(" ", "-")

    config = load_config()
    config["source_name"] = device_name
    config["source_id"] = device_name
    save_config(config)
    success(f"Device: {device_name}")
    click.echo()

    # ── Auth ──
    api_key = _terminal_auth(endpoint)
    return api_key


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


def _start_metric_readout(sensor_hub):
    """Show live metric values in the terminal."""
    num_metrics = 0

    def _readout():
        nonlocal num_metrics
        while True:
            try:
                readings = sensor_hub.read_all()
                if not readings:
                    time.sleep(2)
                    continue

                # Move cursor up to overwrite previous readout
                if num_metrics > 0:
                    click.echo(f"\x1b[{num_metrics}A", nl=False)

                num_metrics = len(readings)
                for r in readings:
                    name = r.metric.replace("_", " ").replace(".", " > ")
                    if isinstance(r.value, float):
                        val = f"{r.value:.1f}"
                    else:
                        val = str(r.value)

                    line = (
                        click.style(f"  {name:<24}", fg=Style.DIM)
                        + click.style(val, bold=True)
                    )
                    # Pad to clear previous content
                    click.echo(f"{line:<60}")

            except Exception:
                pass
            time.sleep(2)

    thread = threading.Thread(target=_readout, daemon=True)
    thread.start()


def _launch_auto_dashboard(api_key: str, endpoint: str, source_id: str, sensors: list, cameras: list):
    """Launch AI-powered dashboard creation in a background thread.

    Skips if a dashboard_id is already saved in config (reconnect case).
    Shows a spinner while generating, then prints a clickable URL.
    """
    # Check if we already have a dashboard for this device
    config = load_config()
    existing_dashboard = config.get("dashboard_id")
    if existing_dashboard:
        dashboard_url = f"{endpoint}/dashboards/{existing_dashboard}"
        hint(f"Dashboard {Style.ARROW} {dashboard_url}")
        return

    def _create():
        import requests
        try:
            # Wait for data + schema capture to complete
            time.sleep(8)

            spinner = Spinner("Building dashboard...")
            spinner.start()

            headers = {
                "x-api-key": api_key,
                "Content-Type": "application/json",
            }

            dashboard_id = None
            dashboard_url = None

            # Try AI-powered dashboard generation
            resp = requests.post(
                f"{endpoint}/api/auth/cli/generate-dashboard",
                headers=headers,
                json={"source_slug": source_id},
                timeout=30,
            )

            if resp.ok:
                data = resp.json()
                dashboard = data.get("dashboard", {})
                dashboard_id = dashboard.get("id")
                dashboard_url = dashboard.get("url", f"{endpoint}/dashboards/{dashboard_id}")
            else:
                # If not enough metrics yet, create a basic dashboard
                logger.debug("AI dashboard: %s %s", resp.status_code, resp.text)

                resp = requests.post(
                    f"{endpoint}/api/dashboards",
                    headers=headers,
                    json={"name": f"{source_id} Dashboard"},
                    timeout=15,
                )
                if resp.ok:
                    dashboard = resp.json().get("dashboard", {})
                    dashboard_id = dashboard.get("id")
                    if dashboard_id:
                        dashboard_url = f"{endpoint}/dashboards/{dashboard_id}"

            if dashboard_id and dashboard_url:
                # Save to config so we don't recreate on next run
                cfg = load_config()
                cfg["dashboard_id"] = dashboard_id
                save_config(cfg)

                spinner.stop(f"Dashboard ready {Style.ARROW} {dashboard_url}", success_status=True)
            else:
                spinner.stop("Dashboard generation failed", success_status=False)

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

    \b
    Quick start:
        plexus start                   # Interactive setup + stream
        plexus start --key plx_xxx     # Use an API key directly

    \b
    Other commands:
        plexus reset                   # Clear config and start over
    """
    pass


# ─────────────────────────────────────────────────────────────────────────────
# plexus start
# ─────────────────────────────────────────────────────────────────────────────

def _should_use_tui(headless: bool) -> bool:
    """Determine if TUI should be used based on flags and environment."""
    if headless or not sys.stdout.isatty():
        return False
    try:
        import rich  # noqa: F401
        return True
    except ImportError:
        return False


def _quiet_status_line(msg: str, _state={"connected": False}):
    """Status callback that suppresses initial connect noise."""
    if not _state["connected"]:
        # Suppress Connecting/Authenticating/Connected during first connect
        if msg.startswith("Connecting to") or msg == "Authenticating...":
            return
        if msg.startswith("Connected as"):
            _state["connected"] = True
            return
    status_line(msg)


def _run_connector(
    *,
    api_key: str,
    endpoint: str,
    use_tui: bool,
    source_name: Optional[str] = None,
    sensor_hub,
    camera_hub,
    can_adapters,
):
    """Single launch site for the connector (TUI or plain)."""
    if use_tui:
        try:
            from plexus.tui import LiveDashboard
            dashboard = LiveDashboard(sensor_hub=sensor_hub)

            def _connector_fn():
                from plexus.connector import run_connector
                run_connector(
                    api_key=api_key,
                    endpoint=endpoint,
                    source_name=source_name,
                    on_status=dashboard.wrap_status_callback(status_line),
                    sensor_hub=sensor_hub,
                    camera_hub=camera_hub,
                    can_adapters=can_adapters,
                )

            dashboard.run(_connector_fn)
        except ImportError as e:
            warning(str(e).strip())
            hint("Install with: pip install plexus-agent[tui]")
            _run_connector(
                api_key=api_key,
                endpoint=endpoint,
                use_tui=False,
                source_name=source_name,
                sensor_hub=sensor_hub,
                camera_hub=camera_hub,
                can_adapters=can_adapters,
            )
        except KeyboardInterrupt:
            pass
    else:
        from plexus.connector import run_connector
        try:
            run_connector(
                api_key=api_key,
                endpoint=endpoint,
                source_name=source_name,
                on_status=_quiet_status_line,
                sensor_hub=sensor_hub,
                camera_hub=camera_hub,
                can_adapters=can_adapters,
            )
        except KeyboardInterrupt:
            click.echo()
            status_line("Disconnected")
            click.echo()


@main.command()
@click.option("--key", "-k", help="API key from dashboard")
@click.option("--device-id", help="Device ID from dashboard")
@click.option("--scan", is_flag=True, help="Re-detect hardware and update config")
def start(key: Optional[str], device_id: Optional[str], scan: bool):
    """
    Set up and start streaming.

    Handles auth, hardware detection, and streaming. Sensors are detected
    on first run and saved to config. Use --scan to re-detect.

    \b
    Examples:
        plexus start                   # Interactive setup
        plexus start --key plx_xxx     # Use an API key directly
    """
    from plexus.detect import (
        detect_sensors, detect_cameras, detect_can,
        detect_named_sensors, sensors_to_config, load_sensors_from_config,
    )

    slug = device_id

    # ── TUI mode detection ────────────────────────────────────────────────
    # Auto-detect: use TUI if interactive terminal, headless otherwise
    headless = not sys.stdout.isatty()
    use_tui = _should_use_tui(headless)

    # ── Welcome ───────────────────────────────────────────────────────────
    header(f"Plexus Agent v{__version__}")

    # ── Auth ──────────────────────────────────────────────────────────────
    api_key = get_api_key()
    endpoint = get_endpoint()

    # ── Startup wizard for first-time users ──────────────────────────────
    if not api_key and not headless:
        api_key = _startup_wizard(endpoint)

    if key:
        # --key flag: save and use
        config = load_config()
        config["api_key"] = key
        save_config(config)
        api_key = key
    elif not api_key:
        # Terminal sign-up / sign-in flow
        api_key = _terminal_auth(endpoint)

    # Validate key
    if not _validate_api_key(api_key, endpoint):
        error("API key invalid or server unreachable")
        hint("Check your key at app.plexus.company/devices")
        click.echo()
        sys.exit(1)

    # ── Device ID ──────────────────────────────────────────────────────────
    if slug:
        config = load_config()
        config["source_id"] = slug
        save_config(config)

    # ── Hardware ──────────────────────────────────────────────────────────
    cfg = load_config()
    saved_sensors = cfg.get("sensors")
    sensor_hub = None
    sensors = []

    if saved_sensors is not None and not scan:
        # ── Load from config (no prompts, no scanning) ──
        sensor_hub, sensors = load_sensors_from_config(saved_sensors)
        if not sensors and saved_sensors:
            warning("No configured sensors responding (try: plexus start --scan)")
    else:
        # ── First run or --scan: detect and save ──
        info("Scanning hardware...")
        click.echo()

        try:
            sensor_hub, sensors = detect_sensors(1)
        except PermissionError:
            warning("I2C permission denied (run: sudo usermod -aG i2c $USER)")
        except ImportError:
            logger.debug("smbus2 not installed, skipping I2C scan")
        except Exception as e:
            logger.debug("Sensor detection failed: %s", e)

        # Fallback to system metrics if nothing found
        if not sensors:
            try:
                sensor_hub, sensors = detect_named_sensors(["system"])
            except Exception:
                warning("Could not enable system metrics (pip install psutil)")

        # Save to config
        cfg["sensors"] = sensors_to_config(sensors)
        save_config(cfg)

        # Print what was detected
        if sensors:
            for s in sensors:
                s_metrics = getattr(s, 'metrics', None) or (
                    getattr(s.driver, 'metrics', None) if hasattr(s, 'driver') else None
                )
                metrics_str = ", ".join(s_metrics) if s_metrics else ""
                click.echo(
                    f"    {Style.CHECK} "
                    + click.style(f"{s.name:<12}", fg=Style.SUCCESS)
                    + click.style(metrics_str, fg=Style.DIM)
                )
            dim(f"Saved to config ({get_config_path()})")
            dim("Re-detect with: plexus start --scan")
        click.echo()

    # Cameras
    camera_hub = None
    cameras = []
    try:
        camera_hub, cameras = detect_cameras()
    except ImportError:
        logger.debug("Camera support not installed (opencv-python missing)")
    except Exception as e:
        logger.debug("Camera detection failed: %s", e)

    # CAN
    can_adapters, up_can, down_can = detect_can()

    # ── Status block ────────────────────────────────────────────────────
    source_id = get_source_id()
    cfg = load_config()
    device_name = cfg.get("source_name") or source_id

    # Get dashboard URL from config (saved from previous run)
    dashboard_id = cfg.get("dashboard_id")
    dashboard_url = f"{endpoint}/dashboards/{dashboard_id}" if dashboard_id else None

    # Launch dashboard creation in background (first run or if missing)
    _launch_auto_dashboard(
        api_key=api_key,
        endpoint=endpoint,
        source_id=source_id,
        sensors=sensors,
        cameras=cameras,
    )

    _print_status_block(device_name, sensors, dashboard_url)

    # ── Live metric readout (background) ────────────────────────────────
    if sensor_hub and not use_tui:
        _start_metric_readout(sensor_hub)

    # ── Start connector ───────────────────────────────────────────────────
    _run_connector(
        api_key=api_key,
        endpoint=endpoint,
        use_tui=use_tui,
        source_name=device_name,
        sensor_hub=sensor_hub,
        camera_hub=camera_hub,
        can_adapters=can_adapters,
    )


# ─────────────────────────────────────────────────────────────────────────────
# plexus reset
# ─────────────────────────────────────────────────────────────────────────────

@main.command()
def reset():
    """
    Clear all configuration and start over.

    Removes saved API key, device ID, and all other settings.
    Run 'plexus start' again to set up from scratch.
    """
    config_path = get_config_path()

    if not config_path.exists():
        info("Nothing to reset — no config file found.")
        click.echo()
        return

    click.echo()
    if click.confirm(click.style("  Remove all Plexus configuration?", fg=Style.WARNING), default=False):
        config_path.unlink()
        click.echo()
        success("Configuration cleared")
        click.echo()
        hint("Run 'plexus start' to set up again")
        click.echo()
    else:
        click.echo()
        dim("  Cancelled")
        click.echo()


if __name__ == "__main__":
    main()
