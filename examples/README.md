# Examples

Each script is standalone — copy into your project, adjust the `source_id`, and run. Every example takes `PLEXUS_API_KEY` from the environment.

| Script | What it shows | Extra deps |
| --- | --- | --- |
| `basic.py` | The 3-line starter | none |
| `mavlink.py` | Drone / autopilot telemetry | `pymavlink` |
| `can.py` | Vehicle CAN bus (with optional DBC decode) | `python-can`, `cantools` |
| `mqtt.py` | MQTT broker → Plexus bridge | `paho-mqtt` |
| `i2c_bme280.py` | Raspberry Pi environmental sensor | `adafruit-circuitpython-bme280` |

The pattern is always the same: use whatever library you'd use anyway, then call `px.send(metric, value)`. Plexus stays out of your decode path.
