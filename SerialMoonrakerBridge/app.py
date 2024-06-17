from flask import Flask, render_template, request
from SerialMoonrakerBridge import SerialMoonrakerBridge

app = Flask(__name__)
bridge = SerialMoonrakerBridge()


def defaultIfEmpty(request, key, default):
    value = request.form[key].strip()
    if value == "":
        value = default
    return value


@app.route("/", methods=("GET", "POST"))
def index():
    p3_address = None
    failure = False
    if request.method == "POST":
        if "disconnect_bridge" in request.form:
            bridge.disconnect()
        elif "connect_bridge" in request.form:
            moonraker_address = request.form["moonraker_address"].strip()
            baudrate = int(defaultIfEmpty(request, "baudrate", "250000"))
            serial_port = defaultIfEmpty(request, "serial_port", "/dev/ttyGS0")

            if moonraker_address != "" and baudrate:
                bridge.connect(
                    serial_port=serial_port,
                    baudrate=baudrate,
                    moonraker_address=moonraker_address,
                )
                failure = not bridge.is_connected
            if "p3_address" in request.form:
                p3_address = request.form["p3_address"].strip()

    if bridge.is_connected:
        return render_template(
            "connected.html",
            moonraker_address=bridge.moonraker_address,
            serial_port=bridge.serial_port,
            baudrate=bridge.baudrate,
            p3_address=p3_address,
        )
    return render_template("disconnected.html", failure=failure)
