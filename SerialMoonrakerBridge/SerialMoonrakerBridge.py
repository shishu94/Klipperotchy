import asyncio
import http.client
import json
import logging
import serial
import threading
import time
import websockets

from datetime import datetime
from urllib.parse import quote
from multiprocessing import Process, Value, Array

# Timestamps (log + recent field) use Oslo local time (Europe/Oslo, CET/CEST
# with automatic DST).  Falls back to system-local if zoneinfo/tzdata is absent.
try:
    from zoneinfo import ZoneInfo          # Python 3.9+
    OSLO = ZoneInfo("Europe/Oslo")
except Exception:
    OSLO = None

# Fixed network locations -- the printer's Moonraker and the Palette never move
# on this setup, so there's nothing to configure; the supervisor auto-connects.
MOONRAKER_ADDRESS = "192.168.1.85:7125"
SERIAL_PORT = "/dev/ttyGS0"
BAUDRATE = 250000

# Once the Palette has connected, PALETTE_HEARTBEAT seconds of total serial
# silence is treated as a drop, and the bridge falls back to greeting so the
# Palette can reconnect.  It polls M105 ~every 5s while connected, so a healthy
# link never reaches this -- only a real disconnect does.  (Heats don't trip it:
# the bridge is blocked in the barrier POST then, not in readline.)
PALETTE_HEARTBEAT = 30

# End-of-print marker.  When the Palette streams this, the bridge enters
# "ending" mode and flushes the tail line-by-line, so END_PRINT and the trailing
# fan-off (M106 S0 P2) aren't orphaned in a batch that never fills the time/size
# triggers.  A real move afterwards (a new print) exits the mode.
END_COMMAND = "END_PRINT"

# In-memory ring of the last RECENT_N traffic lines (P3-> and MR->) shown under
# the web iframe.  RECENT_LEN bytes per slot; longer lines are truncated.
RECENT_N = 20
RECENT_LEN = 200

# How far ahead of the printer the Palette is allowed to run, measured in
# SECONDS OF PRINT TIME instead of a move count.  We estimate each move's
# duration (XYZ distance / feedrate), accumulate it, and once the un-posted
# moves add up to MAX_SECONDS_AHEAD we POST them and stop reading serial until
# the POST returns -- so the Palette can never get more than ~this many seconds
# of print ahead of the nozzle.
#
# The win over a fixed move count: this AUTO-SIZES the batch.  Hundreds of tiny
# fast infill moves post together (throughput), while a couple of long tower
# moves post on their own (low latency) -- and the tower is exactly where the
# Palette's mid-splice "M220 S50" slow-down must land fast or its filament
# buffer runs dry.  With a move count, 8 long tower moves = 5-6 s of backlog;
# here they are bounded to MAX_SECONDS_AHEAD regardless of move length.
#
# The M220 then reaches the printer within ~MAX_SECONDS_AHEAD + Klipper's own
# ~1-2 s lookahead (which the bridge CANNOT shrink -- that is the floor).  Lower
# this for a tighter slow-down; there is no throughput penalty (a POST is cheap
# vs the print-time it carries), only Klipper-buffer starvation if pushed near
# zero.  1.0 s targets ~2-3 s total M220 latency, down from the old 5-6 s.
MAX_SECONDS_AHEAD = 1.0

# Safety cap: a burst of zero-duration commands (M-codes, retracts) adds no
# estimated seconds, so this guarantees they still get posted, and bounds the
# size of any single POST.
HARD_CAP_MOVES = 64

# Commands that must COMPLETE on the printer before the Palette may continue.
# The ping sequence (G4/M400 immediately followed by O31) depends on this: the
# Palette reads its filament encoder right after, so if the printer has not
# caught up the ping reports a false drift and splices land in the wrong place.
# These are rare (a ping every LINEARPINGLENGTH mm), so flushing costs nothing.
BARRIERS = ("G4", "M400", "M109", "M190", "G28")


# --- optional debug log ---------------------------------------------------
# Records P3<->printer traffic to LOG_FILE.  Watch it over SSH with:
#     tail -f /tmp/p3bridge.log
# NOTE: /tmp is RAM (tmpfs) on DietPi and this file grows for the whole print.
# It also costs an open/write/close per gcode line, which slows the command
# rate on a Pi Zero -- keep it False while printing fast.
LOG_ENABLED = False
LOG_FILE = "/tmp/p3bridge.log"


