"""
Basic telemetry — the 3-line starter.

Run:
    export PLEXUS_API_KEY=plx_xxx
    python basic.py
"""

import random
import time

from plexus import Plexus

px = Plexus(api_key="plx_123...", source_id="demo-device")

while True:
    px.send("temperature", 20 + random.random() * 5)
    px.send("humidity", 40 + random.random() * 10)
    time.sleep(1)
