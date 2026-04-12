"""
Read a BME280 over I2C on a Raspberry Pi and stream to Plexus.

Prereq:
    pip install plexus-python adafruit-circuitpython-bme280
    sudo usermod -aG i2c $USER && reboot    # (once)
    export PLEXUS_API_KEY=plx_xxx

Run:
    python i2c_bme280.py
"""

import time

import board
import busio
from adafruit_bme280 import basic as adafruit_bme280

from plexus import Plexus

i2c = busio.I2C(board.SCL, board.SDA)
bme = adafruit_bme280.Adafruit_BME280_I2C(i2c)

px = Plexus(source_id="pi-lab-01")

while True:
    px.send("temperature", bme.temperature)
    px.send("humidity", bme.relative_humidity)
    px.send("pressure", bme.pressure)
    time.sleep(2)
