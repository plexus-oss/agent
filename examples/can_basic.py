#!/usr/bin/env python3
"""
CAN Bus Basic Example

This example demonstrates how to use the CANAdapter to read CAN bus data
and send it to Plexus.

Setup (Linux):
    # Create a virtual CAN interface for testing
    sudo modprobe vcan
    sudo ip link add dev vcan0 type vcan
    sudo ip link set up vcan0

    # Send test frames (in another terminal)
    cansend vcan0 123#DEADBEEF
    cansend vcan0 456#01020304050607

Requirements:
    pip install plexus-agent[can]
"""

import time
import logging
from plexus import Plexus

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """Basic CAN bus example without DBC decoding."""
    from plexus.adapters import CANAdapter

    # Create Plexus client
    plexus = Plexus(
        api_key="plx_your_api_key",
        source_id="can-gateway-001",
    )

    # Create CAN adapter
    adapter = CANAdapter(
        interface="socketcan",  # Linux SocketCAN
        channel="vcan0",        # Virtual CAN for testing (use "can0" for real hardware)
        bitrate=500000,
        emit_raw=True,          # Emit raw frame metrics
        emit_decoded=False,     # No DBC file for this example
    )

    try:
        # Connect to CAN bus
        logger.info("Connecting to CAN bus...")
        adapter.connect()
        logger.info("Connected! Listening for CAN frames...")

        # Read and send frames
        while True:
            metrics = adapter.poll()

            for metric in metrics:
                logger.info(f"Received: {metric.name} = {metric.value}")
                plexus.send(metric.name, metric.value, tags=metric.tags)

            # Small delay to avoid busy-waiting
            time.sleep(0.001)

    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        adapter.disconnect()
        plexus.close()


def main_with_dbc():
    """CAN bus example with DBC signal decoding."""
    from plexus.adapters import CANAdapter

    # Create Plexus client
    plexus = Plexus(
        api_key="plx_your_api_key",
        source_id="vehicle-001",
    )

    # Create CAN adapter with DBC file
    adapter = CANAdapter(
        interface="socketcan",
        channel="can0",
        bitrate=500000,
        dbc_path="/path/to/your/vehicle.dbc",  # Your DBC file
        emit_raw=True,       # Also emit raw frames
        emit_decoded=True,   # Emit decoded signals
    )

    try:
        adapter.connect()
        logger.info("Connected with DBC decoding enabled")

        while True:
            metrics = adapter.poll()

            for metric in metrics:
                # Raw frames will be like: can.raw.0x123 = "DEADBEEF"
                # Decoded signals will be like: engine_rpm = 2500
                logger.info(f"{metric.name} = {metric.value} (tags: {metric.tags})")
                plexus.send(metric.name, metric.value, tags=metric.tags)

            time.sleep(0.001)

    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        adapter.disconnect()
        plexus.close()


def main_with_filters():
    """CAN bus example with message filtering."""
    from plexus.adapters import CANAdapter

    # Create adapter with filters - only receive specific CAN IDs
    adapter = CANAdapter(
        interface="socketcan",
        channel="can0",
        filters=[
            {"can_id": 0x123, "can_mask": 0x7FF},  # Exact match for 0x123
            {"can_id": 0x200, "can_mask": 0x700},  # Match 0x200-0x2FF
        ],
    )

    try:
        adapter.connect()
        logger.info("Connected with filters: only receiving 0x123 and 0x2XX")

        while True:
            metrics = adapter.poll()
            for metric in metrics:
                logger.info(f"{metric.name} = {metric.value}")

    except KeyboardInterrupt:
        pass
    finally:
        adapter.disconnect()


def main_send_frames():
    """Example of sending CAN frames."""
    from plexus.adapters import CANAdapter

    adapter = CANAdapter(
        interface="socketcan",
        channel="vcan0",
    )

    try:
        adapter.connect()

        # Send raw frame
        adapter.send(0x100, bytes([0x01, 0x02, 0x03, 0x04]))
        logger.info("Sent frame 0x100")

        # If you have a DBC file loaded, you can send signals by name
        # adapter.send_signal("EngineCommand", {"throttle": 50, "brake": 0})

    finally:
        adapter.disconnect()


if __name__ == "__main__":
    # Choose which example to run:
    main()
    # main_with_dbc()
    # main_with_filters()
    # main_send_frames()
