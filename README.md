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

## Plug & Play Sensors (Raspberry Pi)

Auto-detect and stream all connected I2C sensors:

```bash
pip install plexus-agent[sensors]
plexus login
plexus scan   # See what's connected
plexus run    # Stream everything
```

Supported sensors:

| Sensor  | Type        | Metrics                                         |
| ------- | ----------- | ----------------------------------------------- |
| MPU6050 | 6-axis IMU  | accel_x/y/z, gyro_x/y/z                         |
| MPU9250 | 9-axis IMU  | accel_x/y/z, gyro_x/y/z                         |
| BME280  | Environment | temperature, humidity, pressure                 |

Or use the Python API:

```python
from plexus import Plexus
from plexus.sensors import auto_sensors

hub = auto_sensors()  # Auto-detect all sensors
hub.run(Plexus())     # Stream to Plexus
```

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

### Supported Value Types

```python
px.send("temperature", 72.5)                      # number
px.send("status", "running")                      # string
px.send("armed", True)                            # boolean
px.send("position", {"x": 1.0, "y": 2.0})         # object
px.send("spectrum", [0.1, 0.5, 0.8, 0.3])         # array
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

| Command                        | Description              |
| ------------------------------ | ------------------------ |
| `plexus login`                 | Sign in via browser      |
| `plexus send <metric> <value>` | Send a value             |
| `plexus stream <metric>`       | Stream from stdin        |
| `plexus scan`                  | Detect connected sensors |
| `plexus sensors`               | List supported sensors   |
| `plexus run`                   | Stream all sensors       |
| `plexus import <file>`         | Import CSV/TSV file      |
| `plexus status`                | Check connection         |

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

### Raspberry Pi + IMU (MPU6050)

```python
from plexus import Plexus
from plexus.sensors import SensorHub, MPU6050

hub = SensorHub()
hub.add(MPU6050(sample_rate=100))  # 100 Hz
hub.run(Plexus())
```

### Raspberry Pi + Environment (BME280)

```python
from plexus import Plexus
from plexus.sensors import SensorHub, BME280

hub = SensorHub()
hub.add(BME280(sample_rate=1))  # 1 Hz
hub.run(Plexus())
```

### Custom Sensor

```python
from plexus.sensors import BaseSensor, SensorReading

class VoltageSensor(BaseSensor):
    name = "Voltage"
    metrics = ["voltage"]

    def read(self):
        return [SensorReading("voltage", read_adc() * 3.3)]

hub = SensorHub()
hub.add(VoltageSensor(sample_rate=10))
hub.run(Plexus())
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
  -d '{"points": [{"metric": "temperature", "value": 72.5, "device_id": "pi-001"}]}'
```

See [API.md](./API.md) for JavaScript, Go, and Arduino examples.

## Installation Options

```bash
pip install plexus-agent              # Core SDK
pip install plexus-agent[sensors]     # + I2C sensor drivers (smbus2)
pip install plexus-agent[mqtt]        # + MQTT bridge
pip install plexus-agent[ros]         # + ROS bag import
pip install plexus-agent[all]         # Everything
```

## License

Apache-2.0
