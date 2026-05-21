# Plexus Data API — Full Reference

**Base URL:** `https://api.plexus.company`  
**Auth:** `x-api-key: plx_...` header on every request  
**Keys:** [app.plexus.company/api](https://app.plexus.company/api) — scoped to org, prefixed `plx_`

---

## Devices

### List devices

```
GET /v1/sources
```

| Param | Values | Description |
|---|---|---|
| `status` | `online` \| `offline` | Filter by connection status |

```bash
curl "https://api.plexus.company/v1/sources?status=online" \
  -H "x-api-key: plx_..."
```

```json
{
  "devices": [
    { "source_id": "drone-01", "online": true,  "last_seen_ms": 1746969600000 },
    { "source_id": "drone-02", "online": false, "last_seen_ms": 1746883200000 }
  ]
}
```

`last_seen_ms` is Unix milliseconds. `null` if the device has never sent data.

### Get device

```
GET /v1/sources/{source_id}
```

Returns `404` if the device has never sent data.

```json
{ "source_id": "drone-01", "online": true, "last_seen_ms": 1746969600000 }
```

---

## Metrics

### List metric names

```
GET /v1/sources/{source_id}/metrics
```

Returns the distinct metric names a device has ever reported.

```json
["battery.voltage", "cpu.percent", "motor.rpm"]
```

### Latest values

```
GET /v1/sources/{source_id}/metrics/latest
```

Most recent value for every metric on the device. Eventually consistent within a few seconds — not a live feed.

```json
{
  "metrics": {
    "battery.voltage": 12.4,
    "cpu.percent": 14.2,
    "motor.rpm": 3200.0
  }
}
```

### Historical query

```
GET /v1/sources/{source_id}/metrics/query
```

| Param | Example | Description |
|---|---|---|
| `metrics` | `cpu.percent,battery.voltage` | Comma-separated names (default: all) |
| `last` | `1h`, `30m`, `7d` | Relative window. Units: `m`, `h`, `d` only |
| `start` | `2026-05-01T00:00:00Z` | ISO 8601 start (use with `end`) |
| `end` | `2026-05-02T00:00:00Z` | ISO 8601 end |
| `interval` | `raw`, `1m`, `10m`, `1h`, `1d` | Force interval (default: auto) |

Use either `last` or `start`/`end`. If both are sent, `last` wins.

**Raw response** (windows under ~10 min):

```json
{
  "interval": "raw",
  "auto_downsampled": false,
  "series": {
    "battery.voltage": {
      "timestamp_ms": [1746969600000, 1746969660000],
      "value": [12.4, 12.1]
    }
  },
  "truncated": false
}
```

**Downsampled response** (longer windows):

```json
{
  "interval": "1h",
  "auto_downsampled": true,
  "series": {
    "battery.voltage": {
      "timestamp_ms": [1746969600000, 1746973200000],
      "min": [11.8, 11.2],
      "max": [12.6, 12.4],
      "avg": [12.2, 11.9],
      "count": [360, 360]
    }
  },
  "truncated": false
}
```

`truncated: true` means a raw query hit the 10,000-row cap — narrow the window or force an `interval`.

---

## Logs (Events)

```
GET /v1/sources/{source_id}/logs
```

Returns events recorded with `px.event()`. Queryable by time range. See API Reference for full params.

---

## Fleet

### Fleet health

```
GET /v1/fleet/health
```

```json
{ "sources_total": 24, "sources_online": 19 }
```

### Fleet metrics

```
GET /v1/fleet/metrics
```

Query a single metric across all devices. Returns one time-series per device.

| Param | Example | Description |
|---|---|---|
| `metric` | `cpu.percent` | Required. Metric name to query |
| `last` | `1h`, `30m`, `7d` | Relative window |
| `start` / `end` | ISO 8601 | Absolute range |

Always auto-downsampled (minimum `1m` interval — no raw mode for fleet queries).

```json
{
  "metric": "cpu.percent",
  "interval": "1h",
  "sources_online": 19,
  "sources_w_metric": 22,
  "sources": [
    {
      "source_id": "drone-01",
      "online": true,
      "timestamp_ms": [1746969600000, 1746973200000],
      "avg": [14.2, 18.7],
      "min": [8.1, 11.3],
      "max": [31.4, 44.2],
      "count": [60, 60]
    }
  ]
}
```

---

## Live Streams (WebSocket)

All streams are device-scoped. Auth frame must be the **first frame sent** within 10 seconds.

| Stream | Endpoint |
|---|---|
| Metrics | `wss://api.plexus.company/v1/sources/{source_id}/metrics/stream` |
| Logs | `wss://api.plexus.company/v1/sources/{source_id}/logs/stream` |
| Video | `wss://api.plexus.company/v1/sources/{source_id}/video/stream` |

### Handshake (all streams)

```json
{ "type": "auth", "api_key": "plx_..." }
```

### Metrics stream

Query params:

| Param | Description |
|---|---|
| `metrics` | Comma-separated metric names to filter. Omit for all metrics. |

Received frames:

```json
{
  "type": "telemetry",
  "points": [
    { "source_id": "drone-01", "metric": "battery.voltage", "value": 12.4, "timestamp": 1746969660000, "class": "metric", "alert": 0 }
  ]
}
```

```json
{ "type": "gateway_reconnecting", "attempt": 1, "delay_s": 2 }
```

### Logs stream

No query params. Received frames:

```json
{ "type": "event", "source_id": "drone-01", "metric": "state_change", "value": "state_change", "timestamp": 1746969660000, "class": "event" }
```

### Video stream

Query params:

| Param | Description |
|---|---|
| `camera_id` | Camera to subscribe to. Repeat for multiple: `?camera_id=front&camera_id=rear`. Defaults to `default`. |
| `timeout` | Max stream duration in seconds. Server closes with `4008` when it expires. |

Received frames:

```json
{ "type": "init", "online_sources": [{ "source_id": "drone-01", "cameras": ["front", "rear"] }] }
```

```json
{ "type": "video_frame", "source_id": "drone-01", "camera_id": "front", "frame": "<base64 JPEG>", "width": 640, "height": 480, "timestamp": 1746969660000 }
```

```json
{ "type": "gateway_reconnecting", "attempt": 1, "max_attempts": 5, "delay_s": 2 }
```

**Video is live relay only** — no persistence, no replay. Frames are dropped silently if the consumer can't keep up.

### Close codes

| Stream | Code | Meaning |
|---|---|---|
| Metrics | `4401` | Unauthorized |
| Metrics | `4402` | Plan does not include API access |
| Logs | `4401` | Unauthorized |
| Video | `4001` | Unauthorized |
| Video | `4002` | Plan does not include API access |
| Video | `4008` | Stream timeout expired |
| All | `1011` | Internal server error |

### `gateway_reconnecting` behavior

Informational — do not close the socket. The server handles upstream reconnects transparently.

---

## TypeScript reference clients

### Metrics stream

```ts
const ws = new WebSocket(
  "wss://api.plexus.company/v1/sources/drone-01/metrics/stream?metrics=battery.voltage,motor.rpm"
);
ws.onopen = () => ws.send(JSON.stringify({ type: "auth", api_key: "plx_..." }));
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === "telemetry") {
    for (const pt of msg.points) console.log(pt.metric, pt.value, pt.timestamp);
  }
};
ws.onclose = ({ code }) => {
  // reconnect after delay; mint a fresh key if code === 4401
};
```

### Video stream (browser canvas)

```ts
const ws = new WebSocket(
  "wss://api.plexus.company/v1/sources/drone-01/video/stream?camera_id=front"
);
ws.onopen = () => ws.send(JSON.stringify({ type: "auth", api_key: "plx_..." }));

const canvas = document.getElementById("feed") as HTMLCanvasElement;
const ctx = canvas.getContext("2d")!;

ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === "video_frame") {
    const img = new Image();
    img.onload = () => {
      canvas.width = msg.width;
      canvas.height = msg.height;
      ctx.drawImage(img, 0, 0);
    };
    img.src = `data:image/jpeg;base64,${msg.frame}`;
  }
};
```
