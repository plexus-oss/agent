#!/usr/bin/env python3
"""
Plexus Demo — Ground Station (Pi #1)

The stationary Pi. Sits on the desk with camera pointed at visitors and
a BME280 monitoring the room environment.

Hardware:
    - Raspberry Pi 4
    - USB camera or Pi Camera (pointed at visitors)
    - BME280 (I2C: SDA→GPIO2, SCL→GPIO3, VCC→3.3V, GND→GND)

Setup:
    pip install plexus-agent[sensors,camera]
    plexus pair --key plx_xxxxx
    python demo_ground_station.py

Dashboard alerts to set up:
    env.temperature > 30    (severity: warning)  — "room getting warm"
    env.temperature > 35    (severity: critical) — "overheating"
"""

import time
import os
import subprocess
from plexus import Plexus, param
from plexus.connector import run_connector
from plexus.sensors.base import BaseSensor, SensorReading

SOURCE_ID = os.environ.get("PLEXUS_SOURCE_ID", "ground-station")

# ─────────────────────────────────────────────────────────────────────────────
# System telemetry
# ─────────────────────────────────────────────────────────────────────────────

class SystemSensor(BaseSensor):
    name = "System Health"
    description = "Onboard computer health metrics"
    metrics = ["sys.cpu_temp", "sys.mem_used_pct", "sys.uptime"]

    def __init__(self, sample_rate=1.0):
        super().__init__(sample_rate=sample_rate)

    def setup(self): pass
    def cleanup(self): pass
    def is_available(self): return True

    def read(self):
        now = time.time()
        readings = []
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                readings.append(SensorReading("sys.cpu_temp", round(int(f.read().strip()) / 1000.0, 1), now))
        except Exception:
            pass
        try:
            import psutil
            readings.append(SensorReading("sys.mem_used_pct", round(psutil.virtual_memory().percent, 1), now))
        except ImportError:
            pass
        try:
            with open("/proc/uptime") as f:
                readings.append(SensorReading("sys.uptime", round(float(f.read().split()[0]), 0), now))
        except Exception:
            pass
        return readings


# ─────────────────────────────────────────────────────────────────────────────
# Plexus client + typed commands
# ─────────────────────────────────────────────────────────────────────────────

px = Plexus()

state = {
    "mode": "nominal",
    "sample_rate_hz": 5,
}


@px.command("set_mode", "Switch operational mode")
@param("mode", type="enum", choices=["nominal", "safe", "diagnostic", "high_rate"])
def set_mode(mode):
    rate_map = {"nominal": 5, "safe": 1, "diagnostic": 20, "high_rate": 50}
    state["mode"] = mode
    state["sample_rate_hz"] = rate_map[mode]
    return {"mode": mode, "sample_rate_hz": rate_map[mode]}


@px.command("capture_snapshot", "Capture a camera frame on demand")
def capture_snapshot():
    if camera_hub:
        frames = camera_hub.capture_all()
        if frames:
            return {"captured": True, "cameras": len(frames)}
    return {"captured": False, "reason": "no camera available"}


@px.command("identify", "Blink onboard LED — find this device in a rack")
def identify():
    try:
        led_path = "/sys/class/leds/ACT/brightness"
        if os.path.exists(led_path):
            for _ in range(5):
                with open(led_path, "w") as f:
                    f.write("1")
                time.sleep(0.2)
                with open(led_path, "w") as f:
                    f.write("0")
                time.sleep(0.2)
            return {"identified": True, "method": "ACT LED"}
    except PermissionError:
        pass
    return {"identified": False, "reason": "no LED access (try sudo)"}


@px.command("run_diagnostic", "Execute onboard diagnostic check")
@param("subsystem", type="enum", choices=["env", "camera", "comms", "all"], default="all")
def run_diagnostic(subsystem):
    results = {}
    if subsystem in ("env", "all"):
        results["env_sensor"] = {"status": "ok" if bme_available else "not connected"}
    if subsystem in ("camera", "all"):
        results["camera"] = {"status": "ok" if camera_hub else "unavailable"}
    if subsystem in ("comms", "all"):
        try:
            result = subprocess.run(["ping", "-c", "1", "-W", "2", "8.8.8.8"], capture_output=True, timeout=5)
            latency = None
            for line in result.stdout.decode().split("\n"):
                if "time=" in line:
                    try:
                        latency = float(line.split("time=")[1].split(" ")[0])
                    except Exception:
                        pass
            results["comms"] = {"status": "ok" if result.returncode == 0 else "degraded", "latency_ms": latency}
        except Exception:
            results["comms"] = {"status": "unknown"}
    return {"subsystem": subsystem, "results": results}


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

camera_hub = None
bme_available = False

if __name__ == "__main__":
    from plexus.sensors import SensorHub

    sensor_hub = SensorHub()

    # BME280 environmental sensor
    try:
        from plexus.sensors import BME280
        bme = BME280(sample_rate=2.0, prefix="env.")
        if bme.is_available():
            sensor_hub.add(bme)
            bme_available = True
            print("[+] BME280 detected (temperature, humidity, pressure)")
        else:
            print("[!] BME280 not detected — running without environment sensor")
    except ImportError:
        print("[!] Sensor support not installed (pip install plexus-agent[sensors])")

    # System health
    sensor_hub.add(SystemSensor(sample_rate=1.0))
    print("[+] System health monitoring active")

    # Camera
    try:
        from plexus.cameras import CameraHub, USBCamera
        camera_hub = CameraHub()
        cam = USBCamera(device_index=0, frame_rate=10, resolution=(640, 480), quality=75, camera_id="main")
        if cam.is_available():
            camera_hub.add(cam)
            print("[+] USB camera detected")
        else:
            camera_hub = None
    except ImportError:
        camera_hub = None

    if camera_hub is None:
        try:
            from plexus.cameras import CameraHub, PiCamera
            camera_hub = CameraHub()
            cam = PiCamera(camera_num=0, frame_rate=10, resolution=(640, 480), quality=75, camera_id="main")
            camera_hub.add(cam)
            print("[+] Pi Camera detected")
        except Exception:
            camera_hub = None
            print("[!] No camera found")

    print(f"\n{'=' * 50}")
    print(f"  GROUND STATION  —  {SOURCE_ID}")
    print(f"  Camera: {'yes' if camera_hub else 'no'}")
    print(f"  BME280: {'yes' if bme_available else 'no'}")
    print(f"{'=' * 50}")
    print("\n  Commands: set_mode, capture_snapshot, identify,")
    print("           run_diagnostic")
    print("\n  Suggested alerts:")
    print("    env.temperature > 30  (warning)")
    print("    env.temperature > 35  (critical)")
    print("\n  Starting...\n")

    run_connector(
        source_id=SOURCE_ID,
        sensor_hub=sensor_hub,
        camera_hub=camera_hub,
        command_registry=px.commands,
        on_status=lambda msg: print(f"  [{time.strftime('%H:%M:%S')}] {msg}"),
    )
