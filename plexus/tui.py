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
    from rich.columns import Columns
    from rich import box
    _rich_available = True
except ImportError:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Braille Chart (ported from ONCE's chart.go)
# ─────────────────────────────────────────────────────────────────────────────

BRAILLE_BASE = 0x2800
LEFT_DOTS = [0x40, 0x04, 0x02, 0x01]
RIGHT_DOTS = [0x80, 0x20, 0x10, 0x08]

# Green → amber gradient (8 steps)
GRADIENT = [
    "#50fa7b", "#6ee867", "#8ed654", "#aac443",
    "#c4b235", "#dba02a", "#ef8e22", "#f5a623",
]

BORDER_COLOR = "#4a4a5a"
MUTED_COLOR = "#6a6a7a"
DIM_COLOR = "#3a3a4a"


def _braille_column(height: int, row_bottom: int, dots: list) -> int:
    if height <= row_bottom:
        return 0
    bits = 0
    for i in range(min(height - row_bottom, 4)):
        bits |= dots[i]
    return bits


def braille_chart(
    data: List[float], width: int, height: int,
    scale_min: Optional[float] = None, scale_max: Optional[float] = None,
) -> List[Text]:
    """Render a braille chart with auto-scaling that shows variation.

    Uses a tight min/max window around the data so even near-constant
    values show meaningful waveforms instead of flat blocks.
    """
    if width <= 0 or height <= 0:
        return [Text("")]

    data_points = width * 2
    padded = [0.0] * data_points
    src_start = max(0, len(data) - data_points)
    dst_start = max(0, data_points - len(data))
    for i, v in enumerate(data[src_start:]):
        padded[dst_start + i] = v

    # Auto-scale: use data range with 10% padding so values aren't all
    # pinned to the top. This reveals variation in near-constant metrics.
    if scale_max is not None and scale_min is not None:
        lo, hi = scale_min, scale_max
    else:
        actual_vals = [v for v in padded if v != 0] or padded
        lo = min(actual_vals)
        hi = max(actual_vals)
        spread = hi - lo
        if spread == 0:
            spread = abs(hi) * 0.1 or 1.0
        # Add 10% padding
        lo = lo - spread * 0.1
        hi = hi + spread * 0.1

    rng = hi - lo
    if rng == 0:
        rng = 1.0

    dots_height = height * 4

    heights = []
    for v in padded:
        if v == 0 and lo > 0:
            heights.append(0)
        else:
            normalized = max(0.0, min(1.0, (v - lo) / rng))
            h = int(normalized * dots_height)
            if v > lo and h == 0:
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

        # Gradient: bottom → green, top → amber
        t = (height - 1 - row) / max(height - 1, 1)
        color = GRADIENT[min(int(t * (len(GRADIENT) - 1)), len(GRADIENT) - 1)]
        lines.append(Text("".join(chars), style=Style(color=color)))

    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Logo
# ─────────────────────────────────────────────────────────────────────────────

LOGO_LINES = [
    "    ┌─┐    ",
    " ───┤ ├─── ",
    "    └─┘    ",
    "  plexus   ",
]


def render_logo(tick: int) -> Text:
    """Render the logo with a diagonal shine animation."""
    result = Text(justify="center")
    for row, line in enumerate(LOGO_LINES):
        if row > 0:
            result.append("\n")
        for col, ch in enumerate(line):
            diag = col - row
            shine_hit = 0 <= tick and (tick - 3) <= diag <= tick
            if shine_hit:
                result.append(ch, style=Style(color="#f5a623", bold=True))
            elif row == 3:  # brand name row
                result.append(ch, style=Style(color="#8a8a9a", bold=True))
            else:
                result.append(ch, style=Style(color=MUTED_COLOR))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────

HISTORY_LEN = 120


@dataclass
class MetricState:
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
    metrics: Dict[str, MetricState] = field(default_factory=dict)
    connection_status: str = "connecting"
    total_sent: int = 0
    total_errors: int = 0
    buffer_size: int = 0
    start_time: float = field(default_factory=time.time)
    sort_mode: str = "name"
    paused: bool = False
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
    elif isinstance(value, (dict, list)):
        return str(value)[:40]
    return str(value)[:32]


def _compact_value(value: float) -> str:
    """Format a number compactly for scale labels."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def _format_rate(hz: float) -> str:
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
    """Build a single metric card with braille chart, scale labels, and value."""
    history = list(metric._history)
    label_width = 6
    chart_width = max(width - 4 - label_width - 1, 4)

    # Compute scale from history
    actual_vals = [v for v in history if v != 0] or history or [0.0]
    lo = min(actual_vals)
    hi = max(actual_vals)
    spread = hi - lo
    if spread == 0:
        spread = abs(hi) * 0.1 or 1.0
    scale_lo = lo - spread * 0.1
    scale_hi = hi + spread * 0.1

    # Chart lines
    chart_lines = braille_chart(history, chart_width, chart_height, scale_lo, scale_hi)

    # Status dot
    age = time.time() - metric.last_update if metric.last_update > 0 else 999
    dot_color = "green" if age < 5 else ("yellow" if age < 30 else "red")

    # Assemble content: scale label | chart, per row
    content = Text()
    for i, chart_line in enumerate(chart_lines):
        # Scale labels: max on first row, min on last row
        if i == 0:
            label = _compact_value(scale_hi)
        elif i == len(chart_lines) - 1:
            label = _compact_value(max(scale_lo, 0))
        else:
            label = ""
        padded_label = label.rjust(label_width)
        content.append(padded_label, style=Style(color=MUTED_COLOR))
        content.append(" ")
        content.append_text(chart_line)
        content.append("\n")

    # Value line
    value_line = Text()
    value_line.append(" " * label_width + " ")
    value_line.append(metric.value, style=Style(bold=True))
    rate_str = _format_rate(metric.rate_hz)
    if rate_str:
        value_line.append(f"  {rate_str}", style=Style(color=MUTED_COLOR))
    value_line.append("  ●", style=Style(color=dot_color))
    content.append_text(value_line)

    display_name = metric.name.replace("_", " ").replace(".", " ")

    return Panel(
        content,
        title=f"[{MUTED_COLOR}]{display_name}[/{MUTED_COLOR}]",
        title_align="left",
        border_style=Style(color=BORDER_COLOR),
        box=box.ROUNDED,
        width=width,
        padding=(0, 1),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard Builder
# ─────────────────────────────────────────────────────────────────────────────

def build_dashboard(state: DashboardState, term_width: int = 0) -> Group:
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
        state.connection_status, (MUTED_COLOR, state.connection_status)
    )

    status_line = Text(justify="center")
    status_line.append("● ", style=Style(color=color, bold=True))
    status_line.append(label, style=Style(color=color))
    status_line.append("    ")
    status_line.append(state.uptime, style=Style(color=MUTED_COLOR))
    pts = f"{state.points_per_min:.0f}" if state.points_per_min > 0 else "0"
    status_line.append("    ")
    status_line.append(pts, style=Style(bold=True))
    status_line.append(" pts/min", style=Style(color=MUTED_COLOR))
    if state.buffer_size > 0:
        status_line.append("    ")
        status_line.append(f"{state.buffer_size:,}", style=Style(bold=True))
        status_line.append(" buffered", style=Style(color="#e5c07b"))
    if state.total_errors > 0:
        status_line.append(f"    {state.total_errors} err", style=Style(color="#ff5555"))
    if state.paused:
        status_line.append("    PAUSED", style=Style(color="#e5c07b", bold=True))

    parts.append(status_line)
    parts.append(Text(""))

    # ── Metric cards ──
    with state._lock:
        if not state.metrics:
            waiting = Text(
                "  waiting for first reading...",
                style=Style(color=MUTED_COLOR, italic=True),
                justify="center",
            )
            parts.append(waiting)
        else:
            if state.sort_mode == "rate":
                sorted_metrics = sorted(state.metrics.values(), key=lambda m: m.rate_hz, reverse=True)
            elif state.sort_mode == "recent":
                sorted_metrics = sorted(state.metrics.values(), key=lambda m: m.last_update, reverse=True)
            else:
                sorted_metrics = sorted(state.metrics.values(), key=lambda m: m.name)

            n = len(sorted_metrics)
            usable = term_width if term_width > 0 else 100

            # Responsive layout: side-by-side if they fit, stacked if not
            if n <= 3 and usable >= n * 34 + (n - 1) * 1:
                # Side by side
                card_width = min((usable - (n - 1)) // n, 42)
                chart_height = 6
                cards = []
                for metric in sorted_metrics:
                    cards.append(build_metric_card(metric, card_width, chart_height))
                parts.append(Columns(cards, padding=(0, 1), expand=False))
            elif n <= 6 and usable >= 2 * 34 + 1:
                # Two per row
                card_width = min((usable - 1) // 2, 42)
                chart_height = 5
                row = []
                for i, metric in enumerate(sorted_metrics):
                    row.append(build_metric_card(metric, card_width, chart_height))
                    if len(row) == 2 or i == n - 1:
                        parts.append(Columns(row, padding=(0, 1), expand=False))
                        row = []
            else:
                # Stacked
                card_width = min(usable, 60)
                chart_height = 4
                for metric in sorted_metrics:
                    parts.append(build_metric_card(metric, card_width, chart_height))

    parts.append(Text(""))

    # ── Footer ──
    footer = Text(justify="center")
    for key, label in [("q", "quit"), ("p", "pause"), ("s", "sort")]:
        footer.append(f" {key}", style=Style(bold=True))
        footer.append(f" {label}  ", style=Style(color=MUTED_COLOR))
    parts.append(footer)

    return Group(*parts)


# ─────────────────────────────────────────────────────────────────────────────
# Keyboard
# ─────────────────────────────────────────────────────────────────────────────

class _KeyReader:
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

SHINE_INTERVAL = 10.0
SHINE_MAX = 20


class LiveDashboard:
    """Full-screen live terminal dashboard."""

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
        lower = msg.lower()
        if "connected as" in lower or "authenticated" in lower:
            self.state.set_status("connected")
        elif "connecting" in lower:
            self.state.set_status("connecting")
        elif "disconnected" in lower or "error" in lower:
            self.state.set_status("disconnected")
        elif "reconnecting" in lower:
            self.state.set_status("connecting")

        # Parse buffer status from backlog drain messages
        if "buffered points" in lower:
            # "Draining 12,340 buffered points..."
            import re
            m = re.search(r"([\d,]+)\s+buffered", msg)
            if m:
                self.state.set_buffer(int(m.group(1).replace(",", "")))
        elif "backlog drained" in lower or "backlog: " in lower:
            # Update remaining count from drain progress
            import re
            m = re.search(r"([\d,]+)\s+remaining", msg)
            if m:
                self.state.set_buffer(int(m.group(1).replace(",", "")))
            elif "drained" in lower:
                self.state.set_buffer(0)

    def on_metric(self, name: str, value, timestamp: Optional[float] = None):
        self.state.update_metric(name, value, timestamp)

    def wrap_status_callback(self, original_callback: Optional[Callable] = None) -> Callable:
        def wrapped(msg: str):
            self.on_status(msg)
            if original_callback:
                original_callback(msg)
        return wrapped

    def _sensor_read_loop(self):
        while not self._stop_event.is_set():
            try:
                readings = self.sensor_hub.read_all()
                for r in readings:
                    self.on_metric(r.metric, r.value)
            except Exception:
                pass
            time.sleep(1)

    def _shine_loop(self):
        time.sleep(2.0)
        while not self._stop_event.is_set():
            for tick in range(SHINE_MAX):
                if self._stop_event.is_set():
                    return
                self.state.shine_tick = tick
                time.sleep(0.05)
            self.state.shine_tick = -1
            for _ in range(int(SHINE_INTERVAL * 10)):
                if self._stop_event.is_set():
                    return
                time.sleep(0.1)

    def run(self, connector_fn: Callable):
        self.state = DashboardState()
        self._stop_event.clear()

        connector_thread = threading.Thread(target=connector_fn, daemon=True)
        connector_thread.start()

        if self.sensor_hub:
            threading.Thread(target=self._sensor_read_loop, daemon=True).start()

        self._key_reader = _KeyReader(self.state, self._stop_event)
        self._key_reader.start()

        threading.Thread(target=self._shine_loop, daemon=True).start()

        try:
            with Live(
                build_dashboard(self.state, self.console.width),
                console=self.console,
                refresh_per_second=4,
                screen=True,
            ) as live:
                self._live = live
                while connector_thread.is_alive() and not self._stop_event.is_set():
                    if not self.state.paused:
                        live.update(build_dashboard(self.state, self.console.width))
                    time.sleep(0.25)
        except KeyboardInterrupt:
            pass
        finally:
            self._stop_event.set()
            self._live = None
