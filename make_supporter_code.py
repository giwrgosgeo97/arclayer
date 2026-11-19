"""Prints new supporter codes for ArcLayer (put one in your Ko-fi thank-you message,
or send one to a supporter yourself).   python make_supporter_code.py [how many]
python make_supporter_code.py --new-key   puts a fresh key at the top of app/supporter_key.txt (new codes use it;
codes made with the keys below it keep working until you delete those lines)."""
import os
import secrets
import sys

from app import supporter

if "--new-key" in sys.argv:
    p = os.path.join(os.path.dirname(os.path.abspath(supporter.__file__)), supporter.KEY_FILE)
    old = open(p, encoding="utf-8").read().splitlines() if os.path.exists(p) else []
    head = [ln for ln in old if ln.startswith("#")]
    keys = [ln for ln in old if ln.strip() and not ln.startswith("#")]
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(head + [secrets.token_hex(32)] + keys) + "\n")
    print("new key written to", p)
    sys.exit(0)

for _ in range(int(sys.argv[1]) if len(sys.argv) > 1 else 1):
    code = supporter.make_code()
    assert supporter.check(code)
    print(code)
