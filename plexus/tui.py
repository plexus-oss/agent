"""
Live terminal dashboard for Plexus agent.

Shows real-time telemetry table with metric names, current values,
rates, buffer status, and connection state. Like htop for your hardware.

Usage:
    plexus run --live

Requires: pip install plexus-agent[tui] (installs 'rich')
"""

import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable

# Rich is optional — imported lazily
_rich_available = False
try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
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
            return f"{value:.0f}"
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
        return "-"
    elif hz < 1:
        return f"{hz:.2f}Hz"
    elif hz < 100:
        return f"{hz:.0f}Hz"
    else:
        return f"{hz:.0f}Hz"


def _status_indicator(status: str) -> str:
    """Get a status indicator string."""
    indicators = {
        "connecting": "[yellow]connecting[/yellow]",
        "connected": "[green]streaming[/green]",
        "authenticated": "[green]streaming[/green]",
        "disconnected": "[red]disconnected[/red]",
        "buffering": "[yellow]buffering[/yellow]",
        "error": "[red]error[/red]",
    }
    return indicators.get(status, f"[dim]{status}[/dim]")


def build_dashboard(state: DashboardState) -> Table:
    """Build the Rich table for display."""
    # Header info
    status = _status_indicator(state.connection_status)
    pts_min = f"{state.points_per_min:.0f}" if state.points_per_min > 0 else "0"

    # Main table
    table = Table(
        title=f"  Streaming to Plexus  {status}  {pts_min} pts/min  {state.total_errors} errors  {state.uptime}",
        title_style="bold",
        show_header=True,
        header_style="bold dim",
        border_style="dim",
        pad_edge=True,
        expand=True,
    )

    table.add_column("Metric", style="cyan", no_wrap=True, ratio=3)
    table.add_column("Value", justify="right", ratio=2)
    table.add_column("Rate", justify="right", style="dim", ratio=1)
    table.add_column("Buffer", justify="right", style="dim", ratio=1)
    table.add_column("Status", justify="center", ratio=1)

    with state._lock:
        if not state.metrics:
            table.add_row(
                "[dim]Waiting for data...[/dim]", "", "", "", ""
            )
        else:
            sorted_metrics = sorted(state.metrics.values(), key=lambda m: m.name)
            for metric in sorted_metrics:
                # Staleness check
                age = time.time() - metric.last_update if metric.last_update > 0 else 999
                if age < 5:
                    status_cell = "[green]streaming[/green]"
                elif age < 30:
                    status_cell = "[yellow]stale[/yellow]"
                else:
                    status_cell = "[red]timeout[/red]"

                buffer_cell = str(state.buffer_size) if state.buffer_size > 0 else "0"

                table.add_row(
                    metric.name,
                    metric.value,
                    _format_rate(metric.rate_hz),
                    buffer_cell,
                    status_cell,
                )

    return table


class LiveDashboard:
    """
    Live terminal dashboard that wraps a PlexusConnector.

    Intercepts status updates and metric sends to display a
    real-time table in the terminal.
    """

    def __init__(self):
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
        self._live: Optional[Live] = None

    def on_status(self, msg: str):
        """Status callback for the connector."""
        # Parse status messages to update dashboard state
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
        """Called when a metric is sent (hook into streaming)."""
        self.state.update_metric(name, value, timestamp)

    def wrap_status_callback(self, original_callback: Optional[Callable] = None) -> Callable:
        """Return a status callback that updates both the TUI and the original callback."""
        def wrapped(msg: str):
            self.on_status(msg)
            if original_callback:
                original_callback(msg)
        return wrapped

    def run(self, connector_fn: Callable):
        """
        Run the live dashboard with a connector function.

        Args:
            connector_fn: Function that starts the connector (blocking).
                          Will be run in a background thread.
        """
        self.state = DashboardState()

        # Run connector in background thread
        connector_thread = threading.Thread(target=connector_fn, daemon=True)
        connector_thread.start()

        try:
            with Live(
                build_dashboard(self.state),
                console=self.console,
                refresh_per_second=4,
                screen=False,
            ) as live:
                self._live = live
                while connector_thread.is_alive():
                    live.update(build_dashboard(self.state))
                    time.sleep(0.25)
        except KeyboardInterrupt:
            pass
        finally:
            self._live = None
