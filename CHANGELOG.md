# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.13.0] - 2026-03-31

### Removed

- Auto-dashboard creation on first run — dashboards are now created by users in the UI
- `_build_panels()` and `_launch_auto_dashboard()` internals (~230 lines)
- `dashboard_id` from agent config

### Changed

- Status block now shows app URL instead of auto-generated dashboard link

## [0.10.0] - 2026-03-26

### Changed

- **Simplified CLI to two commands:** `plexus start` and `plexus reset`
- Removed `plexus login`, `plexus scan`, `plexus status`, `plexus doctor`, `plexus add`
- Stripped `plexus start` from 13 flags to 2: `--key` and `--device-id`
- Auth now happens inline during `plexus start` (terminal sign-up/sign-in, no browser)
- Headless mode auto-detected from TTY instead of requiring `--headless` flag
- `plexus login` replaced by inline `_terminal_auth()` in `plexus start`

### Added

- `plexus reset` command — clears config with confirmation, clean slate
- macOS install docs: `brew install pipx && pipx install plexus-agent`
- Setup script now shows what was installed and how to uninstall
- Add-device modal shows transparency about what the curl script does

### Fixed

- All internal error messages updated from `plexus login` to `plexus start`
- PEP 668 install guidance in README for macOS and modern Debian/Ubuntu

## [0.9.6] - 2026-03-24

### Added

- Auto-scaling charts, side-by-side cards, scale labels in TUI
- Braille charts, gradient colors, metric cards, logo shine in TUI
- ONCE-inspired TUI layout with logo, sparklines, thin-bordered panels
- Full-screen alternate buffer TUI with clean layout
- TUI-first experience with startup wizard and keyboard shortcuts
- Live metric readout in terminal while streaming
- Spinner for dashboard generation, clickable URL

### Fixed

- TUI shows no data and garbles setup output
- Signal handler crash when TUI runs connector in background thread
- Send actual sensor readings on connect to seed device schema

## [0.7.4] - 2026-03-08

### Fixed

- Always create dashboard even with no sensors

## [0.7.3] - 2026-03-07

### Changed

- Single-step signup via Backend SDK, dropped OTP flow

## [0.7.2] - 2026-03-06

### Added

- Arrow-key selector for signup/signin flow

## [0.7.1] - 2026-03-05

### Fixed

- Added `__main__.py` so `python3 -m plexus` works

## [0.7.0] - 2026-03-04

### Added

- Terminal-first onboarding — signup/signin without leaving the CLI

## [0.6.1] - 2026-02-28

### Changed

- Removed self-host docker config from agent SDK repo

## [0.6.0] - 2026-02-26

### Added

- BLE relay adapter for gateway devices
- Adapter lifecycle management

## [0.5.9] - 2026-02-25

### Fixed

- Update links

## [0.5.8] - 2026-02-24

### Changed

- Version bump

## [0.5.7] - 2026-02-23

### Changed

- Version bump

## [0.5.6] - 2026-02-22

### Changed

- Version bump

## [0.5.5] - 2026-02-21

### Changed

- Standardized CI/CD workflows

## [0.5.4] - 2026-02-20

### Fixed

- Resolved 39 ruff lint errors and buffer size bug

## [0.5.3] - 2026-02-19

### Added

- Max reconnection attempts to WebSocket connector
- PX4 baud rate documentation in MAVLink adapter
- `on_overflow` callback to buffer backends

### Fixed

- Camera detection failures now logged instead of silently swallowed
- Differentiate I2C permission errors from missing devices
- Secure config directory permissions to 0o700
- Harden GPS NMEA parsing against malformed input

### Changed

- Concurrent sensor reads with per-sensor timeouts

## [0.5.0] - 2026-02-17

### Added

- MAVLink adapter for drone telemetry
- Source ID update support

[0.13.0]: https://github.com/plexus-oss/agent/releases/tag/v0.13.0
[0.10.0]: https://github.com/plexus-oss/agent/releases/tag/v0.10.0
[0.9.6]: https://github.com/plexus-oss/agent/releases/tag/v0.9.6
[0.7.4]: https://github.com/plexus-oss/agent/releases/tag/v0.7.4
[0.7.3]: https://github.com/plexus-oss/agent/releases/tag/v0.7.3
[0.7.2]: https://github.com/plexus-oss/agent/releases/tag/v0.7.2
[0.7.1]: https://github.com/plexus-oss/agent/releases/tag/v0.7.1
[0.7.0]: https://github.com/plexus-oss/agent/releases/tag/v0.7.0
[0.6.1]: https://github.com/plexus-oss/agent/releases/tag/v0.6.1
[0.6.0]: https://github.com/plexus-oss/agent/releases/tag/v0.6.0
[0.5.9]: https://github.com/plexus-oss/agent/releases/tag/v0.5.9
[0.5.8]: https://github.com/plexus-oss/agent/releases/tag/v0.5.8
[0.5.7]: https://github.com/plexus-oss/agent/releases/tag/v0.5.7
[0.5.6]: https://github.com/plexus-oss/agent/releases/tag/v0.5.6
[0.5.5]: https://github.com/plexus-oss/agent/releases/tag/v0.5.5
[0.5.4]: https://github.com/plexus-oss/agent/releases/tag/v0.5.4
[0.5.3]: https://github.com/plexus-oss/agent/releases/tag/v0.5.3
[0.5.0]: https://github.com/plexus-oss/agent/releases/tag/v0.5.0