class SerialMoonrakerBridge:
    def __init__(self):
        self.is_connected = False
        # Shared timestamp, updated by the listenSerial child on every line the
        # Palette sends, so the Flask parent can show a live "Palette connected"
        # dot.  Value = plain shared memory (mmap), NOT a Manager -- a Manager
        # here hung connect() before.
        self.palette_last_seen = Value('d', 0.0, lock=False)
        
        self.moonraker_address = None
        self.serial_port = None
        self.baudrate = None

        self.serial_process = None
        self.moonraker_process = None

        # 1 = auto-connect on.  Shared Value so the forked listenSerial child
        # sees live toggles and stops greeting when disabled.
        self.auto_connect = Value('b', 1, lock=False)
        self.moonraker_ok = False        # live Moonraker reachability (MR dot)
        self._supervisor_started = False
        self._lock = threading.Lock()    # serialise connect / disconnect

        # Last RECENT_N traffic lines in a shared ring, so the web UI can show
        # recent P3<->Moonraker commands without touching the log file.
        self.recent = Array('c', RECENT_N * RECENT_LEN)
        self.recent_idx = Value('i', 0)

    def palette_connected(self):
        # True when the bridge has actually received traffic from the Palette
        # recently (it polls M105 ~every 5s while connected).  Stronger than
        # is_connected: it catches the case where the Palette *shows* connected
        # on its screen but nothing is reaching the bridge.  May read false
        # during a long M109/M190 heat (bridge blocked in the POST, both sides
        # quiet) -- that's a busy bridge, not a lost Palette.
        if not self.is_connected:
            return False
        return (time.time() - self.palette_last_seen.value) < 15.0

    def moonraker_connected(self):
        # Green MR dot: bridge is connected AND Moonraker is currently answering.
        return self.is_connected and self.moonraker_ok

    def _record(self, entry):
        # Append an already-timestamped line (from _log) to the shared ring
        # (last RECENT_N lines).  Cheap padded memcpy under the Array lock, no
        # file I/O.  Runs on the hot path from both child processes.
        try:
            b = entry.encode("utf-8", "ignore")[:RECENT_LEN - 1]
            b = b + b"\x00" * (RECENT_LEN - len(b))          # pad to a full slot
            with self.recent.get_lock():
                slot = (self.recent_idx.value % RECENT_N) * RECENT_LEN
                self.recent.get_obj()[slot:slot + RECENT_LEN] = b
                self.recent_idx.value += 1
        except Exception:
            pass

    def recent_lines(self):
        # The last RECENT_N traffic lines, oldest-first, for the web UI.
        with self.recent.get_lock():
            n = self.recent_idx.value
            raw = self.recent.get_obj().raw
        count = min(n, RECENT_N)
        out = []
        for k in range(count):
            idx = (n - count + k) % RECENT_N
            chunk = raw[idx * RECENT_LEN:(idx + 1) * RECENT_LEN]
            out.append(chunk.split(b"\x00", 1)[0].decode("utf-8", "ignore"))
        return out

    def set_auto_connect(self, enabled):
        self.auto_connect.value = 1 if enabled else 0

    def start_supervisor(self):
        # Launch the auto-connect loop once, in a daemon thread of the Flask
        # process (so it shares is_connected / auto_connect / moonraker_ok
        # directly with the web handlers).
        if self._supervisor_started:
            return
        self._supervisor_started = True
        threading.Thread(target=self._supervise, daemon=True).start()

    def _processes_alive(self):
        return (self.serial_process is not None and self.serial_process.is_alive()
                and self.moonraker_process is not None and self.moonraker_process.is_alive())

    def connect_fixed(self):
        with self._lock:
            if not self.is_connected:
                self.connect(SERIAL_PORT, BAUDRATE, MOONRAKER_ADDRESS)

    def disconnect_locked(self):
        with self._lock:
            self.disconnect()

    def _supervise(self):
        # Every second: refresh Moonraker reachability (for the MR dot); tear
        # down a connection whose child process has died; and, while auto-connect
        # is on, (re)connect whenever the printer is up and we aren't connected.
        # The Palette self-heals -- listenSerial greets until it answers, then
        # stops.  Everything retries on the next 1 s tick.
        while True:
            try:
                self.moonraker_ok = self.checkMoonraker(MOONRAKER_ADDRESS)
                if self.is_connected and not self._processes_alive():
                    self.disconnect_locked()
                if self.auto_connect.value and not self.is_connected and self.moonraker_ok:
                    self.connect_fixed()
            except Exception:
                logging.exception("supervisor loop")
            time.sleep(1)

    def _log(self, msg):
        # Timestamp every line and push it to the in-memory ring for the web UI
        # (ALWAYS, even with LOG_ENABLED off); additionally append to the file
        # only when logging is enabled.
        now = datetime.now(OSLO)   # Oslo local time (or system-local fallback)
        # YYYY.MM.DD HH:MM:SS:mmm  (mmm = 3-digit milliseconds)
        ts = now.strftime("%Y.%m.%d %H:%M:%S:") + now.strftime("%f")[:3]
        entry = ts + "  " + msg
        self._record(entry)
        if not LOG_ENABLED:
            return
        try:
            with open(LOG_FILE, "a") as f:
                f.write(entry + "\n")
        except Exception:
            pass

    def connect(self, serial_port, baudrate, moonraker_address):
        self.moonraker_address = moonraker_address
        self.serial_port = serial_port
        self.baudrate = baudrate
        
        serial_is_alive = False
        moonraker_is_alive = False

        try:
            if not self.checkMoonraker():
                return

            self.serial_con = serial.Serial(
                port=self.serial_port, baudrate=self.baudrate
            )

            self.serial_process = Process(target=self.listenSerial)
            self.moonraker_process = Process(target=self.listenMoonraker)

            self.serial_process.start()
            self.moonraker_process.start()

            # Give the child processes a moment to fail (e.g. serial write
            # error or websocket handshake failure) before we trust is_alive().
            time.sleep(0.5)

            serial_is_alive = self.serial_process.is_alive()
            moonraker_is_alive = self.moonraker_process.is_alive()

        except Exception:
            logging.exception("Failed to connect bridge")

        self.is_connected = serial_is_alive and moonraker_is_alive
        # one of the processes is not running, cleanup
        if not self.is_connected:
            self.disconnect()

    def disconnect(self):
        self.is_connected = False
        if self.serial_process:
            self.serial_process.terminate()
            self.serial_process.join()
            self.serial_process = None
        if self.moonraker_process:
            self.moonraker_process.terminate()
            self.moonraker_process.join()
            self.moonraker_process = None

        # Explicitly release the serial port so a quick reconnect does not
        # fail with "device busy".
        if getattr(self, "serial_con", None):
            try:
                self.serial_con.close()
            except Exception:
                logging.exception("Failed to close serial port")
        self.serial_con = None

        self.moonraker_address = None
        self.serial_port = None
        self.baudrate = None

    def checkMoonraker(self, address=None):
        try:
            # timeout so an unreachable printer fails FAST -- otherwise the
            # supervisor blocks on the dead TCP connect and moonraker_ok stays
            # stale (MR dot wrongly green).
            conn = http.client.HTTPConnection(address or self.moonraker_address or MOONRAKER_ADDRESS, timeout=3)
            conn.request("GET", "/printer/info")
            response = conn.getresponse()
            conn.close()
            return response.status == 200
        except:
            return False

    def tempReport(self):
        # Answer M105 locally from Moonraker's object model so the Palette
        # always gets a proper single-line Marlin reply, instead of relying on
        # the M105 text coming back asynchronously over the websocket.
        try:
            # timeout so answering M105 never blocks listenSerial on a dead
            # Moonraker -- otherwise the Palette stops being read and P3 goes red.
            conn = http.client.HTTPConnection(self.moonraker_address, timeout=3)
            conn.request("GET", "/printer/objects/query?extruder&heater_bed")
            st = json.loads(conn.getresponse().read())["result"]["status"]
            conn.close()
            e, b = st["extruder"], st["heater_bed"]
            return "ok T:%.1f /%.1f B:%.1f /%.1f\n" % (
                e["temperature"], e["target"], b["temperature"], b["target"])
        except Exception:
            return "ok T:0.0 /0.0 B:0.0 /0.0\n"

    def listenSerial(self):
        # On the FIRST connect after a Palette power-cycle the USB-CDC link is
        # not ready the instant we open the port, so a single greeting is lost.
        # Re-greet on every idle tick until the Palette starts talking, then
        # switch to blocking reads.  No cap: with auto-connect the Palette may
        # power up long after the bridge, and greeting until it answers -- then
        # stopping -- is exactly the "connect once both are up" behaviour.
        self.serial_con.timeout = 1
        connected = False
        ending = False        # True while streaming the end sequence (END_PRINT...)

        # Single-threaded lock-step: collect moves, POST them, repeat.  No queue,
        # no sender thread -- the blocking POST is the backpressure (while it
        # runs we stop reading serial, so the Palette waits).  We bound the
        # collected batch by ESTIMATED PRINT TIME (pending_seconds), so the
        # Palette runs at most ~MAX_SECONDS_AHEAD of print ahead of the nozzle.
        # `state` tracks position + feedrate so we can estimate each move.
        self._mr_conn = None
        pending = []
        pending_seconds = 0.0
        state = {"x": 0.0, "y": 0.0, "z": 0.0, "f": 1800.0}

        def flush():
            nonlocal pending_seconds
            if pending:
                self._post("/printer/gcode/script?script=" + quote("\n".join(pending)))
                del pending[:]
            pending_seconds = 0.0

        self.serial_con.write(b"start\r\nok\r\n")
        self._log("=== listenSerial started, greeting sent ===")
        try:
            while True:
                line = self.serial_con.readline().decode("utf-8", "ignore").strip()
                if not line:
                    # Empty = the read timed out with no data.  Before connect
                    # that's every 1s (greet fast).  After connect it means
                    # PALETTE_HEARTBEAT s of silence -> the Palette dropped, so
                    # fall back to the 1s greeting cadence until it answers.
                    # Greeting is gated on auto_connect so the enable/disable
                    # button fully stops auto-(re)connection.
                    if connected:
                        connected = False
                        self.serial_con.timeout = 1
                        self._log("=== Palette silent %ds -- re-greeting ===" % PALETTE_HEARTBEAT)
                    if self.auto_connect.value:
                        self.serial_con.write(b"start\r\nok\r\n")
                    continue
                if not connected:
                    connected = True
                    self.serial_con.timeout = PALETTE_HEARTBEAT   # heartbeat, not blocking

                self.palette_last_seen.value = time.time()   # feeds the web-UI dot
                self._log("P3 -> " + line)

                # Temperature poll: reply directly, do not queue it as gcode.
                if line.startswith("M105"):
                    report = self.tempReport()
                    self._log("   M105 <- " + report.strip())
                    self.serial_con.write(report.encode("utf-8"))
                    continue

                # Explicit job-start sentinel from the Palette.
                if line == "___START_JOB___":
                    flush()
                    self._post("/printer/print/start?filename=RemotePrint.gcode")
                    self.serial_con.write(b"ok\n")
                    continue

                if line.split()[0].upper() in BARRIERS:
                    # Sync point: everything collected before it must land first,
                    # then the barrier runs ALONE with a blocking POST --
                    # /gcode/script does not return until the command completes
                    # on the printer (M400 = toolhead idle).  Only then do we
                    # ack, so a ping that follows reads its encoder after the
                    # printer has truly caught up.  This is what keeps the ping %
                    # accurate.
                    flush()
                    self._post("/printer/gcode/script?script=" + quote(line))
                    self._log("   ^^ " + line + " completed on printer (toolhead idle)")
                    self.serial_con.write(b"ok\n")
                    continue

                # Ordinary move: ack at once so the Palette keeps streaming,
                # collect it, and add its estimated print-time to the batch.
                pending.append(line)
                self.serial_con.write(b"ok\n")
                dur = self._move_seconds(line, state)
                pending_seconds += dur

                # END_PRINT begins the end sequence; a real move means a (new)
                # print is streaming again.  While "ending" we flush every line
                # so the tail (END_PRINT, then M106 S0 P2) is never orphaned in a
                # batch that never reaches the time/size flush triggers.
                if dur > 0:
                    ending = False
                elif line.split()[0].upper() == END_COMMAND:
                    ending = True

                # The Palette's cancel sequence (M140 S0 / M104 T0 S0 / M220
                # S100) never calls Klipper's CANCEL_PRINT, so the printer skips
                # its cleanup -- retract, park, chamber fan off.  M104 T0 S0 is
                # unique to that sequence (P2PP never turns the hotend off with
                # T0 mid-print), so use it to trigger a proper cancel.
                if line.replace(" ", "").upper() == "M104T0S0":
                    flush()                          # push the cancel sequence so far
                    self._post("/printer/gcode/script?script=" + quote("CANCEL_PRINT"))
                    continue

                # Expedite the Palette's mid-splice flow-control slow-down: POST
                # the batch (with this M220 on the end) immediately instead of
                # letting it wait for the batch to fill, so the printer slows
                # before the filament buffer runs dry.  The restore (M220 S100)
                # is expedited the same way, so full speed resumes promptly.
                if line.split()[0].upper() == "M220":
                    flush()
                    continue

                # POST once the un-sent moves add up to MAX_SECONDS_AHEAD of
                # print time -- bounding how far ahead the Palette runs in TIME,
                # not in move count.  HARD_CAP_MOVES catches zero-duration runs.
                if ending or pending_seconds >= MAX_SECONDS_AHEAD or len(pending) >= HARD_CAP_MOVES:
                    flush()
        finally:
            self.serial_con.close()

    def _post(self, path):
        # One keep-alive connection reused for the whole print; reconnect once
        # on a dropped socket.  Moonraker's /printer/gcode/script does NOT return
        # until the script has actually run on the printer -- that is what makes
        # the barriers in listenSerial real sync points.  Called only from the
        # listenSerial process, so self._mr_conn is private to that process.
        for _attempt in (1, 2):
            try:
                if self._mr_conn is None:
                    self._mr_conn = http.client.HTTPConnection(self.moonraker_address)
                self._mr_conn.request("POST", path)
                response = self._mr_conn.getresponse()
                body = response.read()           # must drain before reuse
                if response.status != 200:
                    self._log("   MR ERROR %s %s" % (
                        response.status, body[:120].decode("utf-8", "ignore")))
                return
            except Exception:
                try:
                    self._mr_conn.close()
                except Exception:
                    pass
                self._mr_conn = None

    def _move_seconds(self, line, state):
        # Rough duration estimate for a G0/G1 move, updating tracked position
        # and feedrate in `state`.  Time = XYZ distance / feedrate; E is ignored
        # (retracts are negligible, and absolute-vs-relative E is ambiguous
        # here).  G92 re-seeds position without moving.  Anything else is 0.
        # Only needs ~20% accuracy -- it just paces how far the Palette may run
        # ahead, and Klipper does the real motion planning.
        parts = line.split()
        if not parts:
            return 0.0
        cmd = parts[0].upper()

        if cmd == "G92":
            for p in parts[1:]:
                a = p[:1].upper()
                if a in ("X", "Y", "Z"):
                    try:
                        state[a.lower()] = float(p[1:])
                    except ValueError:
                        pass
            return 0.0

        if cmd not in ("G0", "G1"):
            return 0.0

        nx, ny, nz = state["x"], state["y"], state["z"]
        moved = False
        for p in parts[1:]:
            a = p[:1].upper()
            try:
                val = float(p[1:])
            except ValueError:
                continue
            if a == "X":
                nx = val; moved = True
            elif a == "Y":
                ny = val; moved = True
            elif a == "Z":
                nz = val; moved = True
            elif a == "F":
                state["f"] = val

        if not moved:
            return 0.0                              # F-only or E-only move

        feed = state["f"] if state["f"] > 0 else 1800.0
        dist = ((nx - state["x"]) ** 2 +
                (ny - state["y"]) ** 2 +
                (nz - state["z"]) ** 2) ** 0.5
        state["x"], state["y"], state["z"] = nx, ny, nz
        return dist * 60.0 / feed                   # mm / (mm/min) * 60 = seconds

    def listenMoonraker(self):
        asyncio.run(self.listenMoonrakerAsync())

    async def listenMoonrakerAsync(self):
        async with websockets.connect(
            "ws://" + self.moonraker_address + "/websocket"
        ) as moonraker_ws:
            while True:
                response = await moonraker_ws.recv()
                response_object = json.loads(response)
                if response_object.get("method") == "notify_gcode_response":
                    for item in response_object["params"]:
                        self._log("MR -> " + item)
                        self.serial_con.write((item + "\n").encode("utf-8"))
