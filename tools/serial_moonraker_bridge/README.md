# What is this 
Python3 application that allows to bridge source of GCODE from a serial interface and feed them directly into [Moonraker](https://github.com/Arksine/moonraker) (in other words a [Klipper](https://www.klipper3d.org/) printer) via the network.

# What is it for
This tool is particularly useful to control Klipper via external softwares or devices that only control 3d printers, lasers, CNCs via a physical serial port. 

A few examples:
- [Mosaic Palette 3 (Pro)](https://www.mosaicmfg.com/products/palette-3-pro)
- [Lightburn](https://lightburnsoftware.com/)

# Current state
It is in very early stage, but it will get an install script and run as a service to make the setup as easy as possible.
As of now the script is fully functional, but it doesn't actively check or recovers from failures.
In practice it has been very reliable with my Palette 3 Pro in connected mode.

# Setup
We will use the Mosaic Palette 3 Pro (P3P) as an example to set it up. Also we will use a Raspberry Pi 4 as a reference. 

**Note:** I tested this as is on a few different devices. Some work very reliably others don't. Here is a list of what I tested so far. This means that the devices runs Klipper and the bridge code at the same time. This is what I call local mode.

Work well:
- Raspberry Pi 4 
- BTT CB1
- Makerbase Klipper integrated boards (tested on a QIDI X plus 3)

Crashed Klipper due to CPU overload:
- Rapsberry Pi Zero 2w

For the low resource klipper based printers like the Pi Zero 2w, the bridge can run on a separate device, as long as it can reach the moonraker instance of that printer over the network. For these test, I ran complete prints with the P3P connected to my linux laptop which was serving as a bridge to my Pi Zero 2w based Klipper printer. This would be the remote mode. 

## Requirements

### Hardware
The Palette 3 Pro use a Slave USB serial connection. The problem is that Klipper printers don't have a slave serial port via USB. So we need to provide one. There are 2 ways:

#### Raspberry Pi based 
The raspberry pi can provide a UART serial connection. By Using a UART serial (TTL) to USB adapter, the raspberry can expose a slave serial USB that is compatible with the P3P.

***Needs 1 serial USB adapter***

#### Generic case
By using a pair of serial (TTL) to USB adapter wired together, any host device can expose a slave serial USB compatible with the P3P.

***Needs 2 serial USB adapter***

No strong preference over the serial to USB converter used as long as they support at least 115200 baudrate.

Few examples:
- https://www.amazon.com/s?k=ft232&crid=2WT7HWG23LD7S&sprefix=ft232%2Caps%2C180&ref=nb_sb_noss_1
- [FT232RL (I tested it)](https://www.amazon.com/HiLetgo-FT232RL-Converter-Adapter-Breakout/dp/B00IJXZQ7C/ref=sr_1_1?crid=2WT7HWG23LD7S&dib=eyJ2IjoiMSJ9.cQHozpO05gscv2XRMBAoT3k5X_6ZdSsOw_pNfVqFXgmo8RtUlxlhNs27epYuUfJg5-0-pynSAyIa4fCiXkp0Qb1UCXdVqsLLn54ACr3zAIYLDmGlFvvJPuvCro3WfaIx8DGmSOX2iNtLxqw_OmYmsBr-PZ7O8v-R5Q4jSTSQ7CmpBWhxljG6CNpWatHijTQ3kLHA2cXhsu9Yfyy-fIvLTjN5dFW3cMkK0NfI2d09pLU.M559cIx9AxRVfG3bRpkyEMCHlng6jXVIij1CxHx-KZ0&dib_tag=se&keywords=ft232&qid=1710103861&sprefix=ft232%2Caps%2C180&sr=8-1)
- [CH340G (I tested it)](https://www.amazon.com/HiLetgo-Module-Microcontroller-Download-Serial/dp/B00LZV1G6K/ref=sr_1_1?crid=17C1UWPLWZ5BM&dib=eyJ2IjoiMSJ9.zvmz4Fksq6F9d55nDpLfSdXPCzH5sDloljArNZSjLIdCGVAmoGI9d38gmRl5ICH8fHX3JiHTaJzRAUHsR1Os9NcG6wviaxqPp4O8IDFFZrKkBF9ygGyqzAz_s02UnnUsRg7Xj5t6ydB_05UnuxfaLRsZRiMRgGrZ8WMZQdrDAAAMq0RNS_l8PVN8VydCiKX9tzDxQx-SAAVL56D5Cayc9oN6l78d_Ym0wuCF3tZwrMc.vNYpfjWQ5J2yRGlyCKqTFmIYZzAHmIO1yWnOMSQlIfU&dib_tag=se&keywords=CH340g&qid=1710106717&sprefix=ch340g%2Caps%2C204&sr=8-1)

For more flexibility (and the UART on the RPi can be annoying) I suggest to immediatelly go for the double serial adapter. And they are relatively cheap. 

Note: I recommend at least one with a USB connection via micro USB.
2 with a USB A connector work in practice, but non shielded long serial cables tend to degrade signal quality with length. If you want to go that route, consider a USB extension on one side rather than using long wires between the serial modules.


##### How to connect 2 serial USB converters
Each serial converter provides at least:
- Ground
- VCC
- TX
- RX

In the product above VCC can be selected via a jumper, either 3.3v or 5v. Just keep in mind that to connect 2 serial converters together they must be on the same working voltage. 
Once both are correctly configured with an identical voltage, we can wire them in the following way:

![Look into the SerialConnect.png](SerialConnect.png)
Do not connect VCC, not required here.

Once this double USB A ended cable is done you are ready for the next step.


### Software
The script has 2 notable dependencies:
- [PySerial](https://pyserial.readthedocs.io/en/latest/index.html)
- [Python Websockets](https://websockets.readthedocs.io/en/stable/)

Follow the appropriate ways of installing these 2 libraries if they are not available on your bridge host system.

The remaining dependencies should be included in all the major installs of Python3

If you intend to run this in Local mode (on the Klipper printer) you will need (in most cases) need to ssh into the printer while is powered and start the script manually.

If you intend to run this in Remote mode (on a device on the same network) no additional requirements.

#### Running the script
The script has 3 parameters:
- baudrate: The Baudrate of the serial connection. (default 250000)
- serial: The identifier of the serial interface used. (default /dev/ttyUSB0)
- moonraker: The [ip:port] of the Moonraker instance to target. (default localhost:7125)
- log: The log level in case you are interested to see what is going on.

Note: ensure that one side of the double cable is connected to the host (not needed if using the RPi Uart).

To run the script open a terminal on the bridge host and run,

with the double USB A cable:
`python3 serial_moonraker_bridge.py --baudrate=XXXXXX --moonraker=[IP:PORT] --serial=/dev/ttyUSBX &`

with the RPi uart in local mode with logging enabled:
`python3 serial_moonraker_bridge.py --baudrate=115200 --moonraker=localhost:7125 --serial=/dev/ttyAMA0 --log=INFO &`

This will start the bridge and keep it running. Of course replace the Xs with the appropriate values.

#### Connecting the Palette 3 (Pro)
Once the step above is done, connect the remaining USB A connector, and plug it on the Palette 3.
Go to the P3 and try to connect to a printer. Use the same baudrate as specified to start the bridge code.
If everything went well, the palette should display a connected state in a few seconds.

## Setup the slicer
Configure canvas as you would configure any other slicer with the Klipper printer. Especially the Start and End job.

## Bonus Fake job
This includes a FakePrint.gcode that you can upload to klipper. One can start this in parallel before starting a job with the palette. This will start and pause a print so it triggers the printing mode on KlipperScreen or the Knomi for example.

Copy the FakePrint.gcode file into Klipper (via Mainsail, Fluidd, etc)
In the start job GCODE in canvas, add ___START_JOB___ if you want the bridge to start the fake job automatically.
