"""Supporter codes: hide the "support the project" reminder.

A code is NPS-XXXX-XXXX-XXXX-XXXX: 4 random bytes plus a 6-byte HMAC of them, in base32.
The app checks the HMAC offline, so no server is needed. Anyone who has a code can share it,
but a made-up code doesn't work. Make new codes with:  python make_supporter_code.py [count]

The keys live in app/supporter_key.txt, which is not in git. Removing a key there makes the codes
made with it stop working (do that if a code gets posted publicly).
"""
import base64
import hashlib
import hmac
import os
import secrets

from . import paths

def _keys():
    """The keys, from app/supporter_key.txt (not in git; the installer bundles it) or $ARCLAYER_SUPPORTER_KEYS."""
    lines = os.environ.get("ARCLAYER_SUPPORTER_KEYS", "").split(",")
    for p in (os.path.join(paths.RESOURCES, "app", KEY_FILE), os.path.join(os.path.dirname(os.path.abspath(__file__)), KEY_FILE)):
        try:
            with open(p, encoding="utf-8") as f:
                lines += f.read().splitlines()
            break
        except OSError:
            continue
    out = []
    for ln in lines:
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            try:
                out.append(bytes.fromhex(ln))
            except ValueError:
                pass
    return out


KEY_FILE = "supporter_key.txt"
KEYS = _keys()
PREFIX = "NPS"


def _mac(body, key=None):
    if key is None:
        if not KEYS:
            raise RuntimeError("No supporter key: app/supporter_key.txt is missing")
        key = KEYS[0]
    return hmac.new(key, b"nonplanar-supporter|" + body, hashlib.sha256).digest()[:6]


def make_code():
    body = secrets.token_bytes(4)
    s = base64.b32encode(body + _mac(body)).decode()          # 10 bytes -> 16 characters
    return PREFIX + "-" + "-".join(s[i:i + 4] for i in range(0, 16, 4))


def normalize(code):
    s = "".join(ch for ch in str(code).upper() if ch.isalnum())
    if s.startswith(PREFIX):
        s = s[len(PREFIX):]
    # characters base32 doesn't use, typed by mistake
    return s.translate(str.maketrans({"0": "O", "1": "I", "8": "B", "9": "G"}))


def check(code):
    s = normalize(code)
    if len(s) != 16:
        return False
    try:
        raw = base64.b32decode(s)
    except (ValueError, base64.binascii.Error):
        return False
    return any(hmac.compare_digest(raw[4:], _mac(raw[:4], k)) for k in KEYS)
