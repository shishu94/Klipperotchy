#!/bin/sh

sed -i "s|<USER_DIR>|$PWD|g" serial_moonraker_bridge.cfg
sed -i "s|<USER_DIR>|$PWD|g" start_serial_moonraker_bridge.sh
cp serial_moonraker_bridge.cfg ~/printer_data/config

sed -i "1s|^|[include serial_moonraker_bridge.cfg]\n|g" ~/printer_data/config/printer.cfg




