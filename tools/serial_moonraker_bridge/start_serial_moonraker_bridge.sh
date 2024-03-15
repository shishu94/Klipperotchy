#!/bin/sh
echo "baudrate=$1 serial=$2 moonraker=$3"
nohup /usr/bin/python3 <USER_DIR>/serial_moonraker_bridge.py --baudrate=$1 --serial=$2 --moonraker=$3 &
