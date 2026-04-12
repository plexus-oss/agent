# Changelog

## [0.2.0] - Thin SDK rewrite

Breaking. `plexus-python` is now just the thin client — no agent, adapters, sensors, CLI, or TUI. The package is 886 lines with one runtime dependency (`requests`). Protocol integrations (MAVLink, CAN, MQTT, Modbus, OPC-UA, BLE, I2C sensors) now live as standalone recipes in `examples/`, using the upstream library directly (`pymavlink`, `python-can`, `paho-mqtt`, etc.) plus `px.send()`.

### Added

- 5 runnable example scripts: `basic.py`, `mavlink.py`, `can.py`, `mqtt.py`, `i2c_bme280.py`

### Removed

- `plexus/adapters/` (MAVLink, CAN, MQTT, Modbus, OPC-UA, BLE, Serial — use the upstream lib directly)
- `plexus/sensors/` (I2C drivers + auto-detect — use Adafruit CircuitPython or smbus2 directly)
- `plexus/cameras/` (frame upload — out of scope)
- `plexus/cli.py`, `plexus/connector.py`, `plexus/streaming.py`, `plexus/detect.py`, `plexus/tui.py`, `plexus/deps.py`
- `plexus` console script, `python -m plexus`
- Extras: `[sensors]`, `[system]`, `[tui]`, `[mqtt]`, `[can]`, `[mavlink]`, `[modbus]`, `[opcua]`, `[ble]`, `[serial]`, `[ros]`, `[camera]`, `[picamera]`, `[all]`
- Runtime deps: `click`, `websockets`

### Changed

- Default ingest endpoint points directly at the Plexus gateway (`https://plexus-gateway.fly.dev/ingest`), not the Next.js app proxy
- Client raises `ValueError` clearly when no API key is available, instead of invoking a login flow

## [0.1.0] - Initial release

- `Plexus` thin client for HTTP ingest
- `plexus start` daemon with WebSocket streaming
- Protocol adapters: MAVLink, CAN, MQTT, Modbus, OPC-UA, Serial, BLE
- I2C sensor auto-detection and drivers
- Store-and-forward buffering (SQLite)
