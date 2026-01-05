# Plexus API

Send telemetry data to Plexus using HTTP or WebSocket.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              PLEXUS CLOUD                                 │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   Frontend (Vercel)                    WebSocket Server (Fly.io)         │
│   app.plexus.company                   server-*.fly.dev                  │
│   ├── /api/ingest      POST           ├── /ws/device     Device conn    │
│   ├── /api/sessions    POST           └── /ws/browser    Browser conn   │
│   ├── /api/config      GET  (public)                                    │
│   └── /api/auth/verify-key GET                                          │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘

         ▲                                        ▲
         │ HTTP                                   │ WebSocket
         │                                        │
    ┌────┴────┐                              ┌────┴────┐
    │ Sensors │                              │   CLI   │
    │ Scripts │  plexus send / HTTP POST     │ plexus  │
    │ Devices │                              │ connect │
    └─────────┘                              └─────────┘
```

**Two ways to send data:**

| Method | Endpoint | Use Case |
|--------|----------|----------|
| HTTP POST | `/api/ingest` | Simple, batch, one-way telemetry |
| WebSocket | `/ws/device` | Bidirectional, real-time, remote commands |

**Discovery:** Call `GET /api/config` to get the WebSocket server URL.

## Quick Start

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

## Getting an API Key

**Plexus Cloud:**

1. Sign up at [app.plexus.company](https://app.plexus.company)
2. Go to Settings → Connections
3. Create an API key (starts with `plx_`)

## Endpoints

| Endpoint        | Method | Description                  |
| --------------- | ------ | ---------------------------- |
| `/api/ingest`   | POST   | Send telemetry data          |
| `/api/sessions` | POST   | Start/end recording sessions |

## Authentication

All requests require an API key in the header:

```
x-api-key: plx_xxxxx
```

## Send Data

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

Plexus accepts multiple value types to support diverse sensor data:

| Type    | Example                          | Use Case                         |
| ------- | -------------------------------- | -------------------------------- |
| number  | `72.5`, `-40`, `3.14159`         | Numeric readings (most common)   |
| string  | `"error"`, `"idle"`, `"running"` | Status, state, labels            |
| boolean | `true`, `false`                  | On/off, enabled/disabled         |
| object  | `{"x": 1.2, "y": 3.4, "z": 5.6}` | Vector data, structured readings |
| array   | `[1.0, 2.0, 3.0, 4.0]`           | Waveforms, multiple values       |

```json
{
  "points": [
    { "metric": "temperature", "value": 72.5, "device_id": "pi-001" },
    { "metric": "motor_state", "value": "running", "device_id": "pi-001" },
    { "metric": "armed", "value": true, "device_id": "pi-001" },
    {
      "metric": "accel",
      "value": { "x": 0.1, "y": 0.2, "z": 9.8 },
      "device_id": "pi-001"
    },
    {
      "metric": "fft_bins",
      "value": [0.1, 0.5, 0.8, 0.3],
      "device_id": "pi-001"
    }
  ]
}
```

**Response:** `200 OK` on success

## Sessions

Group related data for analysis and playback.

**Start session:**

```json
POST /api/sessions
{
  "session_id": "test-001",
  "device_id": "sensor-001",
  "status": "started",
  "timestamp": 1699900000
}
```

**End session:**

```json
POST /api/sessions
{
  "session_id": "test-001",
  "device_id": "sensor-001",
  "status": "ended",
  "timestamp": 1699903600
}
```

## Examples

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

### JavaScript

```javascript
const API_KEY = "plx_xxxxx";
const DEVICE_ID = "sensor-001";

