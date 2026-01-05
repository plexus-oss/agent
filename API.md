# Plexus API

Send telemetry data to Plexus using HTTP or WebSocket.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              PLEXUS CLOUD                                 │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   Frontend (Next.js)                     PartyKit Server                 │
│   app.plexus.company                     plexus-realtime.partykit.dev    │
│   ├── /api/ingest           POST         ├── Device connections          │
│   ├── /api/sessions         POST/PATCH   ├── Browser connections         │
│   ├── /api/devices/pair     POST/GET     └── Real-time relay             │
│   ├── /api/auth/verify-key  GET                                          │
│   └── /api/auth/verify-device POST       (Device token verification)     │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘

         ▲                                        ▲
         │ HTTP                                   │ WebSocket
         │ (API Key)                              │ (Device Token)
    ┌────┴────┐                              ┌────┴────┐
    │ Scripts │                              │  Agent  │
    │ Devices │  Direct HTTP POST            │ plexus  │
    │   IoT   │                              │   run   │
    └─────────┘                              └─────────┘
```

**Two ways to send data:**

| Method    | Use Case                                        |
| --------- | ----------------------------------------------- |
| HTTP POST | Simple scripts, batch uploads, embedded devices |
| WebSocket | Real-time streaming, UI-controlled devices      |

## Quick Start

### Option 1: Web-Controlled Device (Recommended)

Pair your device from the dashboard and control everything via UI:

```bash
# On your device
curl -sL https://app.plexus.company/setup | bash -s -- --code ABC123
```

Then control streaming, recording, and configuration from [app.plexus.company/fleet](https://app.plexus.company/fleet).

### Option 2: Direct HTTP

Send data directly via HTTP:

```bash
curl -X POST https://app.plexus.company/api/ingest \
  -H "x-api-key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "points": [{
      "metric": "temperature",
      "value": 72.5,
      "timestamp": 1699900000,
      "device_id": "sensor-001"
    }]
  }'
```

## Authentication

Plexus uses two types of credentials:

| Type          | Prefix   | Use Case                                    |
| ------------- | -------- | ------------------------------------------- |
| Device Token  | `plxd_`  | WebSocket connections (agent `plexus run`)  |
| API Key       | `plx_`   | Direct HTTP access (scripts, embedded IoT)  |

### Getting a Device Token

Device tokens are created automatically during pairing:

1. Go to [app.plexus.company/fleet](https://app.plexus.company/fleet)
2. Click "Pair Device"
3. Run the setup command on your device (or `plexus pair --code ABC123`)
4. Device token is saved to `~/.plexus/config.json`

### Getting an API Key (for HTTP)

For direct HTTP access without the agent:

1. Sign up at [app.plexus.company](https://app.plexus.company)
2. Go to Settings → Connections
3. Create an API key (starts with `plx_`)

## HTTP API

### Authentication

All requests require an API key in the header:

```
x-api-key: plx_xxxxx
```

### Send Data

**POST** `/api/ingest`

```json
{
  "points": [
    {
      "metric": "temperature",
      "value": 72.5,
      "timestamp": 1699900000.123,
      "device_id": "sensor-001",
      "tags": { "location": "lab" },
      "session_id": "test-001"
    }
  ]
}
```

| Field        | Type   | Required | Description                                    |
| ------------ | ------ | -------- | ---------------------------------------------- |
| `metric`     | string | Yes      | Metric name (e.g., `temperature`, `motor.rpm`) |
| `value`      | any    | Yes      | See supported value types below                |
| `timestamp`  | float  | No       | Unix timestamp (seconds). Defaults to now      |
| `device_id`  | string | Yes      | Your device identifier                         |
| `tags`       | object | No       | Key-value labels                               |
| `session_id` | string | No       | Group data into sessions                       |

### Supported Value Types

| Type    | Example                          | Use Case                         |
| ------- | -------------------------------- | -------------------------------- |
| number  | `72.5`, `-40`, `3.14159`         | Numeric readings (most common)   |
| string  | `"error"`, `"idle"`, `"running"` | Status, state, labels            |
| boolean | `true`, `false`                  | On/off, enabled/disabled         |
| object  | `{"x": 1.2, "y": 3.4, "z": 5.6}` | Vector data, structured readings |
| array   | `[1.0, 2.0, 3.0, 4.0]`           | Waveforms, multiple values       |

### Sessions

Group related data for analysis and playback.

**Create session:**

```json
POST /api/sessions
{
  "session_id": "test-001",
  "name": "Motor Test Run",
  "device_id": "sensor-001",
  "status": "active"
}
```

**End session:**

```json
PATCH /api/sessions/{session_id}
{
  "status": "completed",
  "ended_at": "2024-01-15T10:30:00Z"
}
```

## WebSocket API

For real-time UI-controlled streaming, devices connect via WebSocket.

### Connection Flow

1. Device connects to PartyKit server
2. Device authenticates with device token (or legacy API key)
3. Device reports available sensors
4. Dashboard controls streaming via messages

### Device Authentication

Devices authenticate using a device token (from pairing) or API key (legacy):

```json
// Device → Server (using device token - recommended)
{
  "type": "device_auth",
  "device_token": "plxd_xxxxx",
  "device_id": "my-device-001",
  "platform": "Linux",
  "sensors": [
    {
      "name": "MPU6050",
      "description": "6-axis IMU",
      "metrics": ["accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z"],
      "sample_rate": 100,
      "prefix": "",
      "available": true
    }
  ]
}

