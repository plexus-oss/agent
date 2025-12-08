# Plexus Agent

> Send sensor data to Plexus in one line of code.

```bash
pip install plexus-agent
plexus init
plexus send temperature 72.5
```

Data flowing in 60 seconds.

## Installation

```bash
pip install plexus-agent
```

## Quick Start

### 1. Get your API key

Sign up at [app.plexus.dev](https://app.plexus.dev) and create an API key in Settings.

### 2. Initialize

```bash
plexus init
# Enter your API key when prompted
```

### 3. Send data

```bash
# From the command line
plexus send temperature 72.5
plexus send motor.rpm 3450

# Or stream from any script
python read_sensor.py | plexus stream temperature
```

## Python SDK

```python
from plexus import Plexus

px = Plexus()

# Send a single value
px.send("temperature", 72.5)

# With tags
px.send("motor.rpm", 3450, tags={"motor_id": "A1"})

# Batch send (more efficient for multiple metrics)
px.send_batch([
    ("temperature", 72.5),
    ("humidity", 45.2),
    ("pressure", 1013.25),
])
```

### Session Recording

Group related data together for easy analysis and playback:

```python
from plexus import Plexus
import time

px = Plexus()

with px.session("motor-test-001"):
    for _ in range(1000):
        px.send("temperature", read_temp())
        px.send("rpm", read_rpm())
        time.sleep(0.01)  # 100Hz
```

Sessions appear in your dashboard with start/end timestamps, making it easy to analyze specific test runs.

## CLI Commands

### `plexus init`

Set up your API key and device ID.

```bash
plexus init
# API Key: pk_xxxx...
# Config saved to ~/.plexus/config.json
# Device ID: device-a1b2c3d4
# Testing connection...
# Connected successfully!
```

### `plexus send`

Send a single metric value.

```bash
plexus send temperature 72.5
plexus send motor.rpm 3450 -t motor_id=A1 -t location=lab
plexus send pressure 1013.25 --timestamp 1699900000
```

### `plexus stream`

Stream values from stdin. Perfect for piping sensor data.

```bash
# Stream from a Python script
python read_sensor.py | plexus stream temperature

# Rate-limited to 100 samples/sec
cat data.txt | plexus stream pressure -r 100

# With session tracking
python read_motor.py | plexus stream motor.rpm -s test-001
```

### `plexus status`

Check your connection and configuration.

```bash
plexus status
# Plexus Agent Status
# ========================================
# Config file:  /Users/you/.plexus/config.json
# Endpoint:     https://app.plexus.dev
# Device ID:    device-a1b2c3d4
# API Key:      pk_live_...xxxx
#
# Testing connection...
# Connected!
```

## Configuration

Config is stored in `~/.plexus/config.json`:

```json
{
  "api_key": "pk_live_xxxxxxxxxxxx",
  "endpoint": "https://app.plexus.dev",
  "device_id": "device-a1b2c3d4"
}
```

### Environment Variables

Environment variables override config file values:

- `PLEXUS_API_KEY` - Your API key
- `PLEXUS_ENDPOINT` - API endpoint (for self-hosted instances)

```bash
export PLEXUS_API_KEY=pk_live_xxxx
python my_sensor_script.py
```

## Examples

### Raspberry Pi + DHT22

```python
from plexus import Plexus
import adafruit_dht
import board
import time

px = Plexus()
dht = adafruit_dht.DHT22(board.D4)

while True:
    try:
        px.send("temperature", dht.temperature)
        px.send("humidity", dht.humidity)
    except RuntimeError:
        pass  # DHT22 sometimes fails, just skip
    time.sleep(2)
```

### Arduino Serial Bridge

Read from Arduino over serial and forward to Plexus:

```python
from plexus import Plexus
import serial

px = Plexus()
ser = serial.Serial('/dev/ttyUSB0', 9600)

while True:
    line = ser.readline().decode().strip()
    # Expecting format: "temperature:72.5"
    if ':' in line:
        metric, value = line.split(':')
        px.send(metric, float(value))
```

### Motor Test Stand

```python
from plexus import Plexus
import time

px = Plexus()

with px.session("motor-endurance-test-001"):
    start = time.time()
    while time.time() - start < 3600:  # 1 hour test
        px.send_batch([
            ("motor.rpm", read_rpm()),
            ("motor.current", read_current()),
            ("motor.temperature", read_temp()),
            ("motor.vibration", read_vibration()),
        ])
        time.sleep(0.01)  # 100Hz
```

## Self-Hosting

For self-hosted Plexus instances:

```bash
plexus init --endpoint https://plexus.yourcompany.com
```

Or set the environment variable:

```bash
export PLEXUS_ENDPOINT=https://plexus.yourcompany.com
```

## License

Apache-2.0 - Use freely in your projects, commercial or otherwise.
