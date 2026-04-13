# Changelog

## [Unreleased] - Stable device identity

The gateway is now authoritative for a device's `source_id`. The SDK sends a
locally-generated `install_id` in the auth frame; the gateway atomically
claims `(org, source_id)` and, if the desired name is already owned by a
different install, returns an auto-suffixed name (`drone-01` → `drone-01_2`
→ `drone-01_3`…) in the `authenticated` frame. The SDK adopts and persists
the assigned name so subsequent reconnects are stable.

This fixes the silent stream-merging that happened when cloned SD-card
images shared a hostname or when two operators picked the same name.

### Added

- `plexus.config.get_install_id()` — lazy per-installation UUID, persisted
  to `~/.plexus/config.json`. **Not** written by `setup.sh`: it's minted by
  the SDK on first run so pre-baked images get distinct IDs per boot.
- `plexus.config.set_source_id()` — persist the gateway-assigned name after
  auto-suffix resolution.
- `WebSocketTransport(install_id=..., on_source_id_assigned=...)` — the
  transport sends `install_id` in the `device_auth` frame and invokes the
  callback whenever the gateway returns a different `source_id` than
  requested.

### Changed

- `WebSocketTransport` now reads the `source_id` back from the
  `authenticated` frame and updates `self.source_id` in place if the gateway
  auto-suffixed. The rename is logged at INFO level on first occurrence.
- `Plexus` wires `install_id` into the transport and persists the assigned
  `source_id` to config on rename.
- `scripts/setup.sh` — `--name` is **required**. The hostname fallback is
  removed (it was the main source of cloned-image collisions). In a TTY the
  script prompts interactively; in non-TTY it exits with an error. Names are
  validated against `^[a-z0-9][a-z0-9_-]{1,62}$`. Stale `plexus start` /
  `plexus reset` hints were dropped.

### Wire-protocol (compatible)

- `device_auth` frame gains an optional `install_id` field. The gateway
  treats a missing `install_id` as legacy pass-through, so older SDKs and
  the C SDK continue to work unchanged.

## [0.3.0] - WebSocket transport

Adds a wire-compatible WebSocket transport matching the `plexus-c` SDK. WS is now the default; failed sends transparently fall back to `POST /ingest`.

### Added

- `plexus.WebSocketTransport` — connects to `/ws/device` on the gateway. Exchanges the same `device_auth` / `authenticated` / `telemetry` / `heartbeat` / `typed_command` / `command_result` frames as `plexus-c`.
- `Plexus(transport="ws" | "http")` — defaults to `"ws"`.
- `Plexus.on_command(name, handler, description=..., params=...)` — register command handlers; automatic `ack`, handler return becomes `result`, exceptions become `error`.
- `Plexus.close()` — stops the WebSocket thread.
- Runtime dep: `websocket-client>=1.7`.
- Tests: `tests/test_ws.py` (auth handshake, telemetry, command roundtrip, error paths).

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
