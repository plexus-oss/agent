"""
Pipe MAVLink telemetry into Plexus.

Prereq:
    pip install plexus-python pymavlink
    export PLEXUS_API_KEY=plx_xxx

Run:
    python mavlink.py udpin:0.0.0.0:14550
"""

import sys

from pymavlink import mavutil

from plexus import Plexus

conn_str = sys.argv[1] if len(sys.argv) > 1 else "udpin:0.0.0.0:14550"
px = Plexus(source_id="drone-001")
conn = mavutil.mavlink_connection(conn_str)
conn.wait_heartbeat()

while True:
    msg = conn.recv_match(blocking=True)
    if msg is None:
        continue
    t = msg.get_type()

    if t == "ATTITUDE":
        px.send("attitude.roll", msg.roll)
        px.send("attitude.pitch", msg.pitch)
        px.send("attitude.yaw", msg.yaw)
    elif t == "GLOBAL_POSITION_INT":
        px.send("gps.lat", msg.lat / 1e7)
        px.send("gps.lon", msg.lon / 1e7)
        px.send("gps.alt_m", msg.alt / 1000.0)
    elif t == "SYS_STATUS":
        px.send("battery.voltage", msg.voltage_battery / 1000.0)
        px.send("battery.current", msg.current_battery / 100.0)
