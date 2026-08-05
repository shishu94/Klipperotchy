from flask import Flask, render_template, request, jsonify
from SerialMoonrakerBridge import SerialMoonrakerBridge

app = Flask(__name__)
bridge = SerialMoonrakerBridge()

# The Palette's own web UI, shown in the always-on iframe.  Fixed on this setup.
PALETTE_WEB = "192.168.1.90:5000"

# Start auto-connecting immediately: the supervisor keeps Moonraker + Palette
# linked, retrying every second and greeting the Palette until it answers.
bridge.start_supervisor()


@app.route("/status")
def status():
    # Polled by the header dots -- MR = printer/Moonraker, P3 = Palette.
    return jsonify(
        moonraker=bridge.moonraker_connected(),
        palette=bridge.palette_connected(),
        connected=bridge.is_connected,
        auto=bool(bridge.auto_connect.value),
    )


@app.route("/recent")
def recent():
    # Last 10 P3<->Moonraker traffic lines for the field under the iframe.
    return jsonify(lines=bridge.recent_lines())


@app.route("/", methods=("GET", "POST"))
def index():
    if request.method == "POST":
        if "disconnect_bridge" in request.form:
            bridge.set_auto_connect(False)     # manual disconnect stops auto-reconnect
            bridge.disconnect_locked()
        elif "auto_connect" in request.form:
            bridge.set_auto_connect(request.form["auto_connect"] == "enabled")
    return render_template(
        "index.html",
        palette_web=PALETTE_WEB,
        auto=bool(bridge.auto_connect.value),
        connected=bridge.is_connected,
    )
