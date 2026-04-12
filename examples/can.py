"""
Pipe CAN bus frames into Plexus.

Prereq:
    pip install plexus-python python-can cantools
    sudo ip link set can0 type can bitrate 500000 && sudo ip link set can0 up
    export PLEXUS_API_KEY=plx_xxx

Run:
    python can.py can0 [vehicle.dbc]
"""

import sys

import can
import cantools

from plexus import Plexus

channel = sys.argv[1] if len(sys.argv) > 1 else "can0"
dbc_path = sys.argv[2] if len(sys.argv) > 2 else None

px = Plexus(source_id="vehicle-001")
bus = can.interface.Bus(channel=channel, bustype="socketcan")
db = cantools.database.load_file(dbc_path) if dbc_path else None

for msg in bus:
    if db:
        try:
            decoded = db.decode_message(msg.arbitration_id, msg.data)
            for name, value in decoded.items():
                if isinstance(value, (int, float)):
                    px.send(name, value)
            continue
        except KeyError:
            pass
    px.send(
        f"can.raw.0x{msg.arbitration_id:x}",
        int.from_bytes(msg.data, "big"),
    )
