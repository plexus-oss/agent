# Plexus Agent

> Send sensor data to Plexus in one line of code.

## Quick Start

```bash
pip install plexus-agent
plexus login                   # Opens browser to authenticate
plexus send temperature 72.5   # Data flowing in 60 seconds
```

That's it. Works on any device with Python - Raspberry Pi, servers, laptops, containers.

> **Note:** `plexus login` connects to [Plexus Cloud](https://app.plexusaero.space) by default. Your data is stored on Plexus servers. For self-hosted deployments where you control the data, see [Self-Hosting](#self-hosting).

## Installation

```bash
pip install plexus-agent

# With MQTT support
pip install plexus-agent[mqtt]
```

Or use the one-liner:

```bash
curl -fsSL https://app.plexusaero.space/install.sh | bash
```

## Authentication

### Browser Login (Recommended)

```bash
plexus login
```

Opens your browser to sign in. API key is saved automatically.

### Manual Setup

If you prefer, get an API key from [app.plexusaero.space/settings](https://app.plexusaero.space/settings?tab=connections):

```bash
plexus init
# Paste your API key when prompted
```

### Self-Hosted

See [SELF-HOST.md](./SELF-HOST.md) for running Plexus on your own infrastructure.

```bash
plexus login --endpoint http://your-server:3000
```

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
| `plexus login` | Authenticate via browser (recommended) |
| `plexus init` | Set up API key manually |
| `plexus send <metric> <value>` | Send a single value |
| `plexus stream <metric>` | Stream from stdin |
| `plexus import <file>` | Import from CSV/TSV file |
| `plexus mqtt-bridge` | Bridge MQTT to Plexus |
| `plexus status` | Check connection |
| `plexus discover` | Find local Plexus instances |
| `plexus config` | Show current configuration |

### Send Examples

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

### Import CSV Files

Import historical data from CSV or TSV files:

```bash
# Basic import
plexus import sensor_data.csv

# With session grouping (for playback)
plexus import flight_log.csv -s "flight-001"

# Custom timestamp column and format
plexus import data.csv -t time_ms --timestamp-format unix_ms

# Preview without uploading
plexus import data.csv --dry-run
```

Supported timestamp formats: `auto`, `unix`, `unix_ms`, `iso`

### MQTT Bridge

Forward MQTT messages to Plexus:

```bash
# Install MQTT support
pip install plexus-agent[mqtt]

# Connect to local broker
plexus mqtt-bridge

# Specific broker and topic
plexus mqtt-bridge -b mqtt.example.com -t "sensors/#"

# With authentication
plexus mqtt-bridge -b broker.hivemq.com -u user --password pass

# Strip topic prefix (sensors/temp → temp)
plexus mqtt-bridge -t "home/sensors/#" --prefix "home/"
```

## Self-Hosting

Run Plexus on your own infrastructure. Your data stays on your servers.

```bash
# Deploy the stack
git clone https://github.com/plexus-oss/agent
cd agent
docker compose up -d

# Connect to YOUR instance (--endpoint is required for self-hosted)
plexus login --endpoint http://localhost:3000
```

**Important:** The `--endpoint` flag tells the agent where to send data. Without it, data goes to Plexus Cloud.

```bash
# Verify your agent is pointing to the right place
plexus config
```

See [SELF-HOST.md](./SELF-HOST.md) for full documentation including LAN discovery, MQTT setup, and production configuration.

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