await fetch("https://app.plexus.company/api/ingest", {
  method: "POST",
  headers: {
    "x-api-key": API_KEY,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    points: [
      {
        metric: "temperature",
        value: 72.5,
        timestamp: Date.now() / 1000,
        device_id: DEVICE_ID,
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

### Python (no SDK)

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

## Self-Hosted

For self-hosted instances, replace the endpoint:

```bash
curl -X POST http://your-server:3000/api/ingest \
  -H "x-api-key: plx_selfhost_default_key_12345678" \
  ...
```

## Errors

| Status | Meaning                         |
| ------ | ------------------------------- |
| 200    | Success                         |
| 400    | Bad request (check JSON format) |
| 401    | Invalid or missing API key      |
| 403    | API key lacks permissions       |

## Best Practices

- **Batch points** - Send up to 100 points per request
- **Use timestamps** - Always include accurate timestamps
- **Consistent device_id** - Use the same ID for each physical device
- **Use tags** - Label data for filtering (e.g., `{"location": "lab", "unit": "celsius"}`)

## Python SDK with Sensor Drivers

The Python SDK includes pre-built drivers for common sensors. Zero configuration required.

### Quick Start (Raspberry Pi)

```bash
pip install plexus-agent[sensors]
plexus login
plexus run  # Auto-detects and streams all connected sensors
```

### Supported Sensors

| Sensor  | Type        | Metrics                                                       | I2C Address |
| ------- | ----------- | ------------------------------------------------------------- | ----------- |
| MPU6050 | 6-axis IMU  | `accel_x`, `accel_y`, `accel_z`, `gyro_x`, `gyro_y`, `gyro_z` | 0x68, 0x69  |
| MPU9250 | 9-axis IMU  | `accel_x`, `accel_y`, `accel_z`, `gyro_x`, `gyro_y`, `gyro_z` | 0x68        |
| BME280  | Environment | `temperature`, `humidity`, `pressure`                         | 0x76, 0x77  |

### CLI Commands

```bash
plexus sensors        # List all supported sensors and their metrics
plexus scan           # Detect sensors connected to your device
plexus run            # Stream all detected sensors to Plexus
plexus run --rate 50  # Override sample rate (Hz)
```

### Python API

```python
from plexus import Plexus
from plexus.sensors import SensorHub, MPU6050, BME280

# Manual setup
hub = SensorHub()
hub.add(MPU6050(sample_rate=100))  # 100 Hz for IMU
hub.add(BME280(sample_rate=1))     # 1 Hz for environmental
hub.run(Plexus())
```

### Auto-Detection

```python
from plexus import Plexus
from plexus.sensors import auto_sensors

hub = auto_sensors()  # Scans I2C bus, creates drivers
hub.run(Plexus())     # Streams everything
```

### Custom Sensors

Extend `BaseSensor` to add your own:

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

hub = SensorHub()
hub.add(MySensor(sample_rate=10))
hub.run(Plexus())
```

## Why use the Python SDK?

The SDK adds convenience but isn't required:

| Feature        | Raw HTTP         | Python SDK              |
| -------------- | ---------------- | ----------------------- |
| Send data      | Manual JSON      | `px.send("temp", 72.5)` |
| Sessions       | Manual start/end | `with px.session():`    |
| Auth setup     | Manual header    | `plexus login`          |
| Batching       | Manual           | `px.send_batch([...])`  |
| MQTT bridge    | Not available    | `plexus mqtt-bridge`    |
| Sensor drivers | Not available    | `plexus run`            |
| Auto-detect    | Not available    | `plexus scan`           |

Use raw HTTP when:

- You're not using Python
- You want minimal dependencies
- You're on embedded devices (Arduino, ESP32)
- You're building your own client library

## WebSocket API (Bidirectional)

For real-time streaming and remote command execution, use WebSocket.

### Connect (CLI)

```bash
plexus connect
```

This connects to the WebSocket server and enables:
- Real-time telemetry streaming
- Remote command execution from dashboard
- Sensor configuration changes

### Discovery

Get the WebSocket URL from the config endpoint:

```bash
curl https://app.plexus.company/api/config
```

Response:
```json
{
  "ws_url": "wss://server-dawn-fire-2564.fly.dev",
  "version": "1.0",
  "features": {
    "bidirectional_streaming": true,
    "remote_commands": true
  }
}
```

### Manual WebSocket Connection

Connect with your API key in headers:

```
WebSocket: wss://{ws_url}/ws/device
Headers:
  x-api-key: plx_xxxxx
  x-device-id: my-device-001
  x-platform: Linux
```

### Message Types (Device → Server)

| Type | Description |
|------|-------------|
| `handshake` | Initial device info |
| `telemetry` | Sensor data points |
| `output` | Command output |
| `pong` | Keepalive response |

### Message Types (Server → Device)

| Type | Description |
|------|-------------|
| `execute` | Run a shell command |
| `cancel` | Cancel running command |
| `start_stream` | Start sensor streaming |
| `stop_stream` | Stop sensor streaming |
| `configure` | Configure a sensor |
| `ping` | Keepalive request |

### Example: Telemetry Message

```json
{
  "type": "telemetry",
  "points": [
    {"metric": "temperature", "value": 72.5, "timestamp": 1699900000}
  ]
}
```

### Example: Execute Command

```json
{
  "type": "execute",
  "id": "cmd-123",
  "command": "uname -a"
}
```

Response (streamed):
```json
{"type": "output", "id": "cmd-123", "event": "start", "command": "uname -a"}
{"type": "output", "id": "cmd-123", "event": "data", "data": "Linux raspberrypi..."}
{"type": "output", "id": "cmd-123", "event": "exit", "code": 0}
```
