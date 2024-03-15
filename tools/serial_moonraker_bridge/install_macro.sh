#!/bin/sh

sed -i "s/<USER_DIR>/$PWD/g" serial_moonraker_bridge.cfg
cp serial_moonraker_bridge.cfg ~/printer_data/config

sed -i "1s/^/[include serial_moonraker_bridge.cfg]/g" /~/printer_data/config/printer.cfg




