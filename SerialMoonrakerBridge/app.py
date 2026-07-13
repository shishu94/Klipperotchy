from flask import Flask, render_template, request
from SerialMoonrakerBridge import SerialMoonrakerBridge

app = Flask(__name__)
bridge = SerialMoonrakerBridge()

# Remembered across requests so the Palette 3 iframe survives page reloads.
p3_address = None


def defaultIfEmpty(request, key, default):
    value = request.form.get(key, "").strip()
    if value == "":
        value = default
    return value


@app.route("/", methods=("GET", "POST"))
def index():
    global p3_address
    failure = False
    if request.method == "POST":
        if "disconnect_bridge" in request.form:
            bridge.disconnect()
            p3_address = None
        elif "connect_bridge" in request.form:
            moonraker_address = request.form["moonraker_address"].strip()
            serial_port = defaultIfEmpty(request, "serial_port", "/dev/ttyGS0")
            try:
                baudrate = int(defaultIfEmpty(request, "baudrate", "250000"))
            except ValueError:
                baudrate = None

            if moonraker_address != "" and baudrate:
                bridge.connect(
                    serial_port=serial_port,
                    baudrate=baudrate,
                    moonraker_address=moonraker_address,
                )
                failure = not bridge.is_connected
            else:
                failure = True

            if bridge.is_connected and "p3_address" in request.form:
                p3_address = request.form["p3_address"].strip() or None

    if bridge.is_connected:
        return render_template(
            "connected.html",
            moonraker_address=bridge.moonraker_address,
            serial_port=bridge.serial_port,
            baudrate=bridge.baudrate,
            p3_address=p3_address,
        )
    return render_template("disconnected.html", failure=failure)
