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

With extras:

```bash
pip install plexus-agent[sensors]    # I2C sensors (IMU, environmental)
pip install plexus-agent[can]        # CAN bus with DBC decoding
pip install plexus-agent[mqtt]       # MQTT bridge
pip install plexus-agent[camera]     # USB cameras (OpenCV)
pip install plexus-agent[serial]     # Serial/UART devices
pip install plexus-agent[ros]        # ROS1/ROS2 bag import
pip install plexus-agent[tui]        # Live terminal dashboard
pip install plexus-agent[system]     # System health (psutil)
pip install plexus-agent[all]        # Everything
```

## Quick Start

One command from install to streaming:

```bash
pip install plexus-agent && plexus start --key plx_xxxxx
```

`plexus start` handles auth, hardware detection, dependency installation, and sensor selection interactively:

```
Found 3 sensors on I2C bus 1:

  [1] ✓ BME280       temperature, humidity, pressure
  [2] ✓ MPU6050      accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z
  [3] ✓ INA219       bus_voltage, current_ma, power_mw

Stream all? [Y/n] or enter numbers to select (e.g., 1,3):
```

Get an API key from [app.plexus.company](https://app.plexus.company) → Fleet → Add Device.

### Option 1: One-liner (recommended)

```bash
plexus start --key plx_xxxxx
```

### Option 2: Step by step

```bash
# 1. Pair (one-time) — get your API key from app.plexus.company/fleet
plexus pair --key plx_xxxxx

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

| Method            | How to get it                                           | Used by                            |
| ----------------- | ------------------------------------------------------- | ---------------------------------- |
| API key (`plx_*`) | Dashboard → Fleet → Add Device, or Settings → Developer | `plexus run` and `Plexus()` client |

Two ways to pair:

1. **API key (recommended):** `plexus pair --key plx_xxxxx`
2. **Browser login:** `plexus pair` (opens browser for OAuth device flow)

Credentials are stored in `~/.plexus/config.json` or can be set via environment variables:

```bash
export PLEXUS_API_KEY=plx_xxxxx
export PLEXUS_ENDPOINT=https://app.plexus.company  # default
```

## CLI Reference

```
plexus start [--key KEY] [--bus N] [--name NAME]      Set up and stream (interactive)
plexus add   [CAPABILITY...]                           Install capabilities (sensors, can, mqtt, ...)
plexus run   [--live] [--auto-install] [OPTIONS]       Start the agent
plexus pair  [--key KEY]                               Pair device with your account
plexus scan  [--all] [--setup] [--json]                Detect connected hardware
plexus status                                          Check connection and config
plexus doctor                                          Diagnose issues
```

### plexus start

Set up and start streaming in one command. Handles auth, hardware detection, dependency installation, and sensor selection interactively.

```bash
plexus start                         # Interactive setup
plexus start --key plx_xxx           # Non-interactive auth
plexus start --key plx_xxx -b 0      # Specify I2C bus
plexus start --name "robot-01"       # Name the device
```

| Flag         | Description                              |
| ------------ | ---------------------------------------- |
| `-k, --key`  | API key (skips interactive auth prompt)  |
| `-n, --name` | Device name for fleet identification     |
| `-b, --bus`  | I2C bus number (default: 1)              |

### plexus add

Install capabilities — like `shadcn add` for hardware. Without arguments, shows an interactive picker with install status.

```bash
plexus add                           # Interactive picker
plexus add can                       # Add CAN bus support
plexus add sensors camera            # Add multiple
```

Available capabilities: `sensors`, `camera`, `mqtt`, `can`, `serial`, `system`, `tui`, `ros`.

### plexus run

Start the agent. Connects to Plexus and streams telemetry controlled from the dashboard.

```bash
plexus run                              # Start with auto-detected hardware
plexus run --live                       # Live terminal dashboard (like htop)
plexus run --sensor system              # Stream CPU, memory, disk, thermals
plexus run --auto-install               # Auto-install missing dependencies
plexus run --mqtt localhost:1883        # Bridge MQTT data
plexus run --no-sensors --no-cameras    # Skip hardware auto-detection
```

| Flag             | Description                                         |
| ---------------- | --------------------------------------------------- |
| `-n, --name`     | Device name for fleet identification                |
| `--live`         | Show live terminal dashboard with real-time metrics |
| `--auto-install` | Auto-install missing Python dependencies on demand  |
| `--no-sensors`   | Disable I2C sensor auto-detection                   |
| `--no-cameras`   | Disable camera auto-detection                       |
| `-b, --bus`      | I2C bus number (default: 1)                         |
| `-s, --sensor`   | Sensor type to use (e.g. `system`). Repeatable.     |
| `--mqtt`         | MQTT broker to bridge (e.g. `localhost:1883`)       |
| `--mqtt-topic`   | MQTT topic to subscribe to (default: `sensors/#`)   |

### plexus scan

Detect all connected hardware — I2C sensors, cameras, serial ports, USB devices, network interfaces, GPIO, Bluetooth, and system info.

```bash
plexus scan                # Full hardware scan
plexus scan --all          # Show all I2C addresses (including unknown)
plexus scan --setup        # Auto-configure CAN interfaces
plexus scan --json         # Machine-readable JSON output
```

### plexus doctor

Diagnose connectivity, configuration, and dependency issues. Checks config files, network reachability, authentication, installed dependencies, and hardware permissions.

```bash
plexus doctor              # Run all diagnostics
```

Run `plexus <command> --help` for full options.

## Commands & Remote Control

Declare typed commands on your device. The dashboard auto-generates UI controls — sliders, dropdowns, toggles — from the schema.

```python
from plexus import Plexus, param

px = Plexus()

@px.command("set_speed", description="Set motor speed")
@param("rpm", type="float", min=0, max=10000, unit="rpm")
@param("ramp_time", type="float", min=0.1, max=10.0, default=1.0, unit="s")
async def set_speed(rpm, ramp_time):
    motor.set_rpm(rpm, ramp=ramp_time)
    return {"actual_rpm": motor.read_rpm()}

@px.command("set_mode", description="Switch operating mode")
@param("mode", type="enum", choices=["idle", "run", "calibrate"])
async def set_mode(mode):
    controller.set_mode(mode)
```

Commands are sent to the device over WebSocket and executed in real time. The dashboard shows:

- Parameter inputs with validation (min/max, type checking, required fields)
- Execution status and results
- Command history

This works the same way in the C SDK — see the [C SDK README](../c-sdk/README.md#typed-commands) for the equivalent API.

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

| Sensor  | Type          | Metrics                                                   | Interface  |
| ------- | ------------- | --------------------------------------------------------- | ---------- |
| MPU6050 | 6-axis IMU    | accel_x/y/z, gyro_x/y/z                                   | I2C (0x68) |
| MPU9250 | 9-axis IMU    | accel_x/y/z, gyro_x/y/z                                   | I2C (0x68) |
| BME280  | Environmental | temperature, humidity, pressure                           | I2C (0x76) |
| System  | System health | cpu.temperature, memory.used_pct, disk.used_pct, cpu.load | None       |

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

## Live Terminal Dashboard

Run `plexus run --live` to get a real-time terminal UI — like htop for your hardware:

```
┌──────────────────────────────────────────────────────────────┐
│ Plexus Live Dashboard     ● online     ↑ 4m 32s             │
├──────────────┬──────────┬────────┬────────┬─────────────────┤
│ Metric       │ Value    │ Rate   │ Buffer │ Status          │
├──────────────┼──────────┼────────┼────────┼─────────────────┤
│ cpu.temp     │ 62.3     │ 1.0 Hz │ 0      │ ● streaming    │
│ engine.rpm   │ 3,450    │ 10 Hz  │ 0      │ ● streaming    │
│ pressure     │ 1013.2   │ 1.0 Hz │ 0      │ ● streaming    │
└──────────────┴──────────┴────────┴────────┴─────────────────┘
│ Throughput: 12 pts/min   Total: 847   Errors: 0             │
└──────────────────────────────────────────────────────────────┘
```

Requires the `tui` extra: `pip install plexus-agent[tui]`

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
