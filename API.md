# Plexus HTTP API

Send telemetry data to Plexus using any HTTP client. No SDK required.

## Quick Start

```bash
curl -X POST https://app.plexusaero.space/api/ingest \
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

1. Sign up at [app.plexusaero.space](https://app.plexusaero.space)
2. Go to Settings → Connections
3. Create an API key (starts with `plx_`)

**Self-Hosted:**

- Default key: `plx_selfhost_default_key_12345678` (change in production)
- Or create via the dashboard at `http://your-server:3000/settings`

Once you have a key, you can use raw HTTP from any language - no SDK needed.

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
| `value`      | number | Yes      | Numeric value                                  |
| `timestamp`  | float  | Yes      | Unix timestamp (seconds, decimals OK)          |
| `device_id`  | string | Yes      | Your device identifier                         |
| `tags`       | object | No       | Key-value labels                               |
| `session_id` | string | No       | Group data into sessions                       |

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

curl -X POST https://app.plexusaero.space/api/ingest \
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

await fetch("https://app.plexusaero.space/api/ingest", {
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
    req, _ := http.NewRequest("POST", "https://app.plexusaero.space/api/ingest", bytes.NewBuffer(body))
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
    "https://app.plexusaero.space/api/ingest",
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
    http.begin("https://app.plexusaero.space/api/ingest");
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

## Why use the Python SDK?

The SDK adds convenience but isn't required:

| Feature     | Raw HTTP         | Python SDK              |
| ----------- | ---------------- | ----------------------- |
| Send data   | Manual JSON      | `px.send("temp", 72.5)` |
| Sessions    | Manual start/end | `with px.session():`    |
| Auth setup  | Manual header    | `plexus login`          |
| Batching    | Manual           | `px.send_batch([...])`  |
| MQTT bridge | Not available    | `plexus mqtt-bridge`    |

Use raw HTTP when:

- You're not using Python
- You want minimal dependencies
- You're on embedded devices (Arduino, ESP32)
- You're building your own client library
