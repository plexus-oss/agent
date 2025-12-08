# Plexus Agent

> Send sensor data to Plexus in one line of code.

## Quick Start

```bash
pip install plexus-agent
plexus init        # paste your API key from app.plexusaero.space
plexus send temperature 72.5
```

That's it. Works on any device with Python - Raspberry Pi, servers, laptops, containers.

## Get Your API Key

1. Go to [app.plexusaero.space/settings](https://app.plexusaero.space/settings?tab=connections)
2. Create an API key
3. Copy the key and paste it when running `plexus init`

## Sending Data

### Command Line

```bash
# Basic
plexus send temperature 72.5

# With tags (for multiple sensors)
plexus send motor.temperature 72.5 -t motor_id=A1
plexus send motor.temperature 68.3 -t motor_id=A2

# Stream from any script
python read_sensor.py | plexus stream temperature
```

### Python SDK

```python
from plexus import Plexus

px = Plexus()

# Send values
px.send("temperature", 72.5)
px.send("motor.rpm", 3450, tags={"motor_id": "A1"})

# Batch send (more efficient)
px.send_batch([
    ("temperature", 72.5),
    ("humidity", 45.2),
    ("pressure", 1013.25),
])
```

### Session Recording

Group related data for easy analysis:

```python
from plexus import Plexus

px = Plexus()

with px.session("motor-test-001"):
    for _ in range(1000):
        px.send("temperature", read_temp())
        px.send("rpm", read_rpm())
        time.sleep(0.01)  # 100Hz
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `plexus init` | Set up API key |
| `plexus send <metric> <value>` | Send a single value |
| `plexus stream <metric>` | Stream from stdin |
| `plexus status` | Check connection |

### Examples

```bash
# Send with tags
plexus send motor.rpm 3450 -t motor_id=A1 -t location=lab

# Send with timestamp
plexus send pressure 1013.25 --timestamp 1699900000

# Stream with rate limiting
cat data.txt | plexus stream pressure -r 100

# Stream with session
python read_motor.py | plexus stream motor.rpm -s test-001
```

## Configuration

Config is stored in `~/.plexus/config.json`.

### Environment Variables

Override config with environment variables:

```bash
export PLEXUS_API_KEY=plx_xxxxx
export PLEXUS_ENDPOINT=https://plexus.yourcompany.com  # for self-hosted
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
        pass
    time.sleep(2)
```

### Arduino Serial Bridge

```python
from plexus import Plexus
import serial

px = Plexus()
ser = serial.Serial('/dev/ttyUSB0', 9600)

while True:
    line = ser.readline().decode().strip()
    if ':' in line:
        metric, value = line.split(':')
        px.send(metric, float(value))
```

### Motor Test Stand

```python
from plexus import Plexus
import time

px = Plexus()

with px.session("endurance-test-001"):
    start = time.time()
    while time.time() - start < 3600:  # 1 hour
        px.send_batch([
            ("motor.rpm", read_rpm()),
            ("motor.current", read_current()),
            ("motor.temperature", read_temp()),
        ])
        time.sleep(0.01)  # 100Hz
```

## License

Apache-2.0
