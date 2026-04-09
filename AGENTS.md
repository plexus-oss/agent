# AGENTS.md — Plexus Agent

Machine-readable interface for AI assistants and automation scripts.

## Environment Variables

| Variable          | Description                                        | Example                       |
| ----------------- | -------------------------------------------------- | ----------------------------- |
| `PLEXUS_API_KEY`  | API key for authentication                         | `plx_xxxxx`                   |
| `PLEXUS_ENDPOINT` | Server URL (default: `https://app.plexus.company`) | `https://custom.endpoint.com` |
| `PLEXUS_ORG_ID`   | Organization ID                                    | `org_xxxxx`                   |
| `PLEXUS_WS_URL`   | Gateway WebSocket URL (overrides API discovery)    | `ws://localhost:8080`         |

## CLI Commands

```bash
plexus start                           # Interactive setup + stream
plexus start --key plx_xxxxx           # Non-interactive auth
plexus start --device-id my-device     # Set device identifier
plexus reset                           # Clear config and start over
```

Headless mode is auto-detected when output is piped or in a non-TTY environment. Set `PLEXUS_API_KEY` to skip interactive auth in headless contexts.

## Exit Codes

| Code  | Meaning                               |
| ----- | ------------------------------------- |
| `0`   | Clean shutdown                        |
| `1`   | Configuration or authentication error |
| `130` | Interrupted (SIGINT / Ctrl+C)         |

## Python SDK

```python
from plexus import Plexus

px = Plexus(api_key="plx_xxxxx", source_id="device-001")
px.send("temperature", 72.5)
px.send("pressure", 1013.25, tags={"unit": "hPa"})

# Batch
px.send_batch([
    ("temperature", 72.5),
    ("pressure", 1013.25),
])

# Persistent buffering for reliability
px = Plexus(api_key="plx_xxxxx", persistent_buffer=True)
```

## Project Structure

```
plexus/
├── cli.py          # CLI entry point (click commands)
├── tui.py          # Live terminal dashboard (Rich)
├── client.py       # Plexus HTTP client
├── connector.py    # WebSocket connector
├── config.py       # Config file management (~/.plexus/)
├── detect.py       # Hardware auto-detection
├── deps.py         # Dependency management
├── sensors/        # Sensor drivers (I2C, system)
│   ├── base.py     # BaseSensor, SensorHub
│   └── drivers/    # Individual sensor implementations
├── adapters/       # Protocol adapters
│   ├── can.py      # CAN bus
│   ├── mavlink.py  # MAVLink (drones)
│   ├── mqtt.py     # MQTT bridge
│   └── camera.py   # Camera capture
```

## Key Conventions

- Config lives in `~/.plexus/config.json`
- API keys are prefixed with `plx_`
- Source IDs (device slugs) are used for metric namespacing
- Telemetry is sent via WebSocket (real-time) and HTTP (persistence)
- The TUI is enabled by default in interactive terminals; headless mode is auto-detected
