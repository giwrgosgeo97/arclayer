"""Cameras for the Device tab, served to the page as MJPEG.

* Bambu A1 / P1 camera: TLS on port 6000, an 80-byte login (user "bblp" + access code),
  then a stream of JPEG frames, each after a 16-byte header whose first 4 bytes are the size.
* Bambu X1 / X1E / H2D camera: RTSP over TLS, rtsps://bblp:<access code>@<ip>:322/streaming/live/1.
* Any other camera (a Tapo, an IP camera, a webcam server): an RTSP or HTTP URL,
  decoded with PyAV (FFmpeg) and re-encoded as JPEG.

A camera runs only while someone is watching it, and stops a few seconds after the last viewer leaves.
"""
import socket
import ssl
import struct
import threading
import time


class _Feed:
    def __init__(self):
        self.frame = None            # latest JPEG bytes
        self.seq = 0
        self.error = ""
        self.viewers = 0
        self.last_view = 0.0
        self.cond = threading.Condition()
        self.thread = None
        self.stop = False

    def put(self, jpg):
        with self.cond:
            self.frame, self.seq, self.error = jpg, self.seq + 1, ""
            self.cond.notify_all()

    def fail(self, msg):
        with self.cond:
            self.error = msg
            self.cond.notify_all()

    def wait(self, seq, timeout=5.0):
        with self.cond:
            self.cond.wait_for(lambda: self.seq != seq or self.stop, timeout)
            return self.frame, self.seq

    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def ensure(self, target):
        """Start the reader thread if it isn't running."""
        self.last_view = time.time()
        if not self.running():
            self.stop = False
            self.thread = threading.Thread(target=target, daemon=True)
            self.thread.start()

    def idle(self):
        return self.viewers <= 0 and time.time() - self.last_view > 8


def _ctx():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