// Device → Server (using API key - legacy, still supported)
{
  "type": "device_auth",
  "api_key": "plx_xxxxx",
  "device_id": "my-device-001",
  "platform": "Linux",
  "sensors": []
}

// Server → Device
{
  "type": "authenticated",
  "device_id": "my-device-001"
}
```

### Message Types (Dashboard → Device)

| Type            | Description                       |
| --------------- | --------------------------------- |
| `start_stream`  | Start streaming sensor data       |
| `stop_stream`   | Stop streaming                    |
| `start_session` | Start recording to a session      |
| `stop_session`  | Stop recording                    |
| `configure`     | Configure sensor (e.g., sample rate) |
| `execute`       | Run a shell command               |
| `cancel`        | Cancel running command            |
| `ping`          | Keepalive request                 |

### Message Types (Device → Dashboard)

| Type              | Description              |
| ----------------- | ------------------------ |
| `telemetry`       | Sensor data points       |
| `session_started` | Confirm session started  |
| `session_stopped` | Confirm session stopped  |
| `output`          | Command output           |
| `pong`            | Keepalive response       |

### Start Streaming

```json
// Dashboard → Device
{
  "type": "start_stream",
  "device_id": "my-device-001",
  "metrics": ["accel_x", "accel_y", "accel_z"],
  "interval_ms": 100
}

// Device → Dashboard (continuous)
{
  "type": "telemetry",
  "points": [
    { "metric": "accel_x", "value": 0.12, "timestamp": 1699900000123 },
    { "metric": "accel_y", "value": 0.05, "timestamp": 1699900000123 },
    { "metric": "accel_z", "value": 9.81, "timestamp": 1699900000123 }
  ]
}
```

### Start Session (Recording)

```json
// Dashboard → Device
{
  "type": "start_session",
  "device_id": "my-device-001",
  "session_id": "session_1699900000_abc123",
  "session_name": "Motor Test",
  "metrics": [],
  "interval_ms": 100
}

// Device → Dashboard
{
  "type": "session_started",
  "session_id": "session_1699900000_abc123",
  "session_name": "Motor Test"
}

// Device streams telemetry with session_id tag
{
  "type": "telemetry",
  "session_id": "session_1699900000_abc123",
  "points": [
    {
      "metric": "accel_x",
      "value": 0.12,
      "timestamp": 1699900000123,
      "tags": { "session_id": "session_1699900000_abc123" }
    }
  ]
}
```

### Configure Sensor

```json
// Dashboard → Device
{
  "type": "configure",
  "device_id": "my-device-001",
  "sensor": "MPU6050",
  "config": {
    "sample_rate": 50
  }
}
```

### Execute Command

```json
// Dashboard → Device
{
  "type": "execute",
  "id": "cmd-123",
  "command": "uname -a"
}

// Device → Dashboard (streamed)
{"type": "output", "id": "cmd-123", "event": "start", "command": "uname -a"}
{"type": "output", "id": "cmd-123", "event": "data", "data": "Linux raspberrypi..."}
{"type": "output", "id": "cmd-123", "event": "exit", "code": 0}
```

## Code Examples

### Python (Direct HTTP)

```python
import requests
import time

