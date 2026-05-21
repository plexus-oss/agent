---
name: Plexus
description: This skill should be used when the user asks to "set up Plexus on my device", "install Plexus on my Raspberry Pi", "send telemetry to Plexus", "integrate Plexus into my project", "build a monitoring app with Plexus", "query my Plexus devices", "stream live data from Plexus", or "connect to the Plexus API". Covers both the device-side SDK and the consumer-side REST/WebSocket API.
version: 0.1.0
---

# Plexus

Plexus is a hardware observability platform. Devices push telemetry (metrics, events, video) via the Python SDK or raw HTTP. That data is then queryable and streamable through the Plexus Data API and dashboard.

There are two distinct workflows — choose based on what the user is building:

- **Device side** — install the SDK, authenticate, and start sending data from hardware
- **App side** — query historical data, stream live updates, and monitor a fleet via the REST/WebSocket API

---

## Device Side: SDK Setup

### Install

One-line setup for Linux devices (Raspberry Pi, Jetson, etc.) — installs the SDK, configures auth, and handles hardware support automatically:

```bash
curl -sL https://app.plexus.company/setup | bash -s -- --key plx_xxx --name drone-01
```

`--name` is required and must be unique across the fleet (`^[a-z0-9][a-z0-9_-]{1,62}$`). The gateway auto-suffixes duplicates (`drone-01_2`, etc.).

For development machines or manual installs:

```bash
pip install plexus-python
plexus init   # opens browser to authenticate and saves key to ~/.plexus/config.json
```

### Send telemetry

```python
from plexus import Plexus

px = Plexus(source_id="drone-01")  # reads PLEXUS_API_KEY from env or ~/.plexus/config.json

px.send("battery.voltage", 12.4)
px.send("motor.rpm", 3200, tags={"motor_id": "A1"})
px.send_batch([
    ("battery.voltage", 12.4),
    ("battery.current_ma", 340),
    ("motor.rpm", 3200),
])
```

Metric names use `<subsystem>.<metric>` convention (`battery.voltage`, `motor.rpm`, `gps.latitude`).

### Send events

Use `event()` for discrete occurrences — faults, state changes, operator actions. These appear as markers on the timeline, not time-series lines:

```python
px.event("fault", "E-stop triggered")
px.event("state_change", {"from": "IDLE", "to": "RUNNING"})
```

### Group data into a run

```python
with px.run("thermal-cycle-001"):
    while running:
        px.send("temperature", read_temp())
```

### Reliability

Enable SQLite persistence to survive restarts and power loss:

```python
px = Plexus(persistent_buffer=True)
```

The SDK buffers locally, retries with exponential backoff, and syncs the device clock against the gateway on every WebSocket connection — important on embedded boards that boot without NTP.

### Video streaming

```python
# Frame by frame (OpenCV, picamera2)
import cv2
cap = cv2.VideoCapture(0)
while True:
    ok, frame = cap.read()
    if ok:
        px.send_video_frame(frame, camera_id="front")

# From a URL (RTSP, HLS — requires FFmpeg on $PATH)
stop = px.stream_camera("rtsp://192.168.1.10/stream", camera_id="front")
stop.set()  # stop when done
```

---

## App Side: API Integration

**Base URL:** `https://api.plexus.company`  
**Auth:** `x-api-key: plx_...` header on every request  
**Keys:** [app.plexus.company/api](https://app.plexus.company/api)

### Discover devices

```bash
GET /v1/sources                          # all devices
GET /v1/sources?status=online            # online only
GET /v1/sources/{source_id}              # single device
```

### Discover metrics

```bash
GET /v1/sources/{source_id}/metrics      # list metric names the device has reported
```

### Fetch latest values

```bash
GET /v1/sources/{source_id}/metrics/latest
```

### Query historical data

```bash
GET /v1/sources/{source_id}/metrics/query?metrics=battery.voltage&last=1h
GET /v1/sources/{source_id}/metrics/query?metrics=cpu.percent&start=2026-05-01T00:00:00Z&end=2026-05-02T00:00:00Z
```

Windows under ~10 min return raw `value` arrays. Longer windows auto-downsample to `min/max/avg/count` — Plexus picks an interval (`1m`, `10m`, `1h`, `1d`) targeting ~1000 points. Force with `?interval=raw` or `?interval=10m`.

### Stream live data (WebSocket)

```ts
const ws = new WebSocket(
  "wss://api.plexus.company/v1/sources/drone-01/metrics/stream?metrics=battery.voltage,motor.rpm"
);
ws.onopen = () => ws.send(JSON.stringify({ type: "auth", api_key: "plx_..." }));
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === "telemetry") console.log(msg.points);
};
```

Auth frame must be the **first frame** — socket closes with `4401` after 10 s if not received.

### Stream live video (WebSocket)

```ts
const ws = new WebSocket(
  "wss://api.plexus.company/v1/sources/drone-01/video/stream?camera_id=front"
);
ws.onopen = () => ws.send(JSON.stringify({ type: "auth", api_key: "plx_..." }));
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === "video_frame") {
    img.src = `data:image/jpeg;base64,${msg.frame}`;
  }
};
```

### Fleet-wide queries

```bash
GET /v1/fleet/health                               # total and online device counts
GET /v1/fleet/metrics?metric=cpu.percent&last=1h   # one metric across all devices
```

---

## Key Conventions

- `source_id` is the stable device identity — everything (metrics, events, commands) is scoped to it
- API keys are prefixed `plx_` and scoped to an org
- Config persists to `~/.plexus/config.json`; SDK reads `PLEXUS_API_KEY` env var automatically
- Metric names: `<subsystem>.<metric>` — keep subsystems consistent across a fleet
- Tags: low-cardinality key-value pairs for filtering (`firmware`, `location`, `motor_id`) — not UUIDs or timestamps

---

## Additional Resources

For complete method signatures, options, and advanced patterns:

- **`references/sdk.md`** — Full SDK reference: all methods, transport options, clock correction, commands, environment variables
- **`references/api.md`** — Full API reference: all REST endpoints, WebSocket frame shapes, query params, close codes
