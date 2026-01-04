#!/usr/bin/env python3
"""
Test script to simulate IMU sensor streaming.
Run this locally to test the streaming fix without needing the Pi.

Usage:
    cd plexus-oss/agent
    pip install -e .
    python test_stream.py
"""

import time
import math
import random
from plexus import Plexus

def main():
    # Use localhost for local development
    px = Plexus(endpoint="http://localhost:3000")
    print(f"Streaming to {px.endpoint} as device {px.device_id}")
    print("Press Ctrl+C to stop\n")

    t = 0
    while True:
        # Simulate IMU data (sinusoidal with noise)
        accel_x = math.sin(t * 0.5) + random.uniform(-0.1, 0.1)
        accel_y = math.cos(t * 0.5) + random.uniform(-0.1, 0.1)
        accel_z = 9.8 + random.uniform(-0.2, 0.2)
        gyro_x = math.sin(t * 2) * 10 + random.uniform(-1, 1)
        gyro_y = math.cos(t * 2) * 10 + random.uniform(-1, 1)
        gyro_z = random.uniform(-5, 5)

        # Send ALL metrics in one batch (the fix!)
        px.send_batch([
            ("accel_x", round(accel_x, 4)),
            ("accel_y", round(accel_y, 4)),
            ("accel_z", round(accel_z, 4)),
            ("gyro_x", round(gyro_x, 2)),
            ("gyro_y", round(gyro_y, 2)),
            ("gyro_z", round(gyro_z, 2)),
        ])

        print(f"Sent: accel=({accel_x:.2f}, {accel_y:.2f}, {accel_z:.2f}) "
              f"gyro=({gyro_x:.1f}, {gyro_y:.1f}, {gyro_z:.1f})")

        t += 0.1
        time.sleep(0.1)  # 10 Hz

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped")
