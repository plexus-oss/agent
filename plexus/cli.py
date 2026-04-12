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


def _pip_install_cmd(package: str) -> str:
    """Return the right install command for the user's environment."""
    import shutil
    if shutil.which("pipx") and ".local/share/pipx" in (sys.prefix or ""):
        return f"pipx inject plexus-python {package}"
    return f"pip install {package}"


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


def _print_status_block(device_name: str, sensors: list, endpoint: str):
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
    label("Dashboard", endpoint)
    click.echo()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _validate_api_key(api_key: str, endpoint: str) -> bool:
    """Make a lightweight request to verify the API key is valid.

    Returns True if the key is accepted, False otherwise.
    """
    from plexus.config import get_gateway_url
    try:
        import requests
        resp = requests.post(
            f"{get_gateway_url()}/ingest",
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
            hint("Install with: pip install plexus-python[tui]")
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

        hw_detected = False
        try:
            sensor_hub, sensors = detect_sensors()
            hw_detected = bool(sensors)
        except PermissionError:
            warning("I2C permission denied (run: sudo usermod -aG i2c $USER)")
        except ImportError:
            warning("I2C sensor support not installed")
            dim(f"Install with: {_pip_install_cmd('smbus2')}")
        except OSError as e:
            warning(f"I2C bus error: {e}")
        except Exception as e:
            warning(f"Sensor detection failed: {e}")

        # Fallback to system metrics if nothing found
        if not sensors:
            try:
                sensor_hub, sensors = detect_named_sensors(["system"])
            except Exception:
                warning("Could not enable system metrics (pip install psutil)")

        # Only save to config if we found real hardware sensors —
        # don't persist fallback-only so next run re-scans
        if hw_detected:
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
        warning("Camera support not installed")
        dim(f"Install with: {_pip_install_cmd('picamera2')}")
        dim(f"         or: {_pip_install_cmd('opencv-python')}")
    except Exception as e:
        warning(f"Camera detection failed: {e}")

    # CAN
    can_adapters, up_can, down_can = detect_can()

    # ── Status block ────────────────────────────────────────────────────
    source_id = get_source_id()
    cfg = load_config()
    device_name = cfg.get("source_name") or source_id

    _print_status_block(device_name, sensors, endpoint)

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
