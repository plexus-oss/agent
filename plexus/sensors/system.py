"""
System health sensor driver.

Reports CPU, memory, disk, network, and process metrics.
No external dependencies — uses only the Python standard library.
Works on any Linux system; some metrics degrade gracefully on macOS.

Usage:
    from plexus.sensors import SystemSensor, SensorHub
    from plexus import Plexus

    hub = SensorHub()
    hub.add(SystemSensor())
    hub.run(Plexus())

Or from the CLI:
    plexus run --sensor system
"""

import os
import time
import shutil
import logging
import subprocess
from typing import Dict, List, Optional

from .base import BaseSensor, SensorReading

logger = logging.getLogger(__name__)


class SystemSensor(BaseSensor):
    """
    System health sensor for fleet monitoring.

    Provides:
    - cpu.temperature: CPU temperature in Celsius (Linux)
    - cpu.usage_pct: CPU usage as a percentage (0-100)
    - cpu.load: 1-minute load average
    - memory.used_pct: Memory usage as a percentage (0-100)
    - memory.available_mb: Available memory in MB
    - disk.used_pct: Root disk usage as a percentage (0-100)
    - disk.available_gb: Available disk space in GB
    - net.rx_bytes: Network bytes received (cumulative)
    - net.tx_bytes: Network bytes transmitted (cumulative)
    - system.uptime: System uptime in seconds
    - system.processes: Number of running processes
    """

    name = "System"
    description = "System health (CPU, memory, disk, network, uptime)"
    metrics = [
        "cpu.temperature",
        "cpu.usage_pct",
        "cpu.load",
        "memory.used_pct",
        "memory.available_mb",
        "disk.used_pct",
        "disk.available_gb",
        "net.rx_bytes",
        "net.tx_bytes",
        "system.uptime",
        "system.processes",
    ]

    def __init__(
        self,
        sample_rate: float = 1.0,
        prefix: str = "",
        tags: Optional[dict] = None,
    ):
        super().__init__(sample_rate=sample_rate, prefix=prefix, tags=tags)
        # Previous CPU times for usage calculation
        self._prev_cpu_times: Optional[Dict[str, int]] = None
        self._prev_cpu_time: float = 0.0

    def _read_cpu_temperature(self) -> Optional[float]:
        """Read CPU temperature from thermal zone. Returns Celsius or None."""
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                return int(f.read().strip()) / 1000.0
        except (FileNotFoundError, ValueError, PermissionError):
            pass

        # Fallback: vcgencmd (common on ARM SBCs)
        try:
            result = subprocess.run(
                ["vcgencmd", "measure_temp"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                temp_str = result.stdout.strip().replace("temp=", "").replace("'C", "")
                return float(temp_str)
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass

        return None

    def _read_cpu_usage_pct(self) -> Optional[float]:
        """Read CPU usage percentage from /proc/stat delta."""
        try:
            with open("/proc/stat", "r") as f:
                line = f.readline()  # first line is aggregate
            parts = line.split()
            # user, nice, system, idle, iowait, irq, softirq, steal
            times = {
                "user": int(parts[1]),
                "nice": int(parts[2]),
                "system": int(parts[3]),
                "idle": int(parts[4]),
                "iowait": int(parts[5]) if len(parts) > 5 else 0,
            }
            now = time.monotonic()

            if self._prev_cpu_times is not None:
                prev = self._prev_cpu_times
                dt = now - self._prev_cpu_time

                if dt > 0:
                    d_idle = (times["idle"] + times["iowait"]) - (prev["idle"] + prev["iowait"])
                    d_total = sum(times.values()) - sum(prev.values())

                    if d_total > 0:
                        usage = (1.0 - d_idle / d_total) * 100.0
                        self._prev_cpu_times = times
                        self._prev_cpu_time = now
                        return round(max(0.0, min(100.0, usage)), 1)

            self._prev_cpu_times = times
            self._prev_cpu_time = now
            return None  # Need two samples to calculate delta
        except (FileNotFoundError, ValueError, IndexError):
            return None

    def _read_cpu_load(self) -> Optional[float]:
        """Read 1-minute load average."""
        try:
            return round(os.getloadavg()[0], 2)
        except OSError:
            return None

    def _read_memory(self) -> Optional[tuple]:
        """Read memory stats. Returns (used_pct, available_mb) or None."""
        try:
            with open("/proc/meminfo", "r") as f:
                meminfo = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip().split()[0]
                        meminfo[key] = int(val)

                total = meminfo.get("MemTotal", 0)
                available = meminfo.get("MemAvailable", 0)
                if total > 0:
                    used_pct = round((1.0 - available / total) * 100.0, 1)
                    available_mb = round(available / 1024.0, 1)
                    return used_pct, available_mb
        except (FileNotFoundError, ValueError, KeyError):
            pass
        return None

    def _read_disk(self) -> tuple:
        """Read disk stats. Returns (used_pct, available_gb)."""
        usage = shutil.disk_usage("/")
        used_pct = round(usage.used / usage.total * 100.0, 1)
        available_gb = round(usage.free / (1024 ** 3), 2)
        return used_pct, available_gb

    def _read_network_bytes(self) -> Optional[tuple]:
        """Read total network rx/tx bytes from /proc/net/dev. Returns (rx, tx) or None."""
        try:
            rx_total = 0
            tx_total = 0
            with open("/proc/net/dev", "r") as f:
                for line in f:
                    line = line.strip()
                    if ":" not in line:
                        continue
                    iface, data = line.split(":", 1)
                    iface = iface.strip()
                    # Skip loopback
                    if iface == "lo":
                        continue
                    fields = data.split()
                    if len(fields) >= 9:
                        rx_total += int(fields[0])
                        tx_total += int(fields[8])
            return rx_total, tx_total
        except (FileNotFoundError, ValueError):
            return None

    def _read_uptime(self) -> Optional[float]:
        """Read system uptime in seconds from /proc/uptime."""
        try:
            with open("/proc/uptime", "r") as f:
                return round(float(f.read().split()[0]), 1)
        except (FileNotFoundError, ValueError):
            return None

    def _read_process_count(self) -> Optional[int]:
        """Count running processes via /proc."""
        try:
            count = 0
            for entry in os.listdir("/proc"):
                if entry.isdigit():
                    count += 1
            return count
        except OSError:
            return None

    def read(self) -> List[SensorReading]:
        readings = []

        # CPU
        cpu_temp = self._read_cpu_temperature()
        if cpu_temp is not None:
            readings.append(SensorReading("cpu.temperature", round(cpu_temp, 1)))

        cpu_usage = self._read_cpu_usage_pct()
        if cpu_usage is not None:
            readings.append(SensorReading("cpu.usage_pct", cpu_usage))

        cpu_load = self._read_cpu_load()
        if cpu_load is not None:
            readings.append(SensorReading("cpu.load", cpu_load))

        # Memory
        mem = self._read_memory()
        if mem is not None:
            readings.append(SensorReading("memory.used_pct", mem[0]))
            readings.append(SensorReading("memory.available_mb", mem[1]))

        # Disk
        disk_used_pct, disk_available_gb = self._read_disk()
        readings.append(SensorReading("disk.used_pct", disk_used_pct))
        readings.append(SensorReading("disk.available_gb", disk_available_gb))

        # Network
        net = self._read_network_bytes()
        if net is not None:
            readings.append(SensorReading("net.rx_bytes", net[0]))
            readings.append(SensorReading("net.tx_bytes", net[1]))

        # System
        uptime = self._read_uptime()
        if uptime is not None:
            readings.append(SensorReading("system.uptime", uptime))

        procs = self._read_process_count()
        if procs is not None:
            readings.append(SensorReading("system.processes", procs))

        return readings

    def is_available(self) -> bool:
        """Always available — disk and load work on any POSIX system."""
        return True
