"""Where the app keeps its files.

Run from source: everything sits next to the code (profiles/, workspace/, output/).
Installed (frozen exe): the program folder is left alone. Presets and imported models go to
%APPDATA%\\ArcLayer and exported prints to Documents\\ArcLayer.

The app was called "NonPlanar Slicer" up to 0.7.20. On the first start of ArcLayer the old data folder is
copied to the new one (the old folder stays as it was, as a backup).
"""
import os
import shutil
import sys

FROZEN = getattr(sys, "frozen", False)
APP_NAME = "ArcLayer"
OLD_NAME = "NonPlanar Slicer"            # up to 0.7.20

if FROZEN:
    RESOURCES = sys._MEIPASS                                     # bundled read-only files (ui/)
    DATA = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
    OUTPUT = os.path.join(os.path.expanduser("~"), "Documents", APP_NAME)
    _OLD_DATA = os.path.join(os.path.dirname(DATA), OLD_NAME)
else:
    RESOURCES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DATA = RESOURCES
    OUTPUT = os.path.join(RESOURCES, "output")

UI = os.path.join(RESOURCES, "app", "ui")
PROFILES = os.path.join(DATA, "profiles")
WORK = os.path.join(DATA, "workspace")
LOG = os.path.join(DATA, "app.log")


def migrate_old_name():
    """First start after the rename: copy the old data folder (%APPDATA%, "NonPlanar Slicer") to the new one.
    Copy, never move: the old folder stays untouched, so nothing can be lost. Returns True if it copied."""
    if not FROZEN or os.path.exists(DATA) or not os.path.isdir(_OLD_DATA):
        return False
    tmp = DATA + ".copying"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(_OLD_DATA, tmp, ignore=shutil.ignore_patterns("instance.json", "*.lock"))
    os.replace(tmp, DATA)                  # all or nothing: a half copy never becomes the data folder
    return True


def _version():
    for p in (os.path.join(RESOURCES, "version.txt"), os.path.join(RESOURCES, "installer", "version.txt")):
        try:
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            pass
    return "dev"


VERSION = _version()
