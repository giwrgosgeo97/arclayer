"""Supporter codes: hide the "support the project" reminder.

A code is NPS-XXXX-XXXX-XXXX-XXXX (80 bits in base32). The app holds only a slow hash of each valid code
(app/supporter_hashes.py), so nothing in the app or the installer can be used to make a code. Codes are checked
offline, no server is needed. Anyone who has a code can share it, but a made-up code doesn't work.

New codes: python make_supporter_code.py [count]. A code works from the next release on; a release without
its hash stops accepting it (do that if a code gets posted publicly).
"""
import functools
import hashlib
import hmac

from .supporter_hashes import HASHES

PREFIX = "NPS"
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def normalize(code):
    s = "".join(ch for ch in str(code).upper() if ch.isalnum())
    if s.startswith(PREFIX) and len(s) > 16:
        s = s[len(PREFIX):]
    # characters base32 doesn't use, typed by mistake
    return s.translate(str.maketrans({"0": "O", "1": "I", "8": "B", "9": "G"}))


@functools.lru_cache(maxsize=32)
def digest(s):
    """The hash of a normalized code. Slow on purpose (about 0.1 s, 32 MB), so guessing codes isn't worth it."""
    return hashlib.scrypt(s.encode(), salt=b"arclayer-supporter", n=2 ** 15, r=8, p=1, maxmem=2 ** 26, dklen=16).hex()


def check(code):
    s = normalize(code)
    if len(s) != 16 or any(c not in ALPHABET for c in s):
        return False
    d = digest(s)
    return any(hmac.compare_digest(d, h) for h in HASHES)
