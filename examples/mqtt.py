"""
Bridge an MQTT broker into Plexus.

Prereq:
    pip install plexus-python paho-mqtt
    export PLEXUS_API_KEY=plx_xxx

Run:
    python mqtt.py localhost sensors/#
"""

import json
import sys

import paho.mqtt.client as mqtt

from plexus import Plexus

broker = sys.argv[1] if len(sys.argv) > 1 else "localhost"
topic = sys.argv[2] if len(sys.argv) > 2 else "sensors/#"

px = Plexus(source_id="mqtt-gateway")


def on_message(_client, _userdata, msg):
    name = msg.topic.replace("/", ".")
    payload = msg.payload.decode("utf-8", errors="replace")
    try:
        data = json.loads(payload)
    except ValueError:
        try:
            px.send(name, float(payload))
        except ValueError:
            px.send(name, payload)
        return

    if isinstance(data, dict):
        for key, value in data.items():
            px.send(f"{name}.{key}", value)
    else:
        px.send(name, data)


client = mqtt.Client()
client.on_message = on_message
client.connect(broker)
client.subscribe(topic)
client.loop_forever()
