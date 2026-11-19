"""Network printing for printers that aren't Bambu: Klipper (Moonraker), OctoPrint and PrusaLink.

Each class looks like app.device.BambuPrinter to the rest of the app: connect / disconnect / connected,
status() with the same keys (gcode_state RUNNING / PAUSE / FINISH / FAILED / IDLE, mc_percent, mc_remaining_time
in minutes, layer_num, total_layer_num, temperatures), command(), set_temp(), set_speed(), upload_and_print()
and camera_url(). A background thread polls the printer every 2 s.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

SPEED_PCT = {1: 50, 2: 100, 3: 124, 4: 166}         # Silent / Standard / Sport / Ludicrous, like Bambu's


def _base(host, default_port=None):
    host = (host or "").strip().rstrip("/")
    if not host:
        raise ValueError("Fill in the printer's address (Printer settings → Connection)")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    u = urllib.parse.urlsplit(host)
    if default_port and not u.port and u.scheme == "http":
        host = f"{u.scheme}://{u.hostname}:{default_port}{u.path}"
    return host


def _multipart(fields, file_field, filename, data):
    b = uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    out.append(f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
               f'Content-Type: application/octet-stream\r\n\r\n'.encode())
    out.append(data)
    out.append(f"\r\n--{b}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={b}"


class _Host:
    kind = "host"
    default_port = None

    def __init__(self):
        self.connected = False
        self.base = ""
        self.headers = {}
        self.opener = urllib.request.build_opener()
        self.state = {}
        self.error = ""
        self.ip = ""
        self.serial = ""
        self._stop = threading.Event()
        self._thread = None

    # ------------------------------------------------------------ HTTP
    def _req(self, method, path, body=None, ctype=None, timeout=10, headers=None):
        h = {**self.headers, **(headers or {})}
        if body is not None and not isinstance(body, bytes):
            body, ctype = json.dumps(body).encode(), "application/json"
        if ctype:
            h["Content-Type"] = ctype
        r = urllib.request.Request(self.base + path, data=body, headers=h, method=method)
        try:
            with self.opener.open(r, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            if e.code in (401, 403):
                raise PermissionError(f"The printer refused the login ({e.code}): check the API key / password "
                                      "in Printer settings → Connection") from e
            raise ConnectionError(f"{self.kind} answered {e.code}: {msg}") from e
        except (urllib.error.URLError, OSError) as e:
            raise ConnectionError(f"Can't reach the printer at {self.base}: {getattr(e, 'reason', e)}") from e
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return {"text": raw.decode("utf-8", "replace")}

    # ------------------------------------------------------------ life cycle
    def connect(self, host, api_key="", user="", password=""):
        self.base = _base(host, self.default_port)
        self.ip = urllib.parse.urlsplit(self.base).hostname or ""
        self.serial = self.base
        self._auth(api_key, user, password)
        self.poll()                                    # raises when the printer can't be reached
        self.connected = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _auth(self, api_key, user, password):
        if api_key:
            self.headers["X-Api-Key"] = api_key

    def disconnect(self):
        self._stop.set()
        self.connected = False

    def _loop(self):
        fails = 0
        while not self._stop.wait(2):
            try:
                self.poll()
                fails, self.error = 0, ""
                self.connected = True
            except Exception as e:
                fails += 1
                self.error = str(e)
                if fails >= 3:
                    self.connected = False

    def status(self):
        s = dict(self.state)
        return {"connected": self.connected, "ip": self.ip, "serial": self.serial, "kind": self.kind,
                "age_s": 0, "ams": [], "tray_now": 255, "auto_refill": None, "runout": False,
                "print_error": 0, "hms": [], "spd_lvl": s.get("spd_lvl"), "error": self.error,
                **{k: s.get(k) for k in ("gcode_state", "mc_percent", "mc_remaining_time", "layer_num",
                                         "total_layer_num", "nozzle_temper", "nozzle_target_temper",
                                         "bed_temper", "bed_target_temper", "subtask_name")}}

    # shared helpers
    def set_speed(self, level):
        lvl = {"silent": 1, "standard": 2, "sport": 3, "ludicrous": 4}.get(str(level).lower(), level)
        if lvl not in SPEED_PCT:
            raise ValueError("Speed must be silent, standard, sport or ludicrous")
        self.gcode(f"M220 S{SPEED_PCT[lvl]}")
        self.state["spd_lvl"] = lvl

    def set_temp(self, what, temp):
        t = int(round(float(temp)))
        if what == "nozzle":
            if not 0 <= t <= 350:
                raise ValueError("Nozzle temperature must be 0-350 °C")
            self.gcode(f"M104 S{t}")
        elif what == "bed":
            if not 0 <= t <= 130:
                raise ValueError("Bed temperature must be 0-130 °C")
            self.gcode(f"M140 S{t}")
        else:
            raise ValueError(what)

    def set_auto_refill(self, on):
        pass                                           # Bambu only

    def camera_url(self):
        return ""


class Moonraker(_Host):
    """Klipper printers with Moonraker (Fluidd / Mainsail; Creality K1 / K2 / Hi, Voron, Sovol, Elegoo Neptune 4…)."""
    kind = "Klipper"
    default_port = 7125
    STATES = {"printing": "RUNNING", "paused": "PAUSE", "complete": "FINISH", "cancelled": "FAILED",
              "error": "FAILED", "standby": "IDLE"}

    def poll(self):
        q = self._req("GET", "/printer/objects/query?print_stats&virtual_sdcard&extruder&heater_bed&gcode_move")
        st = (q.get("result") or {}).get("status") or {}
        ps, sd = st.get("print_stats") or {}, st.get("virtual_sdcard") or {}
        ex, bed, gm = st.get("extruder") or {}, st.get("heater_bed") or {}, st.get("gcode_move") or {}
        prog = float(sd.get("progress") or 0)
        dur = float(ps.get("print_duration") or 0)
        left = dur * (1 - prog) / prog / 60 if prog > 0.01 and dur > 0 else None
        info = ps.get("info") or {}
        sf = float(gm.get("speed_factor") or 1)
        self.state.update({
            "gcode_state": self.STATES.get(ps.get("state"), "IDLE"), "mc_percent": int(round(prog * 100)),
            "mc_remaining_time": int(round(left)) if left is not None else None,
            "layer_num": info.get("current_layer"), "total_layer_num": info.get("total_layer"),
            "nozzle_temper": ex.get("temperature"), "nozzle_target_temper": ex.get("target"),
            "bed_temper": bed.get("temperature"), "bed_target_temper": bed.get("target"),
            "subtask_name": os.path.splitext(ps.get("filename") or "")[0],
            "spd_lvl": min(SPEED_PCT, key=lambda k: abs(SPEED_PCT[k] - sf * 100))})

    def gcode(self, line):
        self._req("POST", "/printer/gcode/script?script=" + urllib.parse.quote(line.strip()))

    def command(self, name):
        paths = {"pause": "/printer/print/pause", "resume": "/printer/print/resume", "stop": "/printer/print/cancel"}
        if name in paths:
            self._req("POST", paths[name])
        elif name in ("light_on", "light_off"):
            raise ValueError("Turn the light on or off in your printer's web page (Klipper has no standard light command)")
        else:
            raise ValueError(f"Unknown command {name}")

    def upload_and_print(self, local_path, remote_name):
        with open(local_path, "rb") as f:
            body, ctype = _multipart({"root": "gcodes", "print": "true"}, "file", remote_name, f.read())
        self._req("POST", "/server/files/upload", body, ctype, timeout=300)
        return remote_name

    def camera_url(self):
        try:
            cams = (self._req("GET", "/server/webcams/list").get("result") or {}).get("webcams") or []
        except Exception:
            cams = []
        for c in cams:
            u = c.get("stream_url") or ""
            if u:
                if u.startswith("/"):
                    p = urllib.parse.urlsplit(self.base)
                    u = f"{p.scheme}://{p.hostname}{u}"
                return u
        return f"http://{self.ip}:8080/?action=stream"     # mjpg-streamer, the usual default (Creality K1 / K2)


class OctoPrint(_Host):
    kind = "OctoPrint"
    STATES = {"Printing": "RUNNING", "Paused": "PAUSE", "Pausing": "PAUSE", "Operational": "IDLE",
              "Finishing": "RUNNING", "Cancelling": "FAILED", "Error": "FAILED", "Offline": "FAILED"}

    def poll(self):
        job = self._req("GET", "/api/job")
        try:
            pr = self._req("GET", "/api/printer?exclude=sd")
        except ConnectionError:                        # 409 while the printer is offline
            pr = {}
        t = pr.get("temperature") or {}
        prog = (job.get("progress") or {})
        st = (job.get("state") or "").split(" (")[0]
        left = prog.get("printTimeLeft")
        self.state.update({
            "gcode_state": self.STATES.get(st, "FINISH" if st.startswith("Operational") and prog.get("completion") == 100 else "IDLE"),
            "mc_percent": int(round(prog.get("completion") or 0)),
            "mc_remaining_time": int(round(left / 60)) if left else None,
            "nozzle_temper": (t.get("tool0") or {}).get("actual"), "nozzle_target_temper": (t.get("tool0") or {}).get("target"),
            "bed_temper": (t.get("bed") or {}).get("actual"), "bed_target_temper": (t.get("bed") or {}).get("target"),
            "subtask_name": os.path.splitext(((job.get("job") or {}).get("file") or {}).get("name") or "")[0]})

    def gcode(self, line):
        self._req("POST", "/api/printer/command", {"commands": [line.strip()]})

    def command(self, name):
        body = {"pause": {"command": "pause", "action": "pause"}, "resume": {"command": "pause", "action": "resume"},
                "stop": {"command": "cancel"}}.get(name)
        if body:
            self._req("POST", "/api/job", body)
        elif name in ("light_on", "light_off"):
            raise ValueError("OctoPrint has no standard light command")
        else:
            raise ValueError(f"Unknown command {name}")

    def upload_and_print(self, local_path, remote_name):
        with open(local_path, "rb") as f:
            body, ctype = _multipart({"select": "true", "print": "true"}, "file", remote_name, f.read())
        self._req("POST", "/api/files/local", body, ctype, timeout=300)
        return remote_name

    def camera_url(self):
        try:
            s = self._req("GET", "/api/settings")
            u = ((s.get("webcam") or {}).get("streamUrl")) or "/webcam/?action=stream"
        except Exception:
            u = "/webcam/?action=stream"
        if u.startswith("/"):
            p = urllib.parse.urlsplit(self.base)
            u = f"{p.scheme}://{p.hostname}{(':' + str(p.port)) if p.port else ''}{u}"
        return u


class PrusaLink(_Host):
    """Prusa MK4 / MK4S / CORE One / MINI / XL / MK3.5 with PrusaLink (user "maker" + the password on the printer)."""
    kind = "PrusaLink"
    STATES = {"PRINTING": "RUNNING", "PAUSED": "PAUSE", "FINISHED": "FINISH", "STOPPED": "FAILED",
              "ERROR": "FAILED", "IDLE": "IDLE", "READY": "IDLE", "ATTENTION": "PAUSE", "BUSY": "RUNNING"}

    def _auth(self, api_key, user, password):
        if password:
            mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            mgr.add_password(None, self.base, user or "maker", password)
            self.opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))
        elif api_key:
            self.headers["X-Api-Key"] = api_key

    def poll(self):
        s = self._req("GET", "/api/v1/status")
        p, j = s.get("printer") or {}, s.get("job") or {}
        self.job_id = j.get("id")
        left = j.get("time_remaining")
        spd = p.get("speed") or 100
        self.state.update({
            "gcode_state": self.STATES.get(p.get("state"), "IDLE"), "mc_percent": int(round(j.get("progress") or 0)),
            "mc_remaining_time": int(round(left / 60)) if left else None,
            "nozzle_temper": p.get("temp_nozzle"), "nozzle_target_temper": p.get("target_nozzle"),
            "bed_temper": p.get("temp_bed"), "bed_target_temper": p.get("target_bed"),
            "spd_lvl": min(SPEED_PCT, key=lambda k: abs(SPEED_PCT[k] - spd))})

    def gcode(self, line):
        raise ValueError("PrusaLink can't send G-code: change temperatures and speed on the printer's screen")

    def command(self, name):
        jid = getattr(self, "job_id", None)
        if name in ("pause", "resume", "stop") and jid is None:
            raise ValueError("Nothing is printing")
        if name == "pause":
            self._req("PUT", f"/api/v1/job/{jid}/pause")
        elif name == "resume":
            self._req("PUT", f"/api/v1/job/{jid}/resume")
        elif name == "stop":
            self._req("DELETE", f"/api/v1/job/{jid}")
        else:
            raise ValueError("PrusaLink has no light command")

    def upload_and_print(self, local_path, remote_name):
        with open(local_path, "rb") as f:
            data = f.read()
        storage = "usb"
        try:
            st = self._req("GET", "/api/v1/storage")
            names = [x.get("path", "").strip("/") for x in st.get("storage_list") or [] if x.get("available")]
            storage = "usb" if "usb" in names else (names[0] if names else "usb")
        except Exception:
            pass
        self._req("PUT", f"/api/v1/files/{storage}/{urllib.parse.quote(remote_name)}", data, "text/x.gcode",
                  timeout=300, headers={"Print-After-Upload": "?1", "Overwrite": "?1"})
        return remote_name


KINDS = {"moonraker": Moonraker, "octoprint": OctoPrint, "prusalink": PrusaLink}