class Cameras:
    def __init__(self):
        self.feeds = {"printer": _Feed(), "extra": _Feed()}
        self.printer = None          # (ip, access_code, model)
        self.extra_url = ""

    # ------------------------------------------------------------ Bambu camera
    def _bambu_loop(self):
        f = self.feeds["printer"]
        tries = 0
        while not f.stop and not f.idle():
            ip, code, model = (tuple(self.printer) + (None,) * 3)[:3] if self.printer else (None, None, None)
            if ip == "url":                              # Klipper / OctoPrint printer: its webcam stream
                url = code
                if not url:
                    f.fail("This printer has no camera set up")
                    time.sleep(3)
                    continue
                self._decode(f, lambda: url, "Printer camera", lambda: self.printer and self.printer[1] == url, once=True)
                continue
            if not ip:
                f.fail("Connect to the printer first")
                time.sleep(2)
                continue
            # X1 / X1E / H2D stream RTSP on 322; A1 / P1 send JPEGs on 6000. Unknown models try both.
            rtsp = (model or "") in RTSP_MODELS or ((model or "") not in JPEG_MODELS and tries % 2 == 1)
            if rtsp:
                tries += 1
                self._decode(f, lambda: f"rtsps://bblp:{code}@{ip}:322/streaming/live/1", "Printer camera",
                             lambda: self.printer and self.printer[0] == ip, once=True)
                continue
            try:
                raw = socket.create_connection((ip, 6000), timeout=10)
                s = _ctx().wrap_socket(raw, server_hostname=ip)
                s.settimeout(10)
                auth = struct.pack("<IIII", 0x40, 0x3000, 0, 0)
                auth += b"bblp".ljust(32, b"\0") + code.encode()[:32].ljust(32, b"\0")
                s.sendall(auth)
                while not f.stop and not f.idle():
                    head = _read(s, 16)
                    size = struct.unpack("<I", head[:4])[0]
                    if not 0 < size < 8_000_000:
                        raise ConnectionError("bad frame size")
                    jpg = _read(s, size)
                    if jpg[:2] == b"\xff\xd8":
                        f.put(jpg)
                s.close()
            except (OSError, ConnectionError, ssl.SSLError) as e:
                tries += 1
                f.fail(f"Printer camera: {e}. On the printer, turn on LAN Only Liveview (Settings > Network).")
                time.sleep(3)

    # ------------------------------------------------------------ RTSP / HTTP camera
    def _url_loop(self):
        f = self.feeds["extra"]
        while not f.stop and not f.idle():
            if not self.extra_url:
                f.fail("Set the camera's address first")
                time.sleep(2)
                continue
            url = self.extra_url
            self._decode(f, lambda: url, "My camera", lambda: url == self.extra_url)

    def _decode(self, f, get_url, label, still_wanted, once=False):
        """Read an RTSP / HTTP stream with PyAV and put JPEG frames into the feed."""
        while not f.stop and not f.idle() and still_wanted():
            url = get_url()
            try:
                import av
                opts = {"rtsp_transport": "tcp", "stimeout": "8000000", "timeout": "8000000"} \
                    if url.lower().startswith("rtsp") else {"timeout": "8000000"}
                with av.open(url, options=opts, timeout=10) as box:
                    vs = box.streams.video[0]
                    vs.thread_type = "AUTO"
                    sar = getattr(vs, "sample_aspect_ratio", None) or getattr(vs.codec_context, "sample_aspect_ratio", None)
                    last = 0.0
                    for frame in box.decode(vs):
                        if f.stop or f.idle() or not still_wanted():
                            break
                        now = time.time()
                        if now - last < 1 / 12:            # about 12 frames a second is plenty
                            continue
                        last = now
                        f.put(_jpeg(frame, sar))
            except Exception as e:                       # bad address, wrong password, camera offline
                msg = friendly_error(e, url)
                if label == "Printer camera":
                    msg += ". On the printer, turn on LAN Only Liveview (Settings > Network)"
                f.fail(f"{label}: {msg}")
                time.sleep(3)
            if once:
                return

    # ------------------------------------------------------------ serving
    def stream(self, name, write):
        """Write a multipart MJPEG stream until the viewer goes away. write(bytes) raises when it does."""
        f = self.feeds[name]
        f.ensure(self._bambu_loop if name == "printer" else self._url_loop)
        f.viewers += 1
        seq = -1
        try:
            while True:
                frame, seq2 = f.wait(seq)
                f.last_view = time.time()
                if frame is None or seq2 == seq:
                    if not f.running():
                        f.ensure(self._bambu_loop if name == "printer" else self._url_loop)
                    continue
                seq = seq2
                write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame)).encode()
                      + b"\r\n\r\n" + frame + b"\r\n")
        finally:
            f.viewers -= 1
            f.last_view = time.time()

    def status(self):
        return {k: {"on": f.running(), "error": f.error, "frames": f.seq} for k, f in self.feeds.items()}

    def restart(self, name):
        f = self.feeds[name]
        with f.cond:
            f.frame, f.error = None, ""
        f.stop = True
        if f.thread:
            f.thread.join(timeout=3)
        f.stop = False


RTSP_MODELS = {"Bambu Lab X1 Carbon", "Bambu Lab X1", "Bambu Lab X1E", "Bambu Lab H2D", "Bambu Lab H2D Pro",
               "Bambu Lab H2S", "Bambu Lab H2C"}
JPEG_MODELS = {"Bambu Lab A1", "Bambu Lab A1 mini", "Bambu Lab P1P", "Bambu Lab P1S"}


