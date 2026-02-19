"""
VL53L0X Time-of-Flight distance sensor driver.

The VL53L0X measures distance using a laser (940nm VCSEL).
Range: 30mm to 2000mm with ±3% accuracy.
Communicates via I2C at default address 0x29.

Usage:
    from plexus.sensors import VL53L0X

    sensor = VL53L0X()
    for reading in sensor.read():
        print(f"{reading.metric}: {reading.value}")
"""

import time
from typing import List, Optional
from .base import BaseSensor, SensorReading

VL53L0X_ADDR = 0x29

# Key register addresses
REG_IDENTIFICATION_MODEL_ID = 0xC0
REG_SYSRANGE_START = 0x00
REG_RESULT_RANGE_STATUS = 0x14
REG_RESULT_RANGE_VAL = 0x1E  # 16-bit range value in mm


class VL53L0X(BaseSensor):
    """
    VL53L0X Time-of-Flight distance sensor driver.

    Provides:
    - distance_mm: Distance in millimeters (30-2000mm range)

    Note: This is a simplified driver using single-shot ranging.
    For production use with custom timing budgets, consider the
    official VL53L0X Python library.
    """

    name = "VL53L0X"
    description = "Time-of-Flight distance sensor (30-2000mm)"
    metrics = ["distance_mm"]
    i2c_addresses = [VL53L0X_ADDR]

    def __init__(
        self,
        address: int = VL53L0X_ADDR,
        bus: int = 1,
        sample_rate: float = 10.0,
        prefix: str = "",
        tags: Optional[dict] = None,
    ):
        super().__init__(sample_rate=sample_rate, prefix=prefix, tags=tags)
        self.address = address
        self.bus_num = bus
        self._bus = None

    def setup(self) -> None:
        try:
            from smbus2 import SMBus
        except ImportError:
            raise ImportError(
                "smbus2 is required for VL53L0X. Install with: pip install smbus2"
            )

        self._bus = SMBus(self.bus_num)

    def cleanup(self) -> None:
        if self._bus:
            self._bus.close()
            self._bus = None

    def read(self) -> List[SensorReading]:
        if self._bus is None:
            self.setup()

        # Start single-shot ranging
        self._bus.write_byte_data(self.address, REG_SYSRANGE_START, 0x01)

        # Wait for measurement to complete (up to 50ms)
        for _ in range(50):
            time.sleep(0.001)
            status = self._bus.read_byte_data(self.address, REG_RESULT_RANGE_STATUS)
            if status & 0x01:  # Device ready
                break

        # Read range result (2 bytes, big-endian)
        data = self._bus.read_i2c_block_data(self.address, REG_RESULT_RANGE_VAL, 2)
        distance_mm = (data[0] << 8) | data[1]

        # Filter out invalid readings (0 = no target, 8190+ = out of range)
        if distance_mm == 0 or distance_mm >= 8190:
            return []

        return [
            SensorReading("distance_mm", distance_mm),
        ]

    def is_available(self) -> bool:
        try:
            from smbus2 import SMBus

            bus = SMBus(self.bus_num)
            model_id = bus.read_byte_data(self.address, REG_IDENTIFICATION_MODEL_ID)
            bus.close()
            return model_id == 0xEE  # VL53L0X model ID
        except Exception:
            return False
