# Plexus Agent

Send sensor data to [Plexus](https://app.plexus.company) in 3 commands.

## Quick Start

```bash
pip install plexus-agent
plexus login
plexus send temperature 72.5
```

View your data at [app.plexus.company](https://app.plexus.company)

Works on Raspberry Pi, servers, laptops, containers - anything with Python.

## Sending Data

### Command Line

```bash
plexus send temperature 72.5
plexus send motor.rpm 3450 -t motor_id=A1
python read_sensor.py | plexus stream temperature
```

### Python SDK

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

Group related data for easy analysis:

```python
with px.session("motor-test-001"):
    for _ in range(1000):
        px.send("temperature", read_temp())
        px.send("rpm", read_rpm())
        time.sleep(0.01)  # 100Hz
```

## CLI Reference

| Command                        | Description         |
| ------------------------------ | ------------------- |
| `plexus login`                 | Sign in via browser |
| `plexus send <metric> <value>` | Send a value        |
| `plexus stream <metric>`       | Stream from stdin   |
| `plexus import <file>`         | Import CSV/TSV file |
| `plexus status`                | Check connection    |

### Import CSV

```bash
plexus import sensor_data.csv
plexus import flight_log.csv -s "flight-001"
plexus import data.csv --dry-run
```

### MQTT Bridge

```bash
pip install plexus-agent[mqtt]
plexus mqtt-bridge -b mqtt.example.com -t "sensors/#"
```

## Examples

### Raspberry Pi + DHT22

```python
from plexus import Plexus
import adafruit_dht
import board

px = Plexus()
dht = adafruit_dht.DHT22(board.D4)

while True:
    px.send("temperature", dht.temperature)
    px.send("humidity", dht.humidity)
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

## Not Using Python?

Send data with any HTTP client:

```bash
curl -X POST https://app.plexus.company/api/ingest \
  -H "x-api-key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"points": [{"metric": "temperature", "value": 72.5}]}'
```

See [API.md](./API.md) for JavaScript, Go, and Arduino examples.

## License

Apache-2.0
