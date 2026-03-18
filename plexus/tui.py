"""
Live terminal dashboard for Plexus agent.

Full-screen, keyboard-driven TUI for monitoring device telemetry.
Braille charts, color gradients, metric cards. Like htop for hardware.

Usage:
    plexus start  (TUI is the default when Rich is available)
    plexus start --headless  (disable TUI)

Optional: pip install plexus-agent[tui] (installs 'rich' for TUI)
"""

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable

# Rich is optional — imported lazily
_rich_available = False
try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.text import Text
    from rich.panel import Panel
    from rich.style import Style
    from rich import box
    _rich_available = True
except ImportError:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Braille Chart (ported from ONCE's chart.go)
# ─────────────────────────────────────────────────────────────────────────────

BRAILLE_BASE = 0x2800
# Left column dots (bottom to top): dots 7, 3, 2, 1
LEFT_DOTS = [0x40, 0x04, 0x02, 0x01]
# Right column dots (bottom to top): dots 8, 6, 5, 4
RIGHT_DOTS = [0x80, 0x20, 0x10, 0x08]

# Green → amber gradient (8 steps, approximating OKLCH blend)
GRADIENT = [
    "#50fa7b",  # green
    "#6ee867",
    "#8ed654",
    "#aac443",
    "#c4b235",
    "#dba02a",
    "#ef8e22",
    "#f5a623",  # amber/orange
]


def _braille_column(height: int, row_bottom: int, dots: list) -> int:
    """Return braille bits for one column based on how many dots to fill."""
    if height <= row_bottom:
        return 0
    bits = 0
    dots_to_fill = min(height - row_bottom, 4)
    for i in range(dots_to_fill):
        bits |= dots[i]
    return bits


def braille_chart(data: List[float], width: int, height: int) -> List[Text]:
    """Render a braille chart as a list of Rich Text lines.

    Each character column encodes 2 data points. Each character row
    encodes 4 vertical dots. Color gradient: green (bottom) to amber (top).

    Args:
        data: time series values
        width: character width of chart area
        height: character height (rows)

    Returns:
        List of Rich Text objects, one per row (top to bottom)
    """
    if width <= 0 or height <= 0:
        return [Text("")]

    data_points = width * 2
    # Pad data to fill width
    padded = [0.0] * data_points
    src_start = max(0, len(data) - data_points)
    dst_start = max(0, data_points - len(data))
    for i, v in enumerate(data[src_start:]):
        padded[dst_start + i] = v

    max_val = max(padded) if padded else 1.0
    if max_val == 0:
        max_val = 1.0

    dots_height = height * 4

    # Calculate dot heights for each data point
    heights = []
    for v in padded:
        h = int((v / max_val) * dots_height)
        if v > 0 and h == 0:
            h = 1
        heights.append(h)

    lines = []
    for row in range(height):
        row_bottom = (height - 1 - row) * 4

        chars = []
        for col in range(width):
            left_idx = col * 2
            right_idx = col * 2 + 1

            char = BRAILLE_BASE
            if left_idx < len(heights):
                char |= _braille_column(heights[left_idx], row_bottom, LEFT_DOTS)
            if right_idx < len(heights):
                char |= _braille_column(heights[right_idx], row_bottom, RIGHT_DOTS)

            chars.append(chr(char))

        # Color gradient: t=0 (bottom) → green, t=1 (top) → amber
        t = (height - 1 - row) / max(height - 1, 1)
        gradient_idx = int(t * (len(GRADIENT) - 1))
        color = GRADIENT[min(gradient_idx, len(GRADIENT) - 1)]

        line = Text("".join(chars), style=Style(color=color))
        lines.append(line)

    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Braille Bar Gauge (ported from ONCE's renderBar)
# ─────────────────────────────────────────────────────────────────────────────

BAR_FULL = "\u28ff"       # ⣿
BAR_ROUND_LEFT = "\u28be"  # ⢾
BAR_ROUND_RIGHT = "\u2537" # ⡷  — using closest available
BAR_EMPTY = "\u2800"       # ⠀ (braille blank)


def braille_bar(value: float, max_val: float, width: int, color: str = "green") -> Text:
    """Render a braille bar gauge with rounded ends."""
    if width <= 0 or max_val <= 0:
        return Text("")

    filled = int((value / max_val) * width)
    filled = min(filled, width)

    result = Text()
    for i in range(width):
        if i < filled:
            if i == 0:
                ch = BAR_ROUND_LEFT
            elif i == filled - 1 and filled < width:
                ch = BAR_FULL
            else:
                ch = BAR_FULL
            result.append(ch, style=Style(color=color))
        else:
            result.append(BAR_EMPTY, style=Style(color="#3a3a4a"))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Logo
# ─────────────────────────────────────────────────────────────────────────────

LOGO_ART = [
    " ┌─┐",
    "─┤ ├─",
    " └─┘",
]

LOGO_TEXT = "plexus"


def render_logo(tick: int) -> Text:
    """Render the logo with a diagonal shine animation."""
    full = Text(justify="center")

    # Build the connector graphic
    for i, line in enumerate(LOGO_ART):
        shine_start = tick - i
        shine_end = shine_start + 3

        t = Text()
        for j, ch in enumerate(line):
            if 0 <= tick and shine_start <= j < shine_end:
                t.append(ch, style=Style(color="#f5a623", bold=True))
            else:
                t.append(ch, style=Style(color="#5a5a6a"))
        full.append(t)
        full.append("\n")

    # Brand name
    name = Text(justify="center")
    for j, ch in enumerate(LOGO_TEXT):
        shine_start = tick - len(LOGO_ART) - j
        if 0 <= tick and -2 <= shine_start <= 1:
            name.append(ch, style=Style(color="#f5a623", bold=True))
        else:
            name.append(ch, style=Style(color="#8a8a9a", bold=True))
    full.append(name)

    return full


# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────

HISTORY_LEN = 120  # 2 minutes at 1Hz


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
    _history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_LEN))

    def update(self, value, timestamp: Optional[float] = None):
        now = timestamp or time.time()
        self.raw_value = value if isinstance(value, (int, float)) else 0.0
        self.value = _format_value(value)
        self.last_update = now
        self.update_count += 1

        if isinstance(value, (int, float)):
            self._history.append(float(value))

        self._timestamps.append(now)
        cutoff = now - 2.0
        self._timestamps = [t for t in self._timestamps if t > cutoff]

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
    sort_mode: str = "name"
    paused: bool = False
    device_name: str = ""
    shine_tick: int = -1
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