def _jpeg(frame, sar=None, max_w=1280):
    """One decoded video frame as JPEG bytes, at its true shape (cameras with non-square pixels are corrected).
    Pillow does the JPEG: FFmpeg's encoder refuses frames whose timestamps repeat or go back, which cameras send."""
    import io
    w, h = frame.width, frame.height
    sar = sar or getattr(frame, "sample_aspect_ratio", None)
    if sar and float(sar) > 0 and abs(float(sar) - 1) > 0.01:
        w = int(round(w * float(sar)))
    if w > max_w:
        h, w = int(round(h * max_w / w)), max_w
    img = frame.to_image()                                # RGB
    if img.size != (w, h):
        img = img.resize((w, h))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def friendly_error(e, url):
    """A camera error in plain words, without the address (it holds the password)."""
    import re
    import urllib.parse
    msg = str(e) or type(e).__name__
    host = urllib.parse.urlsplit(url).hostname or "the camera"
    if "401" in msg or "Unauthorized" in msg or "403" in msg:
        msg = ("wrong user name or password. Tapo: use the Camera Account you made in the Tapo app "
               "(camera > Settings > Advanced Settings > Camera Account), not your Tapo / TP-Link login")
    elif "-138" in msg or "timed out" in msg.lower() or "refused" in msg.lower() or "10061" in msg or "10060" in msg:
        msg = (f"can't reach {host}. Check the IP address, that the camera is on the same Wi-Fi, "
               "and (newer Tapo firmware) that Third-Party Compatibility is on in the Tapo app (Me > Tapo Lab)")
    elif "404" in msg or "not found" in msg.lower():
        msg = "the camera has no stream at that address (Tapo: /stream1 or /stream2)"
    return re.sub(r"[a-z]+://[^\s'\"]+", "<address>", msg)     # never show the password


def test_url(url, timeout=12):
    """Open a camera and read one picture. Returns (True, "WxH") or (False, reason)."""
    try:
        import av
        opts = {"rtsp_transport": "tcp", "stimeout": "8000000", "timeout": "8000000"}             if url.lower().startswith("rtsp") else {"timeout": "8000000"}
        with av.open(url, options=opts, timeout=timeout) as box:
            for frame in box.decode(box.streams.video[0]):
                return True, f"{frame.width}×{frame.height}"
        return False, "the camera sent no picture"
    except Exception as e:
        return False, friendly_error(e, url)


def _local_networks():
    """This computer's IPv4 addresses on private networks (e.g. 192.168.1.23)."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:                                           # the address used to reach the internet
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.connect(("10.255.255.255", 1))
        ips.add(u.getsockname()[0])
        u.close()
    except OSError:
        pass
    return sorted(i for i in ips if i.startswith(("192.168.", "10.")) or
                  (i.startswith("172.") and 16 <= int(i.split(".")[1]) <= 31))


def scan_rtsp(skip=(), port=554, timeout=0.6):
    """Addresses on this computer's networks (the /24 around it) that answer on the RTSP port."""
    from concurrent.futures import ThreadPoolExecutor
    hosts = []
    for me in _local_networks():
        base = me.rsplit(".", 1)[0]
        hosts += [f"{base}.{i}" for i in range(1, 255) if f"{base}.{i}" not in (me, *skip)]

    def probe(h):
        try:
            with socket.create_connection((h, port), timeout=timeout) as c:
                c.settimeout(timeout)
                c.sendall(f"DESCRIBE rtsp://{h}:{port}/stream1 RTSP/1.0\r\nCSeq: 1\r\n\r\n".encode())
                try:
                    head = c.recv(512).decode("latin-1")
                except OSError:
                    head = ""
                server = ""
                for line in head.splitlines():
                    if line.lower().startswith("server:"):
                        server = line.split(":", 1)[1].strip()
                    elif 'realm="' in line and not server:
                        server = line.split('realm="', 1)[1].split('"')[0]
                tapo = "tp-link" in server.lower() or "tapo" in server.lower()
                return {"ip": h, "server": server, "tapo": tapo,
                        "name": "Tapo camera" if tapo else (server or "camera")}
        except OSError:
            return None
    with ThreadPoolExecutor(64) as ex:
        found = [r for r in ex.map(probe, dict.fromkeys(hosts)) if r]
    found.sort(key=lambda r: not r["tapo"])
    return found


def _read(s, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("the camera closed the connection")
        buf += chunk
    return bytes(buf)
