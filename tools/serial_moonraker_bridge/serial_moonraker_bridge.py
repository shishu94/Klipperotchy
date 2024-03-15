import asyncio
import serial
import websockets
import logging
import json
import argparse
import http.client

from urllib.parse import quote
from multiprocessing import Process

parser = argparse.ArgumentParser()
parser.add_argument('--serial', dest='serial', type=str, default='/dev/ttyUSB0', help='Serial port to use (e.g. /dev/ttyAMA0)')
parser.add_argument('--baudrate', dest='baudrate', type=int, choices=[9600, 38400, 57600, 115200, 250000], default=250000)
parser.add_argument('--moonraker', dest='moonraker', type=str, default='localhost:7125', help='Moonraker address. [ip:port] (default localhost:7125)')
parser.add_argument('--log', dest= 'loglevel', type=str,default='WARNING')
args = parser.parse_args()

numeric_level = getattr(logging, args.loglevel.upper(), None)
if not isinstance(numeric_level, int):
    raise ValueError('Invalid log level: %s' % loglevel)
logging.basicConfig(level=numeric_level)

moonraker_address=args.moonraker

def checkMoonraker():
    conn = http.client.HTTPConnection(moonraker_address)                                                       
    conn.request("POST", "/printer/info")                    
    response = conn.getresponse()                                                         
    conn.close()
    if response.reason != 'OK':
        exit()
      
checkMoonraker()

palette_ser = serial.Serial()
palette_ser.port = args.serial
palette_ser.baudrate = args.baudrate
palette_ser.open()


def listenPalette():
	print("Listening palette")
	while True:
		line = palette_ser.readline().decode("utf-8").strip()             
		logging.info('<P3>'+ line + '</P3>')                              
		conn = http.client.HTTPConnection(moonraker_address)              
		if line == "___START_JOB___":                                                          
			conn.request("POST", "/printer/print/start?filename=Remote.gcode")                    
		else:
			conn.request("POST", "/printer/gcode/script?script="+quote(line))
			
		response = conn.getresponse()                                                         
		conn.close()
		if response.reason == 'OK':
			palette_ser.write(b'ok\n')

	

async def listenMoonrakerAsync():
    print("Listening moonraker")
    async with websockets.connect('ws://'+moonraker_address+'/websocket') as moonraker_ws:
        while True:
            response = await moonraker_ws.recv()
            response_object = json.loads(response)
            if response_object['method'] == "notify_gcode_response":
                for item in response_object['params']:
                    logging.info('<MoonRaker>' + item + '</Moonraker>')
                    palette_ser.write((item+'\n').encode('utf-8'))

	
Process(target=listenPalette).start()
asyncio.run(listenMoonrakerAsync())
	
