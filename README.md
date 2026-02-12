# Plexus Agent

Stream telemetry from any device to [Plexus](https://plexus.dev) — real-time observability for hardware systems.

```python
from plexus import Plexus

px = Plexus()
px.send("engine.rpm", 3450, tags={"unit": "A"})
```

Works on any Linux system — edge compute nodes, test rigs, fleet vehicles, ground stations.

## Install

```bash
pip install plexus-agent
```

With protocol support:

```bash
pip install plexus-agent[sensors]    # I2C sensors (IMU, environmental)
pip install plexus-agent[can]        # CAN bus with DBC decoding
pip install plexus-agent[mqtt]       # MQTT bridge
pip install plexus-agent[camera]     # USB cameras (OpenCV)
pip install plexus-agent[ros]        # ROS1/ROS2 bag import
pip install plexus-agent[all]        # Everything
```

## Quick Start

The fastest way to get started — one command, handles everything:

```bash
curl -sL https://app.plexus.company/setup | bash -s -- --key plx_your_api_key
```

This installs Python (if needed), installs the agent, configures your API key, and sets up auto-start. Get an API key from [app.plexus.company](https://app.plexus.company) → Settings → Developer.

### Option 1: Managed Device (recommended)

Pair your device with the dashboard and control streaming, recording, and configuration from the web UI.

```bash
# 1. Pair (one-time) — get the code from app.plexus.company/fleet
plexus pair --code ABC123

# 2. Run the agent
plexus run
```

The agent auto-detects connected sensors, cameras, and CAN interfaces. Control everything from the dashboard.

```bash
# Name the device for fleet identification
plexus run --name "test-rig-01"

# Stream system health (CPU, memory, disk, thermals)
plexus run --sensor system

# Bridge an MQTT broker
plexus run --mqtt localhost:1883

# Skip sensor/camera auto-detection
plexus run --no-sensors --no-cameras
```

### Option 2: Direct HTTP

Send data programmatically without the managed agent. Good for scripts, batch uploads, and custom integrations.

1. Create an API key at [app.plexus.company](https://app.plexus.company) → Settings → Developer
2. Send data:

```python
from plexus import Plexus

px = Plexus(api_key="plx_xxxxx", source_id="test-rig-01")

# Numeric telemetry
px.send("engine.rpm", 3450, tags={"unit": "A"})
px.send("coolant.temperature", 82.3)

# State and configuration
px.send("vehicle.state", "RUNNING")
px.send("motor.enabled", True)
px.send("position", {"x": 1.5, "y": 2.3, "z": 0.8})

# Batch send
px.send_batch([
    ("temperature", 72.5),
    ("pressure", 1013.25),
    ("vibration.rms", 0.42),
])
```

See [API.md](API.md) for curl, JavaScript, Go, and Bash examples.

## Authentication

| Credential   | Prefix  | How to get it                              | Used by                     |
|-------------|---------|--------------------------------------------|-----------------------------|
| Device token | `plxd_` | `plexus pair` (automatic)                  | `plexus run` (WebSocket)    |
| API key      | `plx_`  | Dashboard → Settings → Developer          | `Plexus()` client (HTTP)    |

Credentials are stored in `~/.plexus/config.json` or can be set via environment variables:

```bash
export PLEXUS_API_KEY=plx_xxxxx
export PLEXUS_DEVICE_TOKEN=plxd_xxxxx
export PLEXUS_ENDPOINT=https://app.plexus.company  # default
```

## CLI Reference

```
plexus pair [--code CODE]    Pair device with your account
plexus run  [OPTIONS]        Start the agent
plexus scan [--setup]        Detect connected hardware
plexus status                Check connection and config
```

Run `plexus <command> --help` for full options.

## Sessions

Group related data for analysis and playback:

```python
with px.session("thermal-cycle-001"):
    while running:
        px.send("temperature", read_temp())
        px.send("vibration.rms", read_accel())
        time.sleep(0.01)
```

## Sensors

Auto-detect all connected I2C sensors:

```python
from plexus import Plexus
from plexus.sensors import auto_sensors

hub = auto_sensors()       # finds IMU, environmental, etc.
hub.run(Plexus())          # streams forever
```

Or configure manually:

```python
from plexus.sensors import SensorHub, MPU6050, BME280

hub = SensorHub()
hub.add(MPU6050(sample_rate=100))
hub.add(BME280(sample_rate=1))
hub.run(Plexus())
```

Built-in sensor drivers:

| Sensor  | Type                | Metrics                                          | Interface   |
|---------|---------------------|--------------------------------------------------|-------------|
| MPU6050 | 6-axis IMU          | accel_x/y/z, gyro_x/y/z                         | I2C (0x68)  |
| MPU9250 | 9-axis IMU          | accel_x/y/z, gyro_x/y/z                         | I2C (0x68)  |
| BME280  | Environmental       | temperature, humidity, pressure                   | I2C (0x76)  |
| System  | System health       | cpu.temperature, memory.used_pct, disk.used_pct, cpu.load | None  |

### Custom Sensors

Write a driver for any hardware by extending `BaseSensor`:

```python
from plexus.sensors import BaseSensor, SensorReading

class StrainGauge(BaseSensor):
    name = "StrainGauge"
    description = "Load cell strain gauge via ADC"
    metrics = ["strain", "force_n"]

    def read(self):
        raw = self.adc.read_channel(0)
        strain = (raw / 4096.0) * self.calibration_factor
        return [
            SensorReading("strain", round(strain, 6)),
            SensorReading("force_n", round(strain * self.k_factor, 2)),
        ]
```

## CAN Bus

Read CAN bus data with optional DBC signal decoding:

```python
from plexus import Plexus
from plexus.adapters import CANAdapter

px = Plexus(api_key="plx_xxx", source_id="vehicle-001")
adapter = CANAdapter(
    interface="socketcan",
    channel="can0",
    dbc_path="vehicle.dbc",  # optional: decode signals
)

with adapter:
    while True:
        for metric in adapter.poll():
            px.send(metric.name, metric.value, tags=metric.tags)
```

Supports socketcan, pcan, vector, kvaser, and slcan interfaces. See `examples/can_basic.py` for more.

## MQTT Bridge

Forward MQTT messages to Plexus:

```python
from plexus.adapters import MQTTAdapter

adapter = MQTTAdapter(broker="localhost", topic="sensors/#")
adapter.connect()
adapter.run(on_data=my_callback)
```

Or bridge directly from the CLI:

```bash
plexus run --mqtt localhost:1883 --mqtt-topic "sensors/#"
```

## Buffering and Reliability

The client buffers data locally when the network is unavailable:

- In-memory buffer (default, up to 10,000 points)
- Persistent SQLite buffer for surviving restarts
- Automatic retry with exponential backoff
- Buffered points are sent with the next successful request

```python
# Enable persistent buffering
px = Plexus(persistent_buffer=True)

# Check buffer state
print(px.buffer_size())
px.flush_buffer()
```

## Architecture

```
Device (plexus run)
  ├── WebSocket → PartyKit Server → Dashboard (real-time)
  └── HTTP POST → /api/ingest → ClickHouse (storage)
```

- **WebSocket path**: Used by `plexus run` for real-time streaming controlled from the dashboard. Data flows through the PartyKit relay to connected browsers.
- **HTTP path**: Used by the `Plexus()` client for direct data ingestion. Data is stored in ClickHouse for historical queries.

When recording a session, both paths are used — WebSocket for live view, HTTP for persistence.

## API Reference

See [API.md](API.md) for the full HTTP and WebSocket protocol specification, including:

- Request/response formats
- All message types
- Code examples in Python, JavaScript, Go, and Bash
- Error codes
- Best practices

## License

Apache 2.0
