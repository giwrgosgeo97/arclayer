"""Bambu Lab printer connection over the local network (LAN).

Uses the printer's local interfaces:
  * MQTT over TLS on port 8883 (user "bblp", password = access code) for status and commands
  * FTPS (implicit TLS) on port 990 for uploading the .gcode.3mf to the SD card

On newer firmware, third-party LAN control may need "LAN Only Mode" plus
"Developer Mode" turned on in the printer's network settings.
"""
import ftplib
import json
import os
import socket
import ssl
import threading
import time

import paho.mqtt.client as mqtt


def _insecure_ctx():
    # the printer uses a self-signed certificate
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class _ImplicitFTPS(ftplib.FTP_TLS):
    """FTPS with implicit TLS and TLS session reuse on the data channel (the printer needs both)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size

    def storbinary(self, cmd, fp, blocksize=65536, callback=None, rest=None):
        """ftplib's version ends the data channel with a TLS unwrap() that waits for the printer's
        close_notify, which Bambu printers never send: "The read operation timed out".
        Shut the socket down instead (as other Bambu LAN tools do)."""
        self.voidcmd("TYPE I")
        conn = self.transfercmd(cmd, rest)
        try:
            while True:
                buf = fp.read(blocksize)
                if not buf:
                    break
                conn.sendall(buf)
                if callback:
                    callback(buf)
            if isinstance(conn, ssl.SSLSocket) and "vsFTPd" in (self.welcome or ""):
                conn.unwrap()
            else:
                # half-close: the printer sees the end of the file and answers 226 on the control channel.
                # Closing fully before that can reset the connection and cut the file short.
                try:
                    conn.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
            old = self.sock.gettimeout()
            self.sock.settimeout(15)         # some firmware only answers once the data channel is closed
            try:
                return self.voidresp()
            finally:
                self.sock.settimeout(old)
        finally:
            conn.close()


def runout_code(err):
    """True when the printer's error is a filament runout: xxyy8011 (AMS, AMS lite, AMS-HT, external spool)
    or the toolhead sensor (03008004, 03008015)."""
    try:
        e = int(err or 0)
    except (TypeError, ValueError):
        return False
    return e != 0 and ((e & 0xFFFF) == 0x8011 or e in (0x03008004, 0x03008015))


class BambuPrinter:
    def __init__(self):
        self.client = None
        self.ip = self.serial = self.code = None
        self.connected = False
        self.error = ""
        self.state = {}
        self.reply = None
        self.last_update = 0.0
        self.lock = threading.Lock()

    # ------------------------------------------------------------ MQTT
    def connect(self, ip, serial, code, timeout=8):
        self.disconnect()
        if not (ip and serial and code):
            raise ValueError("Set the printer's IP address, serial number and access code (Printer > Connection)")
        self.ip, self.serial, self.code = ip.strip(), serial.strip(), code.strip()
        self.error, self.state, self.connected = "", {}, False
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"nonplanar-{int(time.time())}")
        c.username_pw_set("bblp", self.code)
        c.tls_set_context(_insecure_ctx())
        c.on_connect = self._on_connect
        c.on_message = self._on_message
        c.on_disconnect = self._on_disconnect
        self.client = c
        c.connect_async(self.ip, 8883, keepalive=30)
        c.loop_start()
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.connected or self.error:
                break
            time.sleep(0.1)
        if not self.connected:
            err = self.error or "No answer from the printer (check the IP and that it's on the same network)"
            self.disconnect()
            raise ConnectionError(err)

    def disconnect(self):
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.connected = False

    def _on_connect(self, client, userdata, flags, reason, props=None):
        if reason == 0 or str(reason) == "Success":
            self.connected = True
            client.subscribe(f"device/{self.serial}/report")
            self.publish({"pushing": {"sequence_id": "0", "command": "pushall"}})
        else:
            self.error = f"Printer refused the connection ({reason}). Check the access code and serial number."

    def _on_disconnect(self, client, userdata, flags, reason, props=None):
        self.connected = False

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except ValueError:
            return
        p = data.get("print")
        if isinstance(p, dict):
            if p.get("command") == "project_file" and ("result" in p or "reason" in p):
                self.reply = dict(p)          # the printer's answer to our start command
            with self.lock:
                for k, v in p.items():       # reports are incremental: merge them
                    if isinstance(v, dict) and isinstance(self.state.get(k), dict):
                        self.state[k] = {**self.state[k], **v}
                    else:
                        self.state[k] = v
                self.last_update = time.time()

    def publish(self, payload):
        if not self.client:
            raise ConnectionError("Not connected to a printer")
        self.client.publish(f"device/{self.serial}/request", json.dumps(payload))

    def status(self):
        with self.lock:
            s = dict(self.state)
        keys = ["gcode_state", "mc_percent", "mc_remaining_time", "layer_num", "total_layer_num",
                "nozzle_temper", "nozzle_target_temper", "bed_temper", "bed_target_temper",
                "subtask_name", "print_error", "wifi_signal", "spd_lvl", "cooling_fan_speed", "hms"]
        info = s.get("ams") if isinstance(s.get("ams"), dict) else {}
        try:
            tray_now = int(info.get("tray_now", 255))
        except (TypeError, ValueError):
            tray_now = 255
        hf = s.get("home_flag")
        return {"connected": self.connected, "ip": self.ip, "serial": self.serial, "kind": "bambu",
                "age_s": round(time.time() - self.last_update, 1) if self.last_update else None,
                "ams": self._ams(s), "tray_now": tray_now,
                "auto_refill": bool((int(hf) >> 10) & 1) if isinstance(hf, (int, float)) else None,
                "runout": runout_code(s.get("print_error")), **{k: s.get(k) for k in keys}}

    @staticmethod
    def _ams(s):
        """AMS lite trays from the report: [{unit, tray, type, color, name, remain, active}]."""
        info = s.get("ams") if isinstance(s.get("ams"), dict) else {}
        now = str(info.get("tray_now", ""))
        out = []
        for unit in info.get("ams") or []:
            for tr in unit.get("tray") or []:
                try:
                    u, k = int(unit.get("id", 0)), int(tr.get("id", 0))
                except (TypeError, ValueError):
                    continue
                col = str(tr.get("tray_color") or "")
                out.append({"unit": u, "tray": k, "index": u * 4 + k, "type": tr.get("tray_type") or "",
                            "name": tr.get("tray_sub_brands") or "", "color": ("#" + col[:6]) if len(col) >= 6 else "",
                            "remain": tr.get("remain"), "active": now == str(u * 4 + k)})
        out.sort(key=lambda t: t["index"])
        return out

    # ------------------------------------------------------------ commands
    def command(self, name):
        cmds = {"pause": "pause", "resume": "resume", "stop": "stop"}
        if name in cmds:
            self.publish({"print": {"sequence_id": "0", "command": cmds[name]}})
        elif name == "light_on" or name == "light_off":
            self.publish({"system": {"sequence_id": "0", "command": "ledctrl", "led_node": "chamber_light",
                                     "led_mode": "on" if name == "light_on" else "off",
                                     "led_on_time": 500, "led_off_time": 500, "loop_times": 0, "interval_time": 0}})
        else:
            raise ValueError(f"Unknown command {name}")

    SPEEDS = {"silent": 1, "standard": 2, "sport": 3, "ludicrous": 4}

    def set_auto_refill(self, on):
        """The printer's own AMS auto refill: on a runout it switches to a slot with the same filament."""
        self.publish({"print": {"sequence_id": "0", "command": "print_option", "auto_switch_filament": bool(on)}})

    def load_slot(self, index, curr_temp, tar_temp):
        """Load AMS slot `index` (ams_id * 4 + slot, 0-based), like Bambu Studio's Load button."""
        ams_id, slot = divmod(int(index), 4)
        self.publish({"print": {"sequence_id": "0", "command": "ams_change_filament", "target": int(index),
                                "ams_id": ams_id, "slot_id": slot,
                                "curr_temp": int(curr_temp), "tar_temp": int(tar_temp)}})

    def gcode(self, line):
        """Run a G-code line on the printer (temperatures etc.)."""
        self.publish({"print": {"sequence_id": "0", "command": "gcode_line", "param": line.strip() + " \n"}})

    def set_temp(self, what, temp):
        t = int(round(float(temp)))
        if what == "nozzle":
            if not 0 <= t <= 300:
                raise ValueError("Nozzle temperature must be 0-300 °C")
            self.gcode(f"M104 S{t}")
        elif what == "bed":
            if not 0 <= t <= 110:
                raise ValueError("Bed temperature must be 0-110 °C")
            self.gcode(f"M140 S{t}")
        else:
            raise ValueError(what)

    def set_speed(self, level):
        lvl = self.SPEEDS.get(str(level).lower(), level)
        if lvl not in (1, 2, 3, 4):
            raise ValueError("Speed must be silent, standard, sport or ludicrous")
        self.publish({"print": {"sequence_id": "0", "command": "print_speed", "param": str(lvl)}})

    def _ftp(self, timeout=60):
        ftp = _ImplicitFTPS(context=_insecure_ctx(), timeout=timeout)
        ftp.connect(self.ip, 990)
        ftp.login("bblp", self.code)
        ftp.prot_p()
        return ftp

    @staticmethod
    def _bye(ftp):
        try:
            ftp.quit()
        except Exception:
            ftp.close()

    def remote_size(self, remote_name):
        """Size of a file on the printer's SD card (a fresh connection), or None."""
        try:
            ftp = self._ftp(timeout=20)
        except Exception:
            return None
        try:
            ftp.voidcmd("TYPE I")
            return ftp.size(remote_name)
        except Exception:
            return None
        finally:
            self._bye(ftp)

    def upload(self, local_path, remote_name=None):
        remote_name = remote_name or os.path.basename(local_path)
        size = os.path.getsize(local_path)
        ftp = self._ftp()
        try:
            with open(local_path, "rb") as f:
                ftp.storbinary(f"STOR {remote_name}", f)
        except (socket.timeout, TimeoutError, ftplib.error_temp, ssl.SSLError, ConnectionError) as e:
            # the data went out but the printer didn't confirm: check on a new connection that the file is complete
            ftp.close()
            got = None
            for _ in range(3):
                time.sleep(1.5)
                got = self.remote_size(remote_name)
                if got == size:
                    break
            if got != size:
                raise ConnectionError(f"Upload to the printer failed ({e}). Check the printer's SD card "
                                      "is inserted and has free space, then try again.") from e
            return remote_name
        self._bye(ftp)
        return remote_name

    def start_print(self, remote_name, plate=1, bed_leveling=True, use_ams=False, ams_mapping=None, timelapse=False,
                    flow_cali=False, vibration_cali=False, wait=25, bed_type="auto"):
        """Send the start command, then wait for the printer to answer or start.
        Raises with the printer's reason when it refuses or doesn't react."""
        with self.lock:
            before = (self.state.get("gcode_state"), self.state.get("subtask_name"))
            err0 = self.state.get("print_error") or 0
        self.reply = None
        seq = str(int(time.time()) % 100000)
        self.publish({"print": {
            "sequence_id": seq, "command": "project_file",
            "param": f"Metadata/plate_{plate}.gcode",
            "url": f"file:///sdcard/{remote_name}",
            "subtask_name": remote_name.replace(".gcode.3mf", ""),
            "project_id": "0", "profile_id": "0", "task_id": "0", "subtask_id": "0",
            "bed_type": bed_type, "timelapse": timelapse, "bed_leveling": bed_leveling,
            "flow_cali": bool(flow_cali), "vibration_cali": bool(vibration_cali), "layer_inspect": False,
            "use_ams": use_ams, "ams_mapping": (ams_mapping or [0]) if use_ams else [],
        }})
        sub = remote_name.replace(".gcode.3mf", "")
        t0 = time.time()
        while time.time() - t0 < wait:
            time.sleep(0.3)
            r = self.reply
            if r:
                res = str(r.get("result", "")).lower()
                if res and res != "success":
                    reason = r.get("reason") or r.get("err_code") or res
                    raise RuntimeError(f"The printer refused the print: {reason}")
            with self.lock:
                st, name = self.state.get("gcode_state"), self.state.get("subtask_name")
                err = self.state.get("print_error") or 0
            if err and err != err0:
                raise RuntimeError(f"The printer reported error {err:08X} when starting. Check its screen."
                                   if isinstance(err, int) else f"The printer reported error {err}. Check its screen.")
            if st in ("PREPARE", "RUNNING", "SLICING") and (name == sub or (st, name) != before):
                return st
            if not self.connected:
                raise ConnectionError("Lost the connection to the printer while starting the print")
        if self.reply and str(self.reply.get("result", "")).lower() == "success":
            return "accepted"
        raise TimeoutError(
            "The file was uploaded, but the printer didn't start it. Look at the printer's screen for a message. "
            "On newer Bambu firmware, LAN printing from other apps needs LAN Only Mode and Developer Mode "
            "turned on (printer Settings > Network/LAN). You can also start the file from the printer's SD card list.")
