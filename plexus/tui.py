"""
Live terminal dashboard for Plexus agent.

Full-screen, keyboard-driven TUI for monitoring device telemetry.
Like htop for your hardware.

Usage:
    plexus start  (TUI is the default when Rich is available)
    plexus start --headless  (disable TUI)

Optional: pip install plexus-agent[tui] (installs 'rich' for TUI)
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable

# Rich is optional — imported lazily
_rich_available = False
try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.table import Table
    from rich.text import Text
    _rich_available = True
except ImportError:
    pass


@dataclass
class MetricState:
    """Tracks the state of a single metric for display."""
    name: str
    value: str = ""
    raw_value: float = 0.0
    rate_hz: float = 0.0
    last_update: float = 0.0
    update_count: int = 0
    _timestamps: List[float] = field(default_factory=list)

    def update(self, value, timestamp: Optional[float] = None):
        now = timestamp or time.time()
        self.raw_value = value if isinstance(value, (int, float)) else 0.0
        self.value = _format_value(value)
        self.last_update = now
        self.update_count += 1

        # Track timestamps for rate calculation
        self._timestamps.append(now)
        # Keep last 2 seconds of timestamps
        cutoff = now - 2.0
        self._timestamps = [t for t in self._timestamps if t > cutoff]

        # Calculate rate
        if len(self._timestamps) >= 2:
            span = self._timestamps[-1] - self._timestamps[0]
            if span > 0:
                self.rate_hz = (len(self._timestamps) - 1) / span


@dataclass
class DashboardState:
    """Global state for the live dashboard."""
    metrics: Dict[str, MetricState] = field(default_factory=dict)
    connection_status: str = "connecting"
    total_sent: int = 0
    total_errors: int = 0
    buffer_size: int = 0
    start_time: float = field(default_factory=time.time)
    sort_mode: str = "name"  # "name", "rate", "recent"
    paused: bool = False
    device_name: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def update_metric(self, name: str, value, timestamp: Optional[float] = None):
        with self._lock:
            if name not in self.metrics:
                self.metrics[name] = MetricState(name=name)
            self.metrics[name].update(value, timestamp)
            self.total_sent += 1

    def set_status(self, status: str):
        with self._lock:
            self.connection_status = status

    def set_buffer(self, size: int):
        with self._lock:
            self.buffer_size = size

    def increment_errors(self):
        with self._lock:
            self.total_errors += 1

    def cycle_sort(self):
        modes = ["name", "rate", "recent"]
        idx = modes.index(self.sort_mode)
        self.sort_mode = modes[(idx + 1) % len(modes)]

    def toggle_pause(self):
        self.paused = not self.paused

    @property
    def uptime(self) -> str:
        elapsed = int(time.time() - self.start_time)
        if elapsed < 60:
            return f"{elapsed}s"
        elif elapsed < 3600:
            return f"{elapsed // 60}m {elapsed % 60}s"
        else:
            return f"{elapsed // 3600}h {(elapsed % 3600) // 60}m"

    @property
    def points_per_min(self) -> float:
        elapsed = time.time() - self.start_time
        if elapsed < 1:
            return 0
        return self.total_sent / elapsed * 60


def _format_value(value) -> str:
    """Format a metric value for display."""
    if isinstance(value, float):
        if abs(value) < 0.001 and value != 0:
            return f"{value:.4e}"
        elif abs(value) < 10:
            return f"{value:.3f}"
        elif abs(value) < 1000:
            return f"{value:.1f}"
        else:
            return f"{value:,.0f}"
    elif isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, int):
        return str(value)
    elif isinstance(value, str):
        return value[:32]
    elif isinstance(value, dict):
        return str(value)[:40]
    elif isinstance(value, list):
        return str(value)[:40]
    return str(value)[:32]


def _format_rate(hz: float) -> str:
    """Format a rate for display."""
    if hz == 0:
        return ""
    elif hz < 1:
        return f"{hz:.1f} Hz"
    else:
        return f"{hz:.0f} Hz"


def build_dashboard(state: DashboardState) -> Group:
    """Build the full-screen dashboard layout."""
    from plexus import __version__

    # ── Status dot ──
    status_map = {
        "connecting": ("yellow", "connecting"),
        "connected": ("green", "streaming"),
        "authenticated": ("green", "streaming"),
        "disconnected": ("red", "disconnected"),
        "buffering": ("yellow", "buffering"),
        "error": ("red", "error"),
    }
    color, label = status_map.get(state.connection_status, ("dim", state.connection_status))

    # ── Header line ──
    header = Text()
    header.append("  Plexus", style="bold")
    header.append(f"  v{__version__}", style="dim")
    header.append("    ")
    header.append("●", style=color)
    header.append(f" {label}", style=color)
    header.append(f"    {state.uptime}", style="dim")
    if state.paused:
        header.append("    paused", style="yellow bold")

    # ── Stats bar ──
    pts = f"{state.points_per_min:.0f}" if state.points_per_min > 0 else "0"
    stats = Text()
    stats.append(f"  {state.total_sent} pts", style="dim")
    stats.append(f"   {pts}/min", style="dim")
    if state.total_errors > 0:
        stats.append(f"   {state.total_errors} errors", style="red dim")
    if state.buffer_size > 0:
        stats.append(f"   buf:{state.buffer_size}", style="yellow dim")

    # ── Metric table ──
    table = Table(
        show_header=True,
        header_style="dim",
        box=None,
        pad_edge=True,
        padding=(0, 2),
        expand=False,
        min_width=50,
    )

    table.add_column("  Metric", style="cyan", no_wrap=True, min_width=24)
    table.add_column("Value", justify="right", min_width=12)
    table.add_column("Rate", justify="right", style="dim", min_width=8)
    table.add_column("", min_width=3)  # status dot

    with state._lock:
        if not state.metrics:
            table.add_row(
                "  [dim]waiting for data...[/dim]", "", "", ""
            )
        else:
            if state.sort_mode == "rate":
                sorted_metrics = sorted(state.metrics.values(), key=lambda m: m.rate_hz, reverse=True)
            elif state.sort_mode == "recent":
                sorted_metrics = sorted(state.metrics.values(), key=lambda m: m.last_update, reverse=True)
            else:
                sorted_metrics = sorted(state.metrics.values(), key=lambda m: m.name)

            for metric in sorted_metrics:
                age = time.time() - metric.last_update if metric.last_update > 0 else 999
                if age < 5:
                    dot = "[green]●[/green]"
                elif age < 30:
                    dot = "[yellow]●[/yellow]"
                else:
                    dot = "[red]●[/red]"

                # Clean metric name: cpu.temperature → cpu temperature
                display_name = "  " + metric.name.replace("_", " ").replace(".", " > ")

                table.add_row(
                    display_name,
                    f"[bold]{metric.value}[/bold]",
                    _format_rate(metric.rate_hz),
                    dot,
                )

    # ── Footer ──
    sort_labels = {"name": "name", "rate": "rate", "recent": "recent"}
    footer = Text()
    footer.append("  q", style="bold dim")
    footer.append(" quit  ", style="dim")
    footer.append("p", style="bold dim")
    footer.append(" pause  ", style="dim")
    footer.append("s", style="bold dim")
    footer.append(f" sort:{sort_labels[state.sort_mode]}  ", style="dim")

    # Compose the full layout
    spacer = Text("")
    return Group(header, stats, spacer, table, spacer, footer)


class _KeyReader:
    """Non-blocking keyboard reader for TUI shortcuts."""

    def __init__(self, state: DashboardState, stop_event: threading.Event):
        self.state = state
        self.stop_event = stop_event
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        import sys
        try:
            import termios
            import tty
        except ImportError:
            return  # Not a Unix terminal

        fd = sys.stdin.fileno()
        try:
            old = termios.tcgetattr(fd)
        except termios.error:
            return

        try:
            tty.setcbreak(fd)
            while not self.stop_event.is_set():
                import select
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    ch = sys.stdin.read(1)
                    if ch == "q":
                        self.stop_event.set()
                    elif ch == "p":
                        self.state.toggle_pause()
                    elif ch == "s":
                        self.state.cycle_sort()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


class LiveDashboard:
    """
    Full-screen live terminal dashboard.

    Takes over the terminal with an alternate screen buffer,
    reads sensors locally, and displays real-time metrics.
    """

    def __init__(self, sensor_hub=None):
        if not _rich_available:
            raise ImportError(
                "\n"
                "  Live dashboard requires 'rich'.\n"
                "\n"
                "  Install with:\n"
                "    pip install plexus-agent[tui]\n"
                "\n"
                "  Or: pip install rich\n"
            )
        self.state = DashboardState()
        self.console = Console()
        self.sensor_hub = sensor_hub
        self._live: Optional[Live] = None
        self._stop_event = threading.Event()
        self._key_reader: Optional[_KeyReader] = None

    def on_status(self, msg: str):
        """Status callback for the connector."""
        lower = msg.lower()
        if "connected as" in lower or "authenticated" in lower:
            self.state.set_status("connected")
        elif "connecting" in lower:
            self.state.set_status("connecting")
        elif "disconnected" in lower or "error" in lower:
            self.state.set_status("disconnected")
        elif "reconnecting" in lower:
            self.state.set_status("connecting")

    def on_metric(self, name: str, value, timestamp: Optional[float] = None):
        """Called when a metric is sent."""
        self.state.update_metric(name, value, timestamp)

    def wrap_status_callback(self, original_callback: Optional[Callable] = None) -> Callable:
        """Return a status callback that updates both the TUI and the original callback."""
        def wrapped(msg: str):
            self.on_status(msg)
            if original_callback:
                original_callback(msg)
        return wrapped

    def _sensor_read_loop(self):
        """Read sensors locally and feed metrics into the TUI state."""
        while not self._stop_event.is_set():
            try:
                readings = self.sensor_hub.read_all()
                for r in readings:
                    self.on_metric(r.metric, r.value)
            except Exception:
                pass
            time.sleep(1)

    def run(self, connector_fn: Callable):
        """
        Run the live dashboard with a connector function.

        Args:
            connector_fn: Function that starts the connector (blocking).
                          Will be run in a background thread.
        """
        self.state = DashboardState()
        self._stop_event.clear()

        # Run connector in background thread
        connector_thread = threading.Thread(target=connector_fn, daemon=True)
        connector_thread.start()

        # Start local sensor reader to feed metrics into TUI
        if self.sensor_hub:
            sensor_thread = threading.Thread(target=self._sensor_read_loop, daemon=True)
            sensor_thread.start()

        # Start keyboard reader
        self._key_reader = _KeyReader(self.state, self._stop_event)
        self._key_reader.start()

        try:
            with Live(
                build_dashboard(self.state),
                console=self.console,
                refresh_per_second=4,
                screen=True,  # Alternate screen buffer — hides setup output
            ) as live:
                self._live = live
                while connector_thread.is_alive() and not self._stop_event.is_set():
                    if not self.state.paused:
                        live.update(build_dashboard(self.state))
                    time.sleep(0.25)
        except KeyboardInterrupt:
            pass
        finally:
            self._stop_event.set()
            self._live = None
