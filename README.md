# Plexus Agent

Connect your hardware to [Plexus](https://app.plexus.company) and control everything from the web dashboard.

## Quick Start

### One-Line Setup (Recommended)

From your Plexus dashboard, click **"Pair Device"** to get a pairing code, then run:

```bash
curl -sL https://app.plexus.company/setup | bash -s -- --code YOUR_CODE
```

This installs the agent, pairs your device, and sets up auto-start on boot.

### Manual Setup

```bash
pip install plexus-agent[sensors]
plexus pair --code YOUR_CODE
plexus run
```

View and control your device at [app.plexus.company/fleet](https://app.plexus.company/fleet)

## How It Works

```
┌─────────────────────────────────────────────────────────────────────┐
│                         PLEXUS DASHBOARD                             │
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │
│  │ Start/Stop  │  │  Recording  │  │   Sensor    │  │    Quick    │ │
│  │  Streaming  │  │   Sessions  │  │   Config    │  │   Actions   │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ WebSocket
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         YOUR DEVICE                                  │
│                                                                      │
│   $ plexus run                                                       │
│   ✓ Connected to Plexus                                              │
│   ✓ 3 sensors detected (MPU6050, BME280, BH1750)                    │
│   ✓ Waiting for commands...                                          │
│                                                                      │
│   [Sensors] ──► [Agent] ◄──► [Dashboard]                            │
└─────────────────────────────────────────────────────────────────────┘
```

**No terminal interaction needed after setup.** All control happens from the web UI:

- Start/stop live sensor streaming
- Record data to sessions
- Configure sensor sample rates
- Run diagnostics, view logs, reboot device

## CLI Reference

The agent has just 4 commands:

| Command         | Description                              |
| --------------- | ---------------------------------------- |
| `plexus run`    | Start agent daemon (main command)        |
| `plexus pair`   | Pair device with your Plexus account     |
| `plexus status` | Show connection and sensor status        |
| `plexus scan`   | Detect connected sensors                 |

### plexus run

Start the agent in daemon mode. Connects to Plexus and waits for commands from the dashboard.

```bash
plexus run                    # Start with auto-detected sensors
plexus run --name "Robot Arm" # Set a device name
plexus run --no-sensors       # Disable sensor auto-detection
```

### plexus pair

Pair this device with your Plexus account using a code from the dashboard.

```bash
plexus pair --code ABC123     # Pair with code from dashboard
plexus pair                   # Interactive OAuth flow
```

### plexus status

Show current connection status and detected sensors.

```bash
plexus status
# Device ID: pi-living-room
# Status: Connected
# Sensors: MPU6050 (100Hz), BME280 (1Hz)
```

### plexus scan

Scan for connected I2C sensors.

```bash
plexus scan
# Found sensors:
#   MPU6050 at 0x68 - 6-axis IMU (accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z)
#   BME280 at 0x76 - Environment (temperature, humidity, pressure)
```

## Supported Sensors

Auto-detected I2C sensors:

| Sensor  | Type        | Metrics                         | I2C Address |
| ------- | ----------- | ------------------------------- | ----------- |
| MPU6050 | 6-axis IMU  | accel_x/y/z, gyro_x/y/z         | 0x68, 0x69  |
| MPU9250 | 9-axis IMU  | accel_x/y/z, gyro_x/y/z         | 0x68        |
| BME280  | Environment | temperature, humidity, pressure | 0x76, 0x77  |

## Custom Sensors

Add your own sensors using the Python API:

```python
from plexus.sensors import BaseSensor, SensorReading, SensorHub

class VoltageSensor(BaseSensor):
    name = "Voltage"
    description = "ADC voltage monitor"
    metrics = ["voltage", "current"]

    def read(self):
        return [
            SensorReading("voltage", read_adc(0) * 3.3),
            SensorReading("current", read_adc(1) * 0.1),
        ]

# Add to hub and run
from plexus.connector import run_connector

hub = SensorHub()
hub.add(VoltageSensor(sample_rate=10))
run_connector(sensor_hub=hub)
```

## Running as a Service

The setup script automatically creates a systemd service. To manage it manually:

```bash
# Enable auto-start on boot
sudo systemctl enable plexus

# Start/stop/restart
sudo systemctl start plexus
sudo systemctl stop plexus
sudo systemctl restart plexus

# View logs
journalctl -u plexus -f
```

Service file location: `/etc/systemd/system/plexus.service`

## Python SDK (Direct Data Sending)

For custom integrations where you want to send data directly:

```python
from plexus import Plexus

px = Plexus()
px.send("temperature", 72.5)
px.send("motor.rpm", 3450, tags={"motor_id": "A1"})
```

### Batch Send

```python
px.send_batch([
    ("temperature", 72.5),
    ("humidity", 45.2),
    ("pressure", 1013.25),
])
```

### Session Recording

```python
with px.session("motor-test-001"):
    for _ in range(1000):
        px.send("temperature", read_temp())
        time.sleep(0.01)  # 100Hz
```

## Not Using Python?

Send data with any HTTP client:

```bash
curl -X POST https://app.plexus.company/api/ingest \
  -H "x-api-key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"points": [{"metric": "temperature", "value": 72.5, "device_id": "pi-001"}]}'
```

See [API.md](./API.md) for JavaScript, Go, Arduino examples, and WebSocket protocol details.

## Installation Options

```bash
pip install plexus-agent              # Core SDK
pip install plexus-agent[sensors]     # + I2C sensor drivers (recommended)
pip install plexus-agent[all]         # Everything
```

## License

Apache-2.0
