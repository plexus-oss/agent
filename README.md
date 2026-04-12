# plexus-python

**Thin Python SDK for [Plexus](https://plexus.company).** Send telemetry to the Plexus gateway in one line. Storage, dashboards, alerts, and fleet management live in the platform — this package just ships your data.

[![PyPI](https://img.shields.io/pypi/v/plexus-python)](https://pypi.org/project/plexus-python/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

## Quick Start

```bash
pip install plexus-python
```

```python
from plexus import Plexus

px = Plexus(api_key="plx_xxx", source_id="device-001")
px.send("temperature", 72.5)
```

Get an API key at [app.plexus.company](https://app.plexus.company) → Devices → Add Device.

## Usage

```python
from plexus import Plexus

px = Plexus(source_id="rig-01")   # reads PLEXUS_API_KEY from env

# Numbers
px.send("engine.rpm", 3450)
px.send("coolant.temperature", 82.3, tags={"unit": "C"})

# Strings, bools, objects, arrays — all JSON-serializable
px.send("vehicle.state", "RUNNING")
px.send("motor.enabled", True)
px.send("position", {"x": 1.5, "y": 2.3, "z": 0.8})

# Batch
px.send_batch([
    ("temperature", 72.5),
    ("pressure", 1013.25),
])

# Named run for grouping related data
with px.run("thermal-cycle-001"):
    while running:
        px.send("temperature", read_temp())
```

## Bring Your Own Protocol

This package ships no adapters, auto-detection, or daemons — just the client. Use whatever library you'd use anyway and pipe values into `px.send()`.

```python
# MAVLink (pymavlink)
for msg in conn:
    if msg.get_type() == "ATTITUDE":
        px.send("attitude.roll", msg.roll)

# CAN (python-can)
for msg in bus:
    px.send(f"can.0x{msg.arbitration_id:x}", int.from_bytes(msg.data, "big"))

# MQTT (paho-mqtt)
def on_message(_c, _u, msg):
    px.send(msg.topic.replace("/", "."), float(msg.payload))

# I2C sensor (Adafruit CircuitPython)
px.send("temperature", bme.temperature)
```

See [`examples/`](examples/) for runnable versions of each.

## Reliability

Every send buffers locally before hitting the network, retries with exponential backoff, and keeps your data safe across outages. Enable SQLite persistence to survive restarts and power loss:

```python
px = Plexus(persistent_buffer=True)
```

Point counts and flush:

```python
px.buffer_size()
px.flush_buffer()
```

## Transport

By default the SDK connects over a **WebSocket** to `/ws/device` on the gateway — same wire protocol as the C SDK. This gives you:

- lower-latency streaming of telemetry,
- live command delivery from the UI / API to the device.

If the socket is unavailable, sends transparently fall back to `POST /ingest` so no data is lost.

```python
# default — ws with http fallback
px = Plexus()

# force http (legacy)
px = Plexus(transport="http")
```

### Handling commands

Register a handler before the first `send()` so the command is advertised in the auth frame:

```python
def reboot(name, params):
    delay = params.get("delay_s", 0)
    # ... reboot logic ...
    return {"ok": True, "delay": delay}

px = Plexus()
px.on_command("reboot", reboot, description="reboot the device")
px.send("temperature", 72.5)   # opens the socket, waits for auth
```

The SDK sends an `ack` frame before invoking the handler, then a `result` frame with whatever the handler returns (or an `error` frame if it raises).

## Environment Variables

| Variable                | Description                  | Default                          |
| ----------------------- | ---------------------------- | -------------------------------- |
| `PLEXUS_API_KEY`        | API key (required)           | none                             |
| `PLEXUS_GATEWAY_URL`    | HTTP ingest URL              | `https://plexus-gateway.fly.dev` |
| `PLEXUS_GATEWAY_WS_URL` | WebSocket URL              | `wss://plexus-gateway.fly.dev`   |

## Architecture

```
Your code ── px.send() ── HTTP POST /ingest ──> plexus-gateway ──> ClickHouse + Dashboard
```

One thin path. No agent, no daemon, no adapters. If you want the full HardwareOps platform — dashboards, alerts, RCA, fleet views — that's the web UI at app.plexus.company. This package gets your data there.

## License

Apache 2.0