# ─────────────────────────────────────────────────────────────────────────────
# Formatting
# ─────────────────────────────────────────────────────────────────────────────

def _format_value(value) -> str:
    """Format a metric value for display."""
    if isinstance(value, float):
        if abs(value) < 0.001 and value != 0:
            return f"{value:.4e}"
        elif abs(value) < 10:
            return f"{value:.2f}"
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


# ─────────────────────────────────────────────────────────────────────────────
# Metric Card
# ─────────────────────────────────────────────────────────────────────────────

def build_metric_card(metric: MetricState, width: int, chart_height: int = 4) -> Panel:
    """Build a single metric card with braille chart and value."""
    history = list(metric._history)
    chart_width = max(width - 4, 8)  # padding inside panel

    # Chart
    chart_lines = braille_chart(history, chart_width, chart_height)

    # Value + rate line
    age = time.time() - metric.last_update if metric.last_update > 0 else 999
    if age < 5:
        dot_color = "green"
        dot = "●"
    elif age < 30:
        dot_color = "yellow"
        dot = "●"
    else:
        dot_color = "red"
        dot = "●"

    value_line = Text()
    value_line.append(f" {metric.value}", style=Style(bold=True))
    rate_str = _format_rate(metric.rate_hz)
    if rate_str:
        value_line.append(f"  {rate_str}", style=Style(color="#5a5a6a"))
    value_line.append(f"  {dot}", style=Style(color=dot_color))

    # Assemble card content
    content = Text()
    for line in chart_lines:
        content.append(" ")
        content.append_text(line)
        content.append("\n")
    content.append_text(value_line)

    # Clean display name
    display_name = metric.name.replace("_", " ").replace(".", " ")

    return Panel(
        content,
        title=f"[dim]{display_name}[/dim]",
        title_align="left",
        border_style=Style(color="#3a3a4a"),
        box=box.ROUNDED,
        width=width,
        padding=(0, 1),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard Builder
# ─────────────────────────────────────────────────────────────────────────────

def build_dashboard(state: DashboardState) -> Group:
    """Build the full-screen dashboard layout."""
    parts = []

    # ── Logo ──
    parts.append(Text(""))
    parts.append(render_logo(state.shine_tick))
    parts.append(Text(""))

    # ── Status line ──
    status_map = {
        "connecting": ("#e5c07b", "connecting..."),
        "connected": ("#50fa7b", "streaming"),
        "authenticated": ("#50fa7b", "streaming"),
        "disconnected": ("#ff5555", "disconnected"),
        "buffering": ("#e5c07b", "buffering"),
        "error": ("#ff5555", "error"),
    }
    color, label = status_map.get(
        state.connection_status, ("#5a5a6a", state.connection_status)
    )

    status_line = Text(justify="center")
    status_line.append("● ", style=Style(color=color, bold=True))
    status_line.append(label, style=Style(color=color))
    status_line.append("    ")
    status_line.append(state.uptime, style=Style(color="#5a5a6a"))

    pts = f"{state.points_per_min:.0f}" if state.points_per_min > 0 else "0"
    status_line.append("    ")
    status_line.append(pts, style=Style(bold=True))
    status_line.append(" pts/min", style=Style(color="#5a5a6a"))

    if state.total_errors > 0:
        status_line.append(f"    {state.total_errors} err", style=Style(color="#ff5555"))
    if state.paused:
        status_line.append("    PAUSED", style=Style(color="#e5c07b", bold=True))

    parts.append(status_line)
    parts.append(Text(""))

    # ── Metric cards ──
    with state._lock:
        if not state.metrics:
            waiting = Text("  waiting for first reading...", style=Style(color="#5a5a6a", italic=True))
            parts.append(waiting)
        else:
            if state.sort_mode == "rate":
                sorted_metrics = sorted(state.metrics.values(), key=lambda m: m.rate_hz, reverse=True)
            elif state.sort_mode == "recent":
                sorted_metrics = sorted(state.metrics.values(), key=lambda m: m.last_update, reverse=True)
            else:
                sorted_metrics = sorted(state.metrics.values(), key=lambda m: m.name)

            # Determine card width based on metric count
            n = len(sorted_metrics)
            if n <= 4:
                card_width = 40
                chart_height = 5
            else:
                card_width = 36
                chart_height = 4

            for metric in sorted_metrics:
                card = build_metric_card(metric, card_width, chart_height)
                parts.append(card)

    parts.append(Text(""))

    # ── Footer ──
    footer = Text(justify="center")
    for key, label in [("q", "quit"), ("p", "pause"), ("s", "sort")]:
        footer.append(f" {key}", style=Style(bold=True))
        footer.append(f" {label}  ", style=Style(color="#5a5a6a"))

    parts.append(footer)

    return Group(*parts)


# ─────────────────────────────────────────────────────────────────────────────
# Keyboard
# ─────────────────────────────────────────────────────────────────────────────

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
            return

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


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

SHINE_INTERVAL = 10.0   # seconds between shines
SHINE_MAX = 20          # tick count for one shine pass


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

    def _shine_loop(self):
        """Animate the logo shine periodically."""
        time.sleep(2.0)  # initial delay
        while not self._stop_event.is_set():
            # Run one shine pass
            for tick in range(SHINE_MAX):
                if self._stop_event.is_set():
                    return
                self.state.shine_tick = tick
                time.sleep(0.05)
            self.state.shine_tick = -1
            # Wait for next shine
            for _ in range(int(SHINE_INTERVAL * 10)):
                if self._stop_event.is_set():
                    return
                time.sleep(0.1)

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
            threading.Thread(target=self._sensor_read_loop, daemon=True).start()

        # Start keyboard reader
        self._key_reader = _KeyReader(self.state, self._stop_event)
        self._key_reader.start()

        # Start logo shine animation
        threading.Thread(target=self._shine_loop, daemon=True).start()

        try:
            with Live(
                build_dashboard(self.state),
                console=self.console,
                refresh_per_second=4,
                screen=True,
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
