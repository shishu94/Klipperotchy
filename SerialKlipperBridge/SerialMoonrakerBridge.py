import asyncio
import http.client
import json
import serial
import websockets

from urllib.parse import quote
from multiprocessing import Process


class SerialMoonrakerBridge:
    def __init__(self):
        self.is_connected = False
        self.moonraker_address = None
        self.serial_process = None
        self.moonraker_process = None
        self.serial_port = None
        self.baudrate = None

    def connect(self, serial_port, baudrate, moonraker_address):
        self.moonraker_address = moonraker_address
        self.serial_port = serial_port
        self.baudrate = baudrate
        if not self.checkMoonraker():
            raise ConnectionError(
                "Moonraker:", self.moonraker_address, "is not reachable."
            )

        self.serial_con = serial.Serial(port=self.serial_port, baudrate=self.baudrate)

        self.serial_process = Process(target=self.listenSerial)
        self.moonraker_process = Process(target=self.listenMoonraker)

        self.serial_process.start()
        self.moonraker_process.start()

        self.is_connected = (
            self.serial_process.is_alive and self.moonraker_process.is_alive
        )
        # one of the processes is not running, cleanup
        if not self.is_connected:
            self.disconnect()

    def disconnect(self):
        self.is_connected = False
        self.serial_process.terminate()
        self.moonraker_process.terminate()
        self.serial_con.close()
        self.serial_con = None

    def checkMoonraker(self):
        conn = http.client.HTTPConnection(self.moonraker_address)
        conn.request("POST", "/printer/info")
        response = conn.getresponse()
        conn.close()
        return response.reason == "OK"

    def listenSerial(self):
        try:
            while True:
                line = self.serial_con.readline().decode("utf-8").strip()
                conn = http.client.HTTPConnection(self.moonraker_address)
                if line == "___START_JOB___":
                    conn.request(
                        "POST", "/printer/print/start?filename=RemotePrint.gcode"
                    )
                else:
                    conn.request("POST", "/printer/gcode/script?script=" + quote(line))

                response = conn.getresponse()
                conn.close()
                if response.reason == "OK":
                    self.serial_con.write(b"ok\n")
        finally:
            self.serial_con.close()

    def listenMoonraker(self):
        asyncio.run(self.listenMoonrakerAsync())

    async def listenMoonrakerAsync(self):
        async with websockets.connect(
            "ws://" + self.moonraker_address + "/websocket"
        ) as moonraker_ws:
            while True:
                response = await moonraker_ws.recv()
                response_object = json.loads(response)
                if response_object["method"] == "notify_gcode_response":
                    for item in response_object["params"]:
                        self.serial_con.write((item + "\n").encode("utf-8"))
