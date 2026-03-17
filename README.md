# Plexus Agent

**Open-source Python SDK for hardware observability and telemetry.** Stream sensor data, CAN bus, MAVLink, cameras, and MQTT from any device to [Plexus](https://plexus.company) — the HardwareOps platform for real-time monitoring and fleet management.

[![PyPI](https://img.shields.io/pypi/v/plexus-agent)](https://pypi.org/project/plexus-agent/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

<!-- terminal demo placeholder -->

## Quick Start

```bash
pip install plexus-agent
plexus start --key plx_xxxxx
# That's it. TUI launches, sensors stream, data flows.
```

Get an API key from [app.plexus.company](https://app.plexus.company) → Devices → Add Device.

## What You Get

- **Live terminal dashboard** — real-time TUI enabled by default (like htop for your hardware)
- **Auto-detect hardware** — sensors, cameras, CAN interfaces found and configured automatically
- **12+ sensor drivers** — IMU, environmental, current/power, ADC, magnetometer, GPS, and more
- **Adapters** — CAN bus (with DBC decoding), MAVLink (drones/UAVs), MQTT bridge, USB cameras
- **Commands and remote control** — declare typed commands, get auto-generated UI controls on the dashboard
- **Offline buffering** — local buffer with automatic retry when network drops

Works on any Linux system — Raspberry Pi, edge compute nodes, test rigs, fleet vehicles, ground stations.

## Install

```bash
pip install plexus-agent
```

| Extra        | What it adds                          |
| ------------ | ------------------------------------- |
| `[sensors]`  | I2C sensors (IMU, environmental)      |
| `[can]`      | CAN bus with DBC decoding             |
| `[mavlink]`  | MAVLink for drones/UAVs               |
| `[mqtt]`     | MQTT bridge                           |
| `[camera]`   | USB cameras (OpenCV)                  |
| `[picamera]` | Raspberry Pi Camera Module            |
| `[serial]`   | Serial/UART (GPS, custom devices)     |
| `[tui]`      | Live terminal dashboard               |
| `[system]`   | System health (psutil)                |
| `[all]`      | Everything                            |

```bash
pip install plexus-agent[all]   # install everything at once
```

## Usage

### Authentication

| Method            | How to get it                                             | Used by                              |
| ----------------- | --------------------------------------------------------- | ------------------------------------ |
| API key (`plx_*`) | Dashboard → Devices → Add Device, or Settings → Developer | `plexus start` and `Plexus()` client |

Two ways to authenticate:

1. **API key (recommended):** `plexus start --key plx_xxxxx`
2. **Browser login:** `plexus login` (opens browser for OAuth device flow)

Credentials are stored in `~/.plexus/config.json` or can be set via environment variables:

```bash
export PLEXUS_API_KEY=plx_xxxxx
export PLEXUS_ENDPOINT=https://app.plexus.company  # default
```

### CLI Reference

```
plexus start [--key KEY] [--name NAME] [OPTIONS]       Set up and stream
plexus login                                           Log in via browser
plexus add   [CAPABILITY...]                           Install capabilities (sensors, can, mqtt, ...)
plexus scan  [--all] [--setup] [--json]                Detect connected hardware
plexus status                                          Check connection and config
plexus doctor                                          Diagnose issues
```

### plexus start

Set up and start streaming in one command. Handles auth, hardware detection, dependency installation, and sensor selection. The live terminal dashboard (TUI) is enabled by default. Interactive when run without flags, non-interactive with flags like `--sensor` or `--no-sensors`.

```bash
plexus start                              # Interactive setup with live TUI
plexus start --key plx_xxx                # Non-interactive auth
plexus start --sensor system              # Stream CPU, memory, disk, thermals
plexus start --headless                   # Disable TUI, log-only output
plexus start --mqtt localhost:1883        # Bridge MQTT data
plexus start --no-sensors --no-cameras    # Skip hardware auto-detection
plexus start --auto-install               # Auto-install missing dependencies
plexus start --name "robot-01"            # Name the device
```

| Flag             | Description                                         |
| ---------------- | --------------------------------------------------- |
| `-k, --key`      | API key (skips interactive auth prompt)             |
| `-n, --name`     | Device name for fleet identification                |
| `--slug`         | Device slug (source ID) from dashboard              |
| `--org`          | Organization ID from dashboard                      |
| `-b, --bus`      | I2C bus number (default: 1)                         |
| `-s, --sensor`   | Sensor type to use (e.g. `system`). Repeatable.     |
| `--no-sensors`   | Disable I2C sensor auto-detection                   |
| `--no-cameras`   | Disable camera auto-detection                       |
| `--mqtt`         | MQTT broker to bridge (e.g. `localhost:1883`)       |
| `--mqtt-topic`   | MQTT topic to subscribe to (default: `sensors/#`)   |
| `--headless`     | Disable the live terminal dashboard (TUI)           |
| `--auto-install` | Auto-install missing Python dependencies on demand  |

`plexus start` handles auth, hardware detection, dependency installation, and sensor selection interactively:

```
Found 3 sensors on I2C bus 1:

  [1] BME280       temperature, humidity, pressure
  [2] MPU6050      accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z
  [3] INA219       bus_voltage, shunt_voltage, current_ma, power_mw

Stream all? [Y/n] or enter numbers to select (e.g., 1,3):
```

### plexus add

Install capabilities — like `shadcn add` for hardware. Without arguments, shows an interactive picker with install status.

```bash
plexus add                           # Interactive picker
plexus add can                       # Add CAN bus support
plexus add sensors camera            # Add multiple
```

Available capabilities: `sensors`, `camera`, `picamera`, `mqtt`, `can`, `mavlink`, `serial`, `system`, `tui`.

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

### Direct HTTP

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

See [API.md](https://github.com/plexus-oss/agent/blob/main/API.md) for curl, JavaScript, Go, and Bash examples.

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

This works the same way in the C SDK — see the [C SDK README](https://github.com/plexus-oss/c-sdk) for the equivalent API.

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

| Sensor   | Type           | Metrics                                                   | Interface  |
| -------- | -------------- | --------------------------------------------------------- | ---------- |
| MPU6050  | 6-axis IMU     | accel_x/y/z, gyro_x/y/z                                   | I2C (0x68) |
| MPU9250  | 6-axis IMU     | accel_x/y/z, gyro_x/y/z                                   | I2C (0x68) |
| BME280   | Environmental  | temperature, humidity, pressure                           | I2C (0x76) |
| INA219   | Current/Power  | bus_voltage, shunt_voltage, current_ma, power_mw          | I2C (0x40) |
| SHT3x    | Temp/Humidity  | temperature, humidity                                     | I2C (0x44) |
| BH1750   | Ambient Light  | illuminance                                               | I2C (0x23) |
| VL53L0X  | Time-of-Flight | distance_mm                                               | I2C (0x29) |
| ADS1115  | 16-bit ADC     | channel_0, channel_1, channel_2, channel_3                | I2C (0x48) |
| QMC5883L | Magnetometer   | mag_x, mag_y, mag_z, heading                              | I2C (0x0D) |
| HMC5883L | Magnetometer   | mag_x, mag_y, mag_z, heading                              | I2C (0x1E) |
| GPS      | GPS Receiver   | lat, lon, altitude, speed                                 | Serial     |
| System   | System health  | cpu.temperature, memory.used_pct, disk.used_pct, cpu.load | None       |

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

## MAVLink (Drones / UAVs)

Stream telemetry from MAVLink-speaking vehicles — ArduPilot, PX4, and other autopilots:

```python
from plexus import Plexus
from plexus.adapters import MAVLinkAdapter

px = Plexus(api_key="plx_xxx", source_id="drone-001")
adapter = MAVLinkAdapter(
    connection_string="udpin:0.0.0.0:14550",  # SITL or GCS
)

with adapter:
    while True:
        for metric in adapter.poll():
            px.send(metric.name, metric.value, tags=metric.tags)
```

Decoded metrics include attitude (roll/pitch/yaw), GPS, battery, airspeed, RC channels, and more. Supports UDP, TCP, and serial connections. See `examples/mavlink_basic.py` for more.

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
plexus start --mqtt localhost:1883 --mqtt-topic "sensors/#"
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

The TUI launches by default when you run `plexus start` — no flags needed. It gives you a real-time view of everything streaming from your device, like htop for your hardware. Pass `--headless` to disable the TUI and get log-only output instead.

Keyboard shortcuts: `q` quit | `p` pause | `s` scroll metrics | `?` help

```
+--------------------------------------------------------------+
| Plexus Live Dashboard     * online     ^ 4m 32s              |
+--------------------------------------------------------------+
| Metric       | Value    | Rate   | Buffer | Status           |
+--------------+----------+--------+--------+------------------+
| cpu.temp     | 62.3     | 1.0 Hz | 0      | * streaming      |
| engine.rpm   | 3,450    | 10 Hz  | 0      | * streaming      |
| pressure     | 1013.2   | 1.0 Hz | 0      | * streaming      |
+--------------+----------+--------+--------+------------------+
| Throughput: 12 pts/min   Total: 847   Errors: 0              |
+--------------------------------------------------------------+
```

Requires the `tui` extra: `pip install plexus-agent[tui]`

## Troubleshooting

**Permission denied on I2C**
```bash
sudo usermod -aG i2c $USER && reboot
```

**API key invalid**
Verify your key at [app.plexus.company/devices](https://app.plexus.company/devices). Keys start with `plx_`.

**No sensors detected**
Run `plexus scan --all` to see raw I2C addresses. Check wiring, pull-up resistors, and that the correct I2C bus is selected (`--bus`).

**TUI not showing**
Install the TUI extra: `pip install plexus-agent[tui]`. The TUI also requires a real terminal — it will not render when output is piped or in a non-TTY environment.

**Behind a proxy or firewall**
Set `PLEXUS_ENDPOINT` to your proxy URL. Ensure outbound access on ports 443 and 80.

**Something else?**
Run `plexus doctor` for automated diagnostics covering config, network, auth, dependencies, and hardware permissions.

## Architecture

```
Device (plexus start)
  +-- WebSocket -> PartyKit Server -> Dashboard (real-time)
  +-- HTTP POST -> /api/ingest -> ClickHouse (storage)
```

- **WebSocket path**: Used by `plexus start` for real-time streaming controlled from the dashboard. Data flows through the PartyKit relay to connected browsers.
- **HTTP path**: Used by the `Plexus()` client for direct data ingestion. Data is stored in ClickHouse for historical queries.

When recording a session, both paths are used — WebSocket for live view, HTTP for persistence.

## API Reference

See [API.md](https://github.com/plexus-oss/agent/blob/main/API.md) for the full HTTP and WebSocket protocol specification, including:

- Request/response formats
- All message types
- Code examples in Python, JavaScript, Go, and Bash
- Error codes
- Best practices

## License

Apache 2.0
