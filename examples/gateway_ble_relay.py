#!/usr/bin/env python3
"""
Gateway BLE Relay Example

Demonstrates the gateway pattern: a Linux host (e.g., Raspberry Pi) scans
for BLE sensors and relays their data to Plexus cloud. This lets devices
without WiFi/internet reach Plexus through a local gateway.

Setup:
    pip install plexus-agent[ble]

Environment variables:
    PLEXUS_API_KEY      - Your Plexus API key (required)
    PLEXUS_SOURCE_ID    - Source identifier (default: "ble-gateway")
    BLE_SERVICE_UUIDS   - Comma-separated BLE service UUIDs to scan for
                          (default: "181A" = Environmental Sensing)
    BLE_NAME_FILTER     - Only relay devices whose name contains this string
    BLE_SCAN_DURATION   - Scan duration in seconds (default: 5)
    POLL_INTERVAL       - Seconds between poll cycles (default: 10)

Usage:
    export PLEXUS_API_KEY="plx_your_key_here"
    python gateway_ble_relay.py
"""

import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ble-gateway")

try:
    from plexus import Plexus
    from plexus.adapters import BLERelayAdapter
except ImportError:
    print("Install plexus-agent with BLE support:")
    print("  pip install plexus-agent[ble]")
    sys.exit(1)


def main():
    api_key = os.environ.get("PLEXUS_API_KEY")
    if not api_key:
        print("Set PLEXUS_API_KEY environment variable")
        sys.exit(1)

    source_id = os.environ.get("PLEXUS_SOURCE_ID", "ble-gateway")
    service_uuids = os.environ.get("BLE_SERVICE_UUIDS", "181A").split(",")
    name_filter = os.environ.get("BLE_NAME_FILTER")
    scan_duration = float(os.environ.get("BLE_SCAN_DURATION", "5"))
    poll_interval = float(os.environ.get("POLL_INTERVAL", "10"))

    # Initialize Plexus client
    px = Plexus(api_key=api_key, source_id=source_id)

    # Initialize BLE adapter
    adapter = BLERelayAdapter(
        service_uuids=[u.strip() for u in service_uuids],
        scan_duration=scan_duration,
        name_filter=name_filter,
        source_prefix=source_id,
    )
    adapter.connect()

    logger.info(
        "Gateway started — scanning for BLE devices (UUIDs: %s, filter: %s)",
        service_uuids,
        name_filter or "none",
    )

    try:
        while True:
            metrics = adapter.poll()
            for m in metrics:
                px.send(m.name, m.value, timestamp=m.timestamp)
                logger.info("  %s = %s", m.name, m.value)

            if metrics:
                px.flush()
                logger.info("Relayed %d metrics to Plexus", len(metrics))
            else:
                logger.debug("No BLE devices found this cycle")

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        adapter.disconnect()


if __name__ == "__main__":
    main()
