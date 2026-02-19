"""
Magnetometer sensor drivers: QMC5883L and HMC5883L.

These sensors measure magnetic field strength in 3 axes.
Used for compass heading, metal detection, and orientation.

QMC5883L: Common Chinese replacement, address 0x0D
HMC5883L: Original Honeywell sensor, address 0x1E

Usage:
    from plexus.sensors import QMC5883L, HMC5883L

    mag = QMC5883L()
    for reading in mag.read():
        print(f"{reading.metric}: {reading.value}")
"""

import time
from typing import List, Optional
from .base import BaseSensor, SensorReading

# ─── QMC5883L ────────────────────────────────────────────────────────────────

QMC5883L_ADDR = 0x0D

QMC_REG_DATA = 0x00       # X LSB, X MSB, Y LSB, Y MSB, Z LSB, Z MSB
QMC_REG_STATUS = 0x06
QMC_REG_CONFIG1 = 0x09    # Mode, ODR, Range, OSR
QMC_REG_CONFIG2 = 0x0A    # Soft reset, pointer roll-over
QMC_REG_CHIP_ID = 0x0D

# Config1: Continuous mode, 200Hz ODR, 8 Gauss range, 512 oversampling
QMC_CONFIG1_DEFAULT = 0x1D
# Config2: Pointer roll-over enabled
QMC_CONFIG2_DEFAULT = 0x40


class QMC5883L(BaseSensor):
    """
    QMC5883L 3-axis magnetometer driver.

    Provides:
    - mag_x, mag_y, mag_z: Magnetic field in microtesla (µT)
    """

    name = "QMC5883L"
    description = "3-axis magnetometer (compass)"
    metrics = ["mag_x", "mag_y", "mag_z"]
    i2c_addresses = [QMC5883L_ADDR]

    def __init__(
        self,
        address: int = QMC5883L_ADDR,
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
                "smbus2 is required for QMC5883L. Install with: pip install smbus2"
            )

        self._bus = SMBus(self.bus_num)

        # Soft reset
        self._bus.write_byte_data(self.address, QMC_REG_CONFIG2, 0x80)
        time.sleep(0.01)

        # Configure: continuous mode, 200Hz, 8G range, 512x oversampling
        self._bus.write_byte_data(self.address, QMC_REG_CONFIG1, QMC_CONFIG1_DEFAULT)
        self._bus.write_byte_data(self.address, QMC_REG_CONFIG2, QMC_CONFIG2_DEFAULT)
        time.sleep(0.01)

    def cleanup(self) -> None:
        if self._bus:
            self._bus.close()
            self._bus = None

    def read(self) -> List[SensorReading]:
        if self._bus is None:
            self.setup()

        # Check data ready
        status = self._bus.read_byte_data(self.address, QMC_REG_STATUS)
        if not (status & 0x01):
            return []  # Data not ready

        # Read 6 bytes: X_LSB, X_MSB, Y_LSB, Y_MSB, Z_LSB, Z_MSB
        data = self._bus.read_i2c_block_data(self.address, QMC_REG_DATA, 6)

        x = (data[1] << 8) | data[0]
        y = (data[3] << 8) | data[2]
        z = (data[5] << 8) | data[4]

        # Convert to signed
        if x > 32767: x -= 65536
        if y > 32767: y -= 65536
        if z > 32767: z -= 65536

        # At 8 Gauss range: 3000 LSB/Gauss, 1 Gauss = 100 µT
        # So LSB = 100/3000 µT ≈ 0.0333 µT
        scale = 100.0 / 3000.0

        return [
            SensorReading("mag_x", round(x * scale, 2)),
            SensorReading("mag_y", round(y * scale, 2)),
            SensorReading("mag_z", round(z * scale, 2)),
        ]

    def is_available(self) -> bool:
        try:
            from smbus2 import SMBus

            bus = SMBus(self.bus_num)
            chip_id = bus.read_byte_data(self.address, QMC_REG_CHIP_ID)
            bus.close()
            return chip_id == 0xFF  # QMC5883L returns 0xFF for chip ID register
        except Exception:
            return False


# ─── HMC5883L ────────────────────────────────────────────────────────────────

HMC5883L_ADDR = 0x1E

HMC_REG_CONFIG_A = 0x00
HMC_REG_CONFIG_B = 0x01
HMC_REG_MODE = 0x02
HMC_REG_DATA = 0x03  # X MSB, X LSB, Z MSB, Z LSB, Y MSB, Y LSB
HMC_REG_ID_A = 0x0A

# Config A: 8 samples averaged, 15Hz output, normal measurement
HMC_CONFIG_A_DEFAULT = 0x70
# Config B: Gain = 1.3 Gauss (1090 LSB/Gauss)
HMC_CONFIG_B_DEFAULT = 0x20
# Mode: Continuous measurement
HMC_MODE_CONTINUOUS = 0x00


class HMC5883L(BaseSensor):
    """
    HMC5883L 3-axis magnetometer driver.

    Provides:
    - mag_x, mag_y, mag_z: Magnetic field in microtesla (µT)
    """

    name = "HMC5883L"
    description = "3-axis magnetometer (compass)"
    metrics = ["mag_x", "mag_y", "mag_z"]
    i2c_addresses = [HMC5883L_ADDR]

    def __init__(
        self,
        address: int = HMC5883L_ADDR,
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
                "smbus2 is required for HMC5883L. Install with: pip install smbus2"
            )

        self._bus = SMBus(self.bus_num)

        self._bus.write_byte_data(self.address, HMC_REG_CONFIG_A, HMC_CONFIG_A_DEFAULT)
        self._bus.write_byte_data(self.address, HMC_REG_CONFIG_B, HMC_CONFIG_B_DEFAULT)
        self._bus.write_byte_data(self.address, HMC_REG_MODE, HMC_MODE_CONTINUOUS)
        time.sleep(0.01)

    def cleanup(self) -> None:
        if self._bus:
            self._bus.close()
            self._bus = None

    def read(self) -> List[SensorReading]:
        if self._bus is None:
            self.setup()

        # Read 6 bytes: X MSB, X LSB, Z MSB, Z LSB, Y MSB, Y LSB
        # Note: HMC5883L register order is X, Z, Y (not X, Y, Z)
        data = self._bus.read_i2c_block_data(self.address, HMC_REG_DATA, 6)

        x = (data[0] << 8) | data[1]
        z = (data[2] << 8) | data[3]
        y = (data[4] << 8) | data[5]

        # Convert to signed
        if x > 32767: x -= 65536
        if y > 32767: y -= 65536
        if z > 32767: z -= 65536

        # At 1.3 Gauss gain: 1090 LSB/Gauss, 1 Gauss = 100 µT
        scale = 100.0 / 1090.0

        return [
            SensorReading("mag_x", round(x * scale, 2)),
            SensorReading("mag_y", round(y * scale, 2)),
            SensorReading("mag_z", round(z * scale, 2)),
        ]

    def is_available(self) -> bool:
        try:
            from smbus2 import SMBus

            bus = SMBus(self.bus_num)
            # Read identification registers (should be 'H', '4', '3')
            id_a = bus.read_byte_data(self.address, HMC_REG_ID_A)
            id_b = bus.read_byte_data(self.address, HMC_REG_ID_A + 1)
            id_c = bus.read_byte_data(self.address, HMC_REG_ID_A + 2)
            bus.close()
            return id_a == 0x48 and id_b == 0x34 and id_c == 0x33  # "H43"
        except Exception:
            return False
