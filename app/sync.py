"""Keep presets and settings the same on several PCs through a shared folder (OneDrive, Google Drive, Dropbox…).

No account and no server: each PC copies its presets into <folder>/NonPlanar Slicer sync and takes newer ones from
it. The newer file wins. A deleted preset is remembered (deleted.json) so it is deleted on the other PCs too.
A few app settings (lists shown, runout order, plate, camera, send options) travel in settings.json.
"""
import json
import os
import shutil
import threading
import time

SHARED_KEYS = ("visible", "runout", "send_options", "plate", "camera_url", "supporter_code")
SUB = "NonPlanar Slicer sync"
SLACK = 2.0          # seconds: cloud folders round file times


def suggestions():
    """Cloud folders found on this PC."""
    home = os.path.expanduser("~")
    out = []
    for label, path in (("OneDrive", os.environ.get("OneDrive")), ("OneDrive", os.path.join(home, "OneDrive")),
                        ("Dropbox", os.path.join(home, "Dropbox")), ("Google Drive", "G:\\My Drive"),
                        ("Google Drive", os.path.join(home, "Google Drive")), ("iCloud Drive", os.path.join(home, "iCloudDrive"))):
        if path and os.path.isdir(path) and all(p != path for _, p in out):
            out.append((label, path))
    return [{"label": a, "path": b} for a, b in out]


class Sync:
    def __init__(self, profiles_dir, get_settings, set_settings, log=print):
        self.root = profiles_dir
        self.get, self.set, self.log = get_settings, set_settings, log
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.last = {"time": None, "pulled": 0, "pushed": 0, "deleted": 0, "error": ""}
        self.changes = 0                    # bumps when something came from another PC (the page reloads presets)

    # ------------------------------------------------------------ paths
    def folder(self):
        d = (self.get().get("sync_dir") or "").strip()
        return os.path.join(d, SUB) if d else ""

    def _local_files(self):
        out = {}
        for sub in ("printer", "filament", "process", "templates"):
            d = os.path.join(self.root, sub)
            if not os.path.isdir(d):
                continue
            for fn in os.listdir(d):
                p = os.path.join(d, fn)
                if os.path.isfile(p) and (fn.endswith(".json") or sub == "templates") and not fn.endswith((".bak", ".tmp")):
                    out[f"{sub}/{fn}"] = p
        return out

    def _remote_files(self, base):
        out = {}
        for sub in ("printer", "filament", "process", "templates"):
            d = os.path.join(base, "profiles", sub)
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    p = os.path.join(d, fn)
                    if os.path.isfile(p) and not fn.endswith((".bak", ".tmp")):
                        out[f"{sub}/{fn}"] = p
        return out

    @staticmethod
    def _read(p, default):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    @staticmethod
    def _write(p, data):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, p)

    @staticmethod
    def _copy(src, dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".tmp"
        shutil.copy2(src, tmp)              # keeps the file time: the newer one keeps winning
        os.replace(tmp, dst)

    # ------------------------------------------------------------ one pass
    def run(self):
        base = self.folder()
        if not base:
            return self.last
        with self.lock:
            pulled = pushed = deleted = 0
            try:
                os.makedirs(base, exist_ok=True)
                tomb_p = os.path.join(base, "deleted.json")
                tomb = self._read(tomb_p, {})
                loc, rem = self._local_files(), self._remote_files(base)
                for rel in sorted(set(loc) | set(rem) | set(tomb)):
                    lp = loc.get(rel) or os.path.join(self.root, *rel.split("/"))
                    rp = rem.get(rel) or os.path.join(base, "profiles", *rel.split("/"))
                    lt = os.path.getmtime(lp) if os.path.exists(lp) else None
                    rt = os.path.getmtime(rp) if os.path.exists(rp) else None
                    dt = tomb.get(rel)
                    if dt and (lt is None or lt <= dt + SLACK) and (rt is None or rt <= dt + SLACK):
                        for p in (lp, rp):          # deleted on some PC after the last change: delete everywhere
                            if os.path.exists(p):
                                os.replace(p, p + ".deleted.bak") if p == lp else os.remove(p)
                                deleted += p == lp
                        continue
                    if lt is not None and (rt is None or lt > rt + SLACK):
                        self._copy(lp, rp)
                        pushed += 1
                    elif rt is not None and (lt is None or rt > lt + SLACK):
                        if lt is not None:
                            shutil.copy2(lp, lp + ".before-sync.bak")
                        self._copy(rp, lp)
                        pulled += 1
                # app settings
                set_p = os.path.join(base, "settings.json")
                remote = self._read(set_p, {})
                local = self.get()
                lt, rt = float(local.get("shared_time") or 0), float(remote.get("time") or 0)
                if lt > rt + 0.5:
                    self._write(set_p, {"time": lt, "values": {k: local[k] for k in SHARED_KEYS if k in local}})
                    pushed += 1
                elif rt > lt + 0.5:
                    vals = {k: v for k, v in (remote.get("values") or {}).items() if k in SHARED_KEYS}
                    self.set(_from_sync=True, shared_time=rt, **vals)
                    pulled += 1
                if pulled or deleted:
                    self.changes += 1
                self.last = {"time": time.time(), "pulled": pulled, "pushed": pushed, "deleted": deleted, "error": ""}
            except Exception as e:
                self.last = {**self.last, "time": time.time(), "error": str(e)}
                self.log(f"sync: {e}")
        return self.last

    def deleted(self, rel):
        """A preset was deleted here: remember it so the other PCs delete it too."""
        base = self.folder()
        if not base:
            return
        with self.lock:
            tomb_p = os.path.join(base, "deleted.json")
            tomb = self._read(tomb_p, {})
            tomb[rel] = time.time()
            try:
                self._write(tomb_p, tomb)
                rp = os.path.join(base, "profiles", *rel.split("/"))
                if os.path.exists(rp):
                    os.remove(rp)
            except OSError as e:
                self.log(f"sync: {e}")

    def kick(self):
        self.wake.set()

    def loop(self):
        time.sleep(3)
        while True:
            if self.folder():
                self.run()
            self.wake.wait(60)
            self.wake.clear()
            time.sleep(1)                   # let a burst of saves finish
