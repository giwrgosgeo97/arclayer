"""Makes new supporter codes for ArcLayer:   python make_supporter_code.py [how many]

The codes go into supporter_codes.txt (private, not in git, not in the installer: keep a backup) and their
hashes into app/supporter_hashes.py (public). A code works in releases built after this, so some spare codes
are made ahead: to change the code in the Ko-fi thank-you message, take the next spare one."""
import os
import secrets
import sys

from app import supporter

ROOT = os.path.dirname(os.path.abspath(__file__))
CODES = os.path.join(ROOT, "supporter_codes.txt")
HASHES = os.path.join(ROOT, "app", "supporter_hashes.py")


def new_code():
    s = "".join(secrets.choice(supporter.ALPHABET) for _ in range(16))
    return supporter.PREFIX + "-" + "-".join(s[i:i + 4] for i in range(0, 16, 4))


def add(codes):
    hashes = list(supporter.HASHES)
    with open(CODES, "a", encoding="utf-8") as f:
        for c in codes:
            h = supporter.digest(supporter.normalize(c))
            if h not in hashes:
                hashes.append(h)
                f.write(c + "\n")
    with open(HASHES, "w", encoding="utf-8", newline="\n") as f:
        f.write('"""Hashes of the valid supporter codes, written by make_supporter_code.py. They can\'t be turned back into codes."""\n'
                "HASHES = (\n" + "".join(f'    "{h}",\n' for h in hashes) + ")\n")


if __name__ == "__main__":
    new = [new_code() for _ in range(int(sys.argv[1]) if len(sys.argv) > 1 else 1)]
    add(new)
    print(*new, sep="\n")
    print("Saved in", CODES)
