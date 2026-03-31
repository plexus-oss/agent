#!/usr/bin/env python3
"""
Plexus Demo — Field Unit (Pi #2)

The handheld Pi. People pick this up, tilt it, and watch the dashboard react.
Has an IMU for motion tracking and a servo motor.

Hardware:
    - Raspberry Pi 4
    - MPU6050 IMU (I2C: SDA→GPIO2, SCL→GPIO3, VCC→3.3V, GND→GND)
    - SG90 Servo (signal→GPIO12, VCC→5V, GND→GND)

Wiring — Servo:
    Servo signal (orange/yellow) → GPIO 12 (pin 32)
    Servo VCC (red)             → 5V (pin 2 or 4)
    Servo GND (brown/black)     → GND (pin 6 or 14)

    Note: GPIO 12 is hardware PWM (PWM0). This avoids jitter from
    software PWM. If GPIO 12 is taken, use GPIO 13 (PWM1) and update
    SERVO_PIN below.

Setup:
    pip install plexus-agent[sensors] RPi.GPIO
    plexus start --key plx_xxxxx
    sudo python demo_field_unit.py   # sudo needed for GPIO PWM

Dashboard alerts to set up:
    imu.accel.magnitude > 1.5   (severity: warning)  — tilted
    imu.accel.magnitude > 3.0   (severity: critical) — shaken hard
"""

import time
import math
import os
import threading
from plexus.connector import run_connector
from plexus.sensors.base import BaseSensor, SensorReading

SOURCE_ID = os.environ.get("PLEXUS_SOURCE_ID", "field-unit")
SERVO_PIN = int(os.environ.get("SERVO_PIN", "12"))

# ─────────────────────────────────────────────────────────────────────────────
# Servo Controller
# ─────────────────────────────────────────────────────────────────────────────

servo_pwm = None
servo_angle = 90  # Start centered

def init_servo():
    global servo_pwm
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(SERVO_PIN, GPIO.OUT)
        servo_pwm = GPIO.PWM(SERVO_PIN, 50)  # 50 Hz for SG90
        servo_pwm.start(angle_to_duty(90))
        print(f"[+] Servo initialized on GPIO{SERVO_PIN}")
        return True
    except Exception as e:
        print(f"[!] Servo not available: {e}")
        print("    (need sudo and RPi.GPIO installed)")
        return False

def angle_to_duty(angle):
    """Convert 0-180° to duty cycle (2.5% - 12.5% for SG90)."""
    return 2.5 + (angle / 180.0) * 10.0

def move_servo(angle):
    global servo_angle
    angle = max(0, min(180, angle))
    servo_angle = angle
    if servo_pwm:
        servo_pwm.ChangeDutyCycle(angle_to_duty(angle))
        # Brief hold then stop to prevent jitter
        time.sleep(0.3)
        servo_pwm.ChangeDutyCycle(0)

def sweep_servo():
    """Non-blocking full sweep for the identify command."""
    def _sweep():
        for a in range(0, 181, 10):
            move_servo(a)
            time.sleep(0.05)
        for a in range(180, -1, -10):
            move_servo(a)
            time.sleep(0.05)
        move_servo(90)
    threading.Thread(target=_sweep, daemon=True).start()

def cleanup_servo():
    if servo_pwm:
        servo_pwm.stop()
    try:
        import RPi.GPIO as GPIO
        GPIO.cleanup()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# IMU wrapper — derived aerospace metrics
# ─────────────────────────────────────────────────────────────────────────────

class MissionIMU(BaseSensor):
    name = "Mission IMU"
    description = "6-axis IMU with derived attitude metrics"
    metrics = [
        "imu.accel.x", "imu.accel.y", "imu.accel.z", "imu.accel.magnitude",
        "imu.gyro.x", "imu.gyro.y", "imu.gyro.z", "imu.gyro.magnitude",
        "imu.attitude.pitch", "imu.attitude.roll",
    ]

    def __init__(self, address=0x68, bus=1, sample_rate=20.0):
        super().__init__(sample_rate=sample_rate)
        self._address = address
        self._bus_num = bus
        self._imu = None

    def setup(self):
        from plexus.sensors import MPU6050
        self._imu = MPU6050(address=self._address, bus=self._bus_num, sample_rate=self.sample_rate)
        self._imu.setup()

    def cleanup(self):
        if self._imu:
            self._imu.cleanup()

    def is_available(self):
        try:
            from smbus2 import SMBus
            with SMBus(self._bus_num) as bus:
                bus.read_byte(self._address)
            return True
        except Exception:
            return False

    def read(self):
        if not self._imu:
            return []

        raw = self._imu.read()
        readings = {}
        for r in raw:
            readings[r.metric] = r.value

        ax = readings.get("accel_x", 0)
        ay = readings.get("accel_y", 0)
        az = readings.get("accel_z", 0)
        gx = readings.get("gyro_x", 0)
        gy = readings.get("gyro_y", 0)
        gz = readings.get("gyro_z", 0)

        accel_mag = math.sqrt(ax * ax + ay * ay + az * az)
        gyro_mag = math.sqrt(gx * gx + gy * gy + gz * gz)
        pitch = math.degrees(math.atan2(ax, math.sqrt(ay * ay + az * az)))
        roll = math.degrees(math.atan2(ay, math.sqrt(ax * ax + az * az)))

        now = time.time()
        return [
            SensorReading("imu.accel.x", round(ax, 4), now),
            SensorReading("imu.accel.y", round(ay, 4), now),
            SensorReading("imu.accel.z", round(az, 4), now),
            SensorReading("imu.accel.magnitude", round(accel_mag, 4), now),
            SensorReading("imu.gyro.x", round(gx, 2), now),
            SensorReading("imu.gyro.y", round(gy, 2), now),
            SensorReading("imu.gyro.z", round(gz, 2), now),
            SensorReading("imu.gyro.magnitude", round(gyro_mag, 2), now),
            SensorReading("imu.attitude.pitch", round(pitch, 2), now),
            SensorReading("imu.attitude.roll", round(roll, 2), now),
        ]


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

imu_sensor = None

if __name__ == "__main__":
    from plexus.sensors import SensorHub

    sensor_hub = SensorHub()

    # IMU
    imu_sensor = MissionIMU(sample_rate=20.0)
    if imu_sensor.is_available():
        sensor_hub.add(imu_sensor)
        print(f"[+] IMU detected at 0x{imu_sensor._address:02x}")
    else:
        print("[!] IMU not detected — running without IMU")
        imu_sensor = None

    # System health
    sensor_hub.add(SystemSensor(sample_rate=1.0))
    print("[+] System health monitoring active")

    # Servo
    servo_ok = init_servo()

    print(f"\n{'=' * 50}")
    print(f"  FIELD UNIT  —  {SOURCE_ID}")
    print(f"  IMU:   {'yes' if imu_sensor else 'no'}")
    print(f"  Servo: {'yes' if servo_ok else 'no'}")
    print(f"{'=' * 50}")
    print("\n  Suggested alerts:")
    print("    imu.accel.magnitude > 1.5  (warning — tilted)")
    print("    imu.accel.magnitude > 3.0  (critical — shaken)")
    print("\n  Hand this Pi to people. Let them tilt it.\n")

    try:
        run_connector(
            source_id=SOURCE_ID,
            sensor_hub=sensor_hub,
            on_status=lambda msg: print(f"  [{time.strftime('%H:%M:%S')}] {msg}"),
        )
    finally:
        cleanup_servo()
