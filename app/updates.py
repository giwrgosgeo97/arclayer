"""Is there a newer ArcLayer? Asks GitHub for the latest release, at most once a day.

Only the release list is fetched (no data about the user or the PC goes out). If GitHub can't be reached,
or the repository is still private, nothing happens.
"""
import json
import os
import threading
import time
import urllib.request

from . import paths

REPO = "giwrgosgeo97/arclayer"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
CACHE = os.path.join(paths.DATA, "update_check.json")
STATE = {"latest": None, "url": PAGE, "notes": "", "checked": 0}


def _num(v):
    out = []
    for part in str(v).lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out + [0] * (4 - len(out)))


def newer(latest, current=None):
    current = current or paths.VERSION
    return bool(latest) and current != "dev" and _num(latest) > _num(current)


def check(force=False):
    try:
        with open(CACHE, encoding="utf-8") as f:
            STATE.update(json.load(f))
    except (OSError, ValueError):
        pass
    if not force and time.time() - STATE.get("checked", 0) < 20 * 3600:
        return STATE
    try:
        req = urllib.request.Request(API, headers={"User-Agent": "ArcLayer", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.load(r)
        STATE.update(latest=str(d.get("tag_name") or "").lstrip("vV"), url=d.get("html_url") or PAGE,
                     notes=(d.get("body") or "")[:1500])
    except Exception as e:                  # offline, private repository, rate limit: try again next start
        print("update check:", e)
    STATE["checked"] = time.time()
    try:
        os.makedirs(paths.DATA, exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(STATE, f)
    except OSError:
        pass
    return STATE


def start():
    threading.Thread(target=check, daemon=True).start()


def status():
    return {"current": paths.VERSION, "latest": STATE.get("latest"), "url": STATE.get("url") or PAGE,
            "notes": STATE.get("notes", ""), "newer": newer(STATE.get("latest"))}
