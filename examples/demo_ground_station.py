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
    plexus start --key plx_xxxxx
    python demo_ground_station.py

Dashboard alerts to set up:
    env.temperature > 30    (severity: warning)  — "room getting warm"
    env.temperature > 35    (severity: critical) — "overheating"
"""

import time
import os
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
    print("\n  Suggested alerts:")
    print("    env.temperature > 30  (warning)")
    print("    env.temperature > 35  (critical)")
    print("\n  Starting...\n")

    run_connector(
        source_id=SOURCE_ID,
        sensor_hub=sensor_hub,
        camera_hub=camera_hub,
        on_status=lambda msg: print(f"  [{time.strftime('%H:%M:%S')}] {msg}"),
    )
