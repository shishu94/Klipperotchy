#!/bin/sh
echo "baudrate=$1 serial=$2 moonraker=$3"
nohup /usb/bin/python3 serial_moonraker_bridge.py --baudrate=$1 --serial=$2 --moonraker=$3 &
