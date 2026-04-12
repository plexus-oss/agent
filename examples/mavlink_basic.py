#!/usr/bin/env python3
"""
MAVLink Basic Example

This example demonstrates how to use the MAVLinkAdapter to read telemetry
from a MAVLink-speaking vehicle and send it to Plexus.

Setup (SITL):
    # Install ArduPilot SITL for testing without hardware
    # https://ardupilot.org/dev/docs/setting-up-sitl-on-linux.html
    sim_vehicle.py -v ArduCopter

    # The adapter will connect to SITL on UDP port 14550

Requirements:
    pip install plexus-python[mavlink]
"""

import time
import logging
from plexus import Plexus

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """Basic MAVLink example — stream all telemetry."""
    from plexus.adapters import MAVLinkAdapter

    # Create Plexus client
    plexus = Plexus(
        api_key="plx_your_api_key",
        source_id="drone-001",
    )

    # Create MAVLink adapter (listens for UDP from SITL or GCS)
    adapter = MAVLinkAdapter(
        connection_string="udpin:0.0.0.0:14550",
        emit_decoded=True,
        emit_raw=False,
    )

    try:
        logger.info("Connecting to MAVLink vehicle...")
        adapter.connect()
        logger.info(f"Connected! Vehicle sysid={adapter._vehicle_sysid}")

        while True:
            metrics = adapter.poll()

            for metric in metrics:
                logger.info(f"{metric.name} = {metric.value}")
                plexus.send(metric.name, metric.value, tags=metric.tags)

            time.sleep(0.01)

    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        adapter.disconnect()
        plexus.close()


def main_filtered():
    """MAVLink example with message filtering — only attitude and GPS."""
    from plexus.adapters import MAVLinkAdapter

    plexus = Plexus(
        api_key="plx_your_api_key",
        source_id="drone-001",
    )

    adapter = MAVLinkAdapter(
        connection_string="udpin:0.0.0.0:14550",
        include_messages=["ATTITUDE", "GPS_RAW_INT", "VFR_HUD"],
    )

    try:
        adapter.connect()
        logger.info("Streaming attitude, GPS, and HUD data only...")

        while True:
            for metric in adapter.poll():
                logger.info(f"{metric.name} = {metric.value}")
                plexus.send(metric.name, metric.value, tags=metric.tags)

            time.sleep(0.01)

    except KeyboardInterrupt:
        pass
    finally:
        adapter.disconnect()
        plexus.close()


def main_commands():
    """Example of sending commands to a vehicle."""
    from plexus.adapters import MAVLinkAdapter

    adapter = MAVLinkAdapter(
        connection_string="udpin:0.0.0.0:14550",
    )

    try:
        adapter.connect()

        # Set flight mode
        adapter.set_mode("GUIDED")
        logger.info("Set mode to GUIDED")

        # Arm the vehicle
        adapter.arm()
        logger.info("Armed")

        time.sleep(5)

        # Disarm the vehicle
        adapter.disarm()
        logger.info("Disarmed")

    finally:
        adapter.disconnect()


def main_serial():
    """MAVLink example over serial (direct flight controller connection)."""
    from plexus.adapters import MAVLinkAdapter

    adapter = MAVLinkAdapter(
        connection_string="/dev/ttyACM0",
        baud=57600,
    )

    try:
        adapter.connect()
        logger.info("Connected to flight controller over serial")

        while True:
            for metric in adapter.poll():
                logger.info(f"{metric.name} = {metric.value}")

            time.sleep(0.01)

    except KeyboardInterrupt:
        pass
    finally:
        adapter.disconnect()


if __name__ == "__main__":
    # Choose which example to run:
    main()
    # main_filtered()
    # main_commands()
    # main_serial()
