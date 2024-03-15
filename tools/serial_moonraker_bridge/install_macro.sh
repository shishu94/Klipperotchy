#!/bin/sh

WORK_DIR=$PWD
sed -i "s\<USER_DIR>\$WORK_DIR\g" serial_moonraker_bridge.cfg
cp serial_moonraker_bridge.cfg ~/printer_data/config

sed -i '1s\^\[include serial_moonraker_bridge.cfg]' /~/printer_data/config/