requests.post(
    "https://app.plexus.company/api/ingest",
    headers={"x-api-key": "plx_xxxxx"},
    json={
        "points": [{
            "metric": "temperature",
            "value": 72.5,
            "timestamp": time.time(),
            "device_id": "sensor-001"
        }]
    }
)
```

### JavaScript

```javascript
await fetch("https://app.plexus.company/api/ingest", {
  method: "POST",
  headers: {
    "x-api-key": "plx_xxxxx",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    points: [
      {
        metric: "temperature",
        value: 72.5,
        timestamp: Date.now() / 1000,
        device_id: "sensor-001",
      },
    ],
  }),
});
```

### Go

```go
package main

import (
    "bytes"
    "encoding/json"
    "net/http"
    "time"
)

func main() {
    points := map[string]interface{}{
        "points": []map[string]interface{}{{
            "metric":    "temperature",
            "value":     72.5,
            "timestamp": float64(time.Now().Unix()),
            "device_id": "sensor-001",
        }},
    }

    body, _ := json.Marshal(points)
    req, _ := http.NewRequest("POST", "https://app.plexus.company/api/ingest", bytes.NewBuffer(body))
    req.Header.Set("x-api-key", "plx_xxxxx")
    req.Header.Set("Content-Type", "application/json")

    http.DefaultClient.Do(req)
}
```

### Arduino / ESP32

```cpp
#include <WiFi.h>
#include <HTTPClient.h>

void sendToPlexus(const char* metric, float value) {
    HTTPClient http;
    http.begin("https://app.plexus.company/api/ingest");
    http.addHeader("Content-Type", "application/json");
    http.addHeader("x-api-key", "plx_xxxxx");

    String payload = "{\"points\":[{";
    payload += "\"metric\":\"" + String(metric) + "\",";
    payload += "\"value\":" + String(value) + ",";
    payload += "\"timestamp\":" + String(millis() / 1000.0) + ",";
    payload += "\"device_id\":\"esp32-001\"";
    payload += "}]}";

    http.POST(payload);
    http.end();
}
```

### Bash

```bash
#!/bin/bash
API_KEY="plx_xxxxx"
DEVICE_ID="sensor-001"

curl -X POST https://app.plexus.company/api/ingest \
  -H "x-api-key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"points\": [{
      \"metric\": \"temperature\",
      \"value\": 72.5,
      \"timestamp\": $(date +%s),
      \"device_id\": \"$DEVICE_ID\"
    }]
  }"
```

## Python SDK with Sensor Drivers

For Raspberry Pi and other Linux devices, the Python SDK includes sensor drivers:

```bash
pip install plexus-agent[sensors]
plexus pair --code YOUR_CODE
plexus run
```

### Supported Sensors

| Sensor  | Type        | Metrics                                                       | I2C Address |
| ------- | ----------- | ------------------------------------------------------------- | ----------- |
| MPU6050 | 6-axis IMU  | `accel_x`, `accel_y`, `accel_z`, `gyro_x`, `gyro_y`, `gyro_z` | 0x68, 0x69  |
| MPU9250 | 9-axis IMU  | `accel_x`, `accel_y`, `accel_z`, `gyro_x`, `gyro_y`, `gyro_z` | 0x68        |
| BME280  | Environment | `temperature`, `humidity`, `pressure`                         | 0x76, 0x77  |

### Custom Sensors

```python
from plexus.sensors import BaseSensor, SensorReading

class MySensor(BaseSensor):
    name = "MySensor"
    metrics = ["voltage", "current"]

    def read(self):
        return [
            SensorReading("voltage", read_adc(0) * 3.3),
            SensorReading("current", read_adc(1) * 0.1),
        ]
```

## Errors

| Status | Meaning                         |
| ------ | ------------------------------- |
| 200    | Success                         |
| 400    | Bad request (check JSON format) |
| 401    | Invalid or missing API key      |
| 403    | API key lacks permissions       |
| 404    | Resource not found              |
| 410    | Resource expired (e.g., pairing code) |

## Best Practices

- **Batch points** - Send up to 100 points per request for HTTP
- **Use timestamps** - Always include accurate timestamps
- **Consistent device_id** - Use the same ID for each physical device
- **Use tags** - Label data for filtering (e.g., `{"location": "lab"}`)
- **Use sessions** - Group related data for easier analysis
- **Prefer WebSocket** - For real-time UI-controlled devices, use `plexus run`
