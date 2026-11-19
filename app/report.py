"""Report a problem: a small .zip with what the developer needs, and a ready email.

Nothing is sent by the app. It writes Documents\\ArcLayer\\reports\\ArcLayer-report-<time>.zip, opens that
folder, and opens a new email in the person's own mail app (they see it, attach the file and press Send).

Private data never goes in: printer IP addresses, serial numbers, access codes, API keys, passwords, camera
addresses (with their login), e-mail addresses and the Windows user name are blanked out of everything.
"""
import io
import json
import os
import platform
import re
import time
import urllib.parse
import zipfile

from . import paths, profiles

# where reports go. Empty: the app still makes the .zip and says to send it in.
REPORT_EMAIL = "arclayer@hotmail.com"

PRIVATE_KEYS = ("ip", "serial", "access_code", "host", "api_key", "host_password", "camera_url", "password",
                "user", "supporter_code", "sync_folder", "folder", "email")
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_URL_LOGIN = re.compile(r"([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _secrets():
    """Exact private values to blank: what is in the printer presets and the app settings."""
    vals = set()
    try:
        for p in profiles.list_all().get("printer", {}).values():
            for k in ("ip", "serial", "access_code", "host", "api_key", "host_password"):
                v = str(p.get(k) or "").strip()
                if len(v) >= 4:
                    vals.add(v)
    except Exception:
        pass
    try:
        with open(os.path.join(paths.DATA, "app_settings.json"), encoding="utf-8") as f:
            st = json.load(f)
        for k in ("camera_url", "supporter_code", "sync_folder"):
            v = str(st.get(k) or "").strip()
            if len(v) >= 4:
                vals.add(v)
        cam = st.get("camera") or {}
        if isinstance(cam, dict):
            for v in cam.values():
                if isinstance(v, str) and len(v) >= 4:
                    vals.add(v)
    except Exception:
        pass
    return sorted(vals, key=len, reverse=True)


def _user_re():
    """The Windows user name as a whole folder name (C:\\Users\\<name>\\...), so "Users" itself stays."""
    user = os.environ.get("USERNAME") or os.path.basename(os.path.expanduser("~"))
    return re.compile(r"([\\/])" + re.escape(user) + r"(?=[\\/]|$)", re.I) if user else None


def scrub(text, secrets=None):
    secrets = _secrets() if secrets is None else secrets
    for v in secrets:
        text = text.replace(v, "<private>")
    u = _user_re()
    if u:
        text = u.sub(r"\1<user>", text)
    text = _URL_LOGIN.sub(r"\1<login>@", text)
    text = _EMAIL.sub("<email>", text)
    return _IP.sub("<ip>", text)


def _clean(obj):
    if isinstance(obj, dict):
        return {k: ("<private>" if k.lower() in PRIVATE_KEYS and obj[k] not in ("", None, 0, False) else _clean(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def _log_tail(n=400):
    try:
        with open(paths.LOG, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return "(no log file)"


def system_info(extra=None):
    info = {"app": "ArcLayer", "version": paths.VERSION, "installed": paths.FROZEN,
            "windows": platform.platform(), "python": platform.python_version(), "time": time.strftime("%Y-%m-%d %H:%M")}
    info.update(extra or {})
    return info


def build(req, extra_info=None, settings=None):
    """req: {what, expected, steps, include_log, include_settings, picture (PNG bytes or None)}.
    Returns {"path", "folder", "mailto", "email", "summary"}."""
    secrets = _secrets()
    what = scrub(str(req.get("what") or "").strip(), secrets)[:4000]
    expected = scrub(str(req.get("expected") or "").strip(), secrets)[:2000]
    info = system_info(extra_info)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(paths.OUTPUT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"ArcLayer-report-{stamp}.zip")
    included = ["what happened", "app and Windows version"]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.txt", f"What happened:\n{what}\n\nWhat I expected:\n{expected or '-'}\n\n"
                                 + "\n".join(f"{k}: {v}" for k, v in info.items()) + "\n")
        if req.get("include_log", True):
            z.writestr("app.log", scrub(_log_tail(), secrets))
            included.append("the last part of the app log")
        if req.get("include_settings", True) and settings:
            z.writestr("settings.json", scrub(json.dumps(_clean(settings), indent=1, default=str), secrets))
            included.append("the printer / filament / process settings in use (no connection details)")
        if req.get("picture"):
            z.writestr("plate.png", req["picture"])
            included.append("a picture of the plate")
    subject = f"ArcLayer {paths.VERSION}: " + (what.splitlines()[0][:70] if what else "problem report")
    body = (f"{what}\n\nExpected: {expected or '-'}\n\nArcLayer {paths.VERSION} · {info['windows']}\n\n"
            f"Please attach this file (its folder just opened):\n{os.path.basename(path)}\n")
    mailto = ("mailto:" + REPORT_EMAIL + "?" + urllib.parse.urlencode(
        {"subject": subject, "body": body[:1500]}, quote_via=urllib.parse.quote)) if REPORT_EMAIL else ""
    return {"path": path, "folder": out_dir, "mailto": mailto, "email": REPORT_EMAIL, "included": included,
            "name": os.path.basename(path)}
