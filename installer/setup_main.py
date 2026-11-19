"""ArcLayer installer / uninstaller (built into ArcLayer-Setup-x.y.z.exe).

Per-user install, so no admin rights are needed:
  program   -> %LOCALAPPDATA%\\Programs\\ArcLayer   (changeable)
  shortcuts -> Start Menu (always), Desktop (optional)
  uninstall -> listed in Windows "Installed apps" (HKCU)

File types: .3mf and .stl open with the app (per user, HKCU\\Software\\Classes). Windows doesn't let a
program take over a file type the user already picked another app for: then the app is only listed
under "Open with", and the finish page says how to make it the default.

Command line (also used for testing):
  Setup.exe --silent [--dir PATH] [--no-desktop] [--no-launch] [--no-associate]
  Uninstall.exe --uninstall [--silent] [--purge]     (--purge also deletes presets in %APPDATA%)
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

APP = "ArcLayer"
EXE = "ArcLayer.exe"
UNINST = "Uninstall.exe"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ArcLayer"
PUBLISHER = "ArcLayer"

HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
PAYLOAD = os.path.join(HERE, "payload.zip")
VERSION_FILE = os.path.join(HERE, "version.txt")
VERSION = open(VERSION_FILE).read().strip() if os.path.exists(VERSION_FILE) else "dev"

DEFAULT_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Programs", APP)
DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP)
NO_WINDOW = 0x08000000

# file types the app opens: extension -> (ProgID, description)
FILE_TYPES = {".3mf": ("ArcLayer.3mf", "3MF model or project"),
              ".stl": ("ArcLayer.stl", "STL model")}
CAPS_KEY = r"Software\ArcLayer\Capabilities"
APP_KEY = "Software\\Classes\\Applications\\" + EXE

# the same app under its old name (up to 0.7.20): removed when ArcLayer is installed (the data is kept)
OLD = {"app": "NonPlanar Slicer", "exe": "NonPlanar Slicer.exe",
       "reg": r"Software\Microsoft\Windows\CurrentVersion\Uninstall\NonPlanarSlicer",
       "types": {".3mf": "NonPlanarSlicer.3mf", ".stl": "NonPlanarSlicer.stl"},
       "caps": r"Software\NonPlanar Slicer", "prev": "NonPlanarSlicer.previous"}


# ------------------------------------------------------------------ helpers

def _known_folder(name):
    """Desktop / Start Menu Programs folder of the current user."""
    ps = f"[Environment]::GetFolderPath('{name}')"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         creationflags=NO_WINDOW)
    return out.stdout.strip()


def _shortcut(lnk, target, workdir, icon, args=""):
    def q(s):
        return s.replace("'", "''")
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(lnk)}');"
          f"$s.TargetPath='{q(target)}';$s.WorkingDirectory='{q(workdir)}';"
          f"$s.IconLocation='{q(icon)},0';$s.Arguments='{q(args)}';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True,
                   creationflags=NO_WINDOW)


def _shortcut_paths():
    programs = _known_folder("Programs")
    desktop = _known_folder("DesktopDirectory")
    return os.path.join(programs, APP + ".lnk"), os.path.join(desktop, APP + ".lnk")


def _dir_size_kb(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total // 1024


def _register(target):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
        vals = {
            "DisplayName": APP, "DisplayVersion": VERSION, "Publisher": PUBLISHER,
            "InstallLocation": target, "DisplayIcon": os.path.join(target, EXE),
            "UninstallString": f'"{os.path.join(target, UNINST)}" --uninstall',
            "QuietUninstallString": f'"{os.path.join(target, UNINST)}" --uninstall --silent',
        }
        for name, v in vals.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, v)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, _dir_size_kb(target))
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def _set(root, path, name, value, kind=None):
    import winreg
    with winreg.CreateKey(root, path) as k:
        winreg.SetValueEx(k, name, 0, kind if kind is not None else winreg.REG_SZ, value)


def _delete_tree(root, path):
    import winreg
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(root, path + "\\" + sub)
        winreg.DeleteKey(root, path)
    except OSError:
        pass


def user_choice(ext):
    """The ProgID the user picked for a file type in Windows (Open with > Always), or None."""
    import winreg
    base = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts" + "\\" + ext
    for sub in ("UserChoiceLatest", "UserChoice"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + "\\" + sub) as k:
                v = winreg.QueryValueEx(k, "ProgId")[0]
                if v:
                    return v
        except OSError:
            pass
    return None


def _notify_shell():
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)     # SHCNE_ASSOCCHANGED
    except Exception:
        pass


def associate(target):
    """Register the app for .3mf and .stl. Returns the extensions another app is still the
    user's choice for (Windows only lets the user change those)."""
    import winreg
    HK = winreg.HKEY_CURRENT_USER
    exe = os.path.join(target, EXE)
    cmd = f'"{exe}" "%1"'
    for ext, (progid, desc) in FILE_TYPES.items():
        cls = r"Software\Classes" + "\\" + progid
        _set(HK, cls, "", desc)
        _set(HK, cls, "FriendlyTypeName", desc)
        _set(HK, cls + r"\DefaultIcon", "", f'"{exe}",0')
        _set(HK, cls + r"\shell", "", "open")
        _set(HK, cls + r"\shell\open", "FriendlyAppName", APP)
        _set(HK, cls + r"\shell\open\command", "", cmd)
        _set(HK, r"Software\Classes" + "\\" + ext + r"\OpenWithProgids", progid, b"", winreg.REG_NONE)
        # the default for this type when the user hasn't picked one in Windows
        with winreg.CreateKey(HK, r"Software\Classes" + "\\" + ext) as k:
            try:
                cur = winreg.QueryValueEx(k, "")[0]
            except OSError:
                cur = ""
            if cur != progid:
                if cur:
                    winreg.SetValueEx(k, "ArcLayer.previous", 0, winreg.REG_SZ, cur)
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, progid)
    _set(HK, APP_KEY, "FriendlyAppName", APP)
    _set(HK, APP_KEY + r"\shell\open\command", "", cmd)
    for ext in FILE_TYPES:
        _set(HK, APP_KEY + r"\SupportedTypes", ext, "")
    # "Default apps" in Settings lists the app with its file types
    _set(HK, CAPS_KEY, "ApplicationName", APP)
    _set(HK, CAPS_KEY, "ApplicationDescription", "Non-planar (curved layer) slicer for 3D printers")
    _set(HK, CAPS_KEY, "ApplicationIcon", f'"{exe}",0')
    for ext, (progid, _) in FILE_TYPES.items():
        _set(HK, CAPS_KEY + r"\FileAssociations", ext, progid)
    _set(HK, r"Software\RegisteredApplications", APP, CAPS_KEY)
    _notify_shell()
    return [ext for ext, (progid, _) in FILE_TYPES.items() if user_choice(ext) not in (None, progid)]


def unassociate(app=APP, exe=EXE, progids=None, caps=r"Software\ArcLayer", prev_name="ArcLayer.previous"):
    import winreg
    HK = winreg.HKEY_CURRENT_USER
    progids = progids or {ext: progid for ext, (progid, _) in FILE_TYPES.items()}
    for ext, progid in progids.items():
        _delete_tree(HK, r"Software\Classes" + "\\" + progid)
        try:
            with winreg.OpenKey(HK, r"Software\Classes" + "\\" + ext + r"\OpenWithProgids", 0, winreg.KEY_ALL_ACCESS) as k:
                winreg.DeleteValue(k, progid)
        except OSError:
            pass
        try:
            with winreg.OpenKey(HK, r"Software\Classes" + "\\" + ext, 0, winreg.KEY_ALL_ACCESS) as k:
                if winreg.QueryValueEx(k, "")[0] == progid:
                    try:
                        prev = winreg.QueryValueEx(k, prev_name)[0]
                    except OSError:
                        prev = ""
                    winreg.SetValueEx(k, "", 0, winreg.REG_SZ, prev)
                try:
                    winreg.DeleteValue(k, prev_name)
                except OSError:
                    pass
        except OSError:
            pass
    _delete_tree(HK, "Software\\Classes\\Applications\\" + exe)
    _delete_tree(HK, caps)
    try:
        with winreg.OpenKey(HK, r"Software\RegisteredApplications", 0, winreg.KEY_ALL_ACCESS) as k:
            winreg.DeleteValue(k, app)
    except OSError:
        pass
    _notify_shell()


def open_default_apps():
    """Settings > Default apps on this app's page (Windows 11), else the general page."""
    import urllib.parse
    for uri in ("ms-settings:defaultapps?registeredAppUser=" + urllib.parse.quote(APP), "ms-settings:defaultapps"):
        try:
            os.startfile(uri)
            return True
        except OSError:
            continue
    return False


def _registered_dir():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            return winreg.QueryValueEx(k, "InstallLocation")[0]
    except OSError:
        return None


def _old_install_dir():
    """Folder of a NonPlanar Slicer install (the old name), or None. The uninstall entry first, then the
    standard folder (an install whose Windows entry went missing)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, OLD["reg"]) as k:
            d = winreg.QueryValueEx(k, "InstallLocation")[0]
        if os.path.exists(os.path.join(d, OLD["exe"])):
            return d
    except OSError:
        pass
    d = os.path.join(os.path.dirname(DEFAULT_DIR), OLD["app"])
    return d if os.path.exists(os.path.join(d, OLD["exe"])) else None


def remove_old_install():
    """Take away NonPlanar Slicer (the old name): program folder, shortcuts, uninstall entry, file types.
    The presets in %APPDATA%\\NonPlanar Slicer are NOT touched (ArcLayer copies them on its first start)."""
    import winreg
    d = _old_install_dir()
    if d and _app_running(d, OLD["exe"]):
        raise RuntimeError("NonPlanar Slicer (the old version) is running. Close it and try again.")
    for folder in (_known_folder("Programs"), _known_folder("DesktopDirectory")):
        lnk = os.path.join(folder, OLD["app"] + ".lnk")
        if folder and os.path.exists(lnk):
            os.remove(lnk)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, OLD["reg"])
    except OSError:
        pass
    unassociate(OLD["app"], OLD["exe"], OLD["types"], OLD["caps"], OLD["prev"])
    if d and os.path.exists(os.path.join(d, OLD["exe"])):     # only ever delete a folder that holds the old app
        shutil.rmtree(d, ignore_errors=True)
    return d


def _app_running(target, exe_name=EXE):
    exe = os.path.join(target, exe_name)
    if not os.path.exists(exe):
        return False
    try:                                  # a running exe can't be renamed on Windows
        os.rename(exe, exe)
        return False
    except OSError:
        return True


# ------------------------------------------------------------------ install / uninstall

def install(target, desktop=True, progress=lambda f, t: None, file_types=True):
    """Returns (exe, extensions another app is still the user's choice for)."""
    if not os.path.exists(PAYLOAD):
        raise FileNotFoundError("This setup has no app inside (payload.zip missing)")
    target = os.path.abspath(target)
    if _app_running(target):
        raise RuntimeError(f"{APP} is running. Close it and try again.")
    progress(0.01, "Removing NonPlanar Slicer (the old name)")
    remove_old_install()
    if os.path.isdir(target):
        progress(0.02, "Removing the old version")
        if not os.path.exists(os.path.join(target, EXE)) and os.listdir(target):
            raise RuntimeError(f"{target} already has other files in it. Pick an empty folder.")
        shutil.rmtree(target)
    os.makedirs(target)
    with zipfile.ZipFile(PAYLOAD) as z:
        items = z.infolist()
        for i, info in enumerate(items):
            z.extract(info, target)
            if i % 50 == 0:
                progress(0.05 + 0.85 * i / len(items), "Copying files")
    progress(0.95, "Creating shortcuts")
    exe = os.path.join(target, EXE)
    start_lnk, desk_lnk = _shortcut_paths()
    _shortcut(start_lnk, exe, target, exe)
    if desktop:
        _shortcut(desk_lnk, exe, target, exe)
    elif os.path.exists(desk_lnk):
        os.remove(desk_lnk)
    _register(target)
    taken = []
    if file_types:
        progress(0.98, "Registering .3mf and .stl")
        try:
            taken = associate(target)
        except OSError as e:
            print("file types:", e)
    else:
        unassociate()
    progress(1.0, "Done")
    return exe, taken


def uninstall(target, purge=False):
    import winreg
    if _app_running(target):
        raise RuntimeError(f"{APP} is running. Close it and try again.")
    for lnk in _shortcut_paths():
        if os.path.exists(lnk):
            os.remove(lnk)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_KEY)
    except OSError:
        pass
    unassociate()
    if os.path.exists(os.path.join(target, EXE)):          # only ever delete a folder that holds the app
        shutil.rmtree(target, ignore_errors=True)
    if purge and os.path.isdir(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)


def _start_app(exe):
    """Start the app the way a double-click does: through Explorer.
    Windows Smart App Control blocks an unsigned program started directly by another unsigned
    program (this setup), but not one the user opens from Explorer or a shortcut."""
    try:
        subprocess.Popen(["explorer.exe", exe], close_fds=True)
    except OSError:
        os.startfile(exe)


def _relaunch_from_temp(argv):
    """Uninstall.exe lives in the folder it deletes, so run a temp copy of it instead."""
    tmp = os.path.join(tempfile.gettempdir(), f"npslicer-uninstall-{os.getpid()}.exe")
    shutil.copy2(sys.executable, tmp)
    subprocess.Popen([tmp] + argv + ["--from-temp"], close_fds=True)


# ------------------------------------------------------------------ GUI

BG, HERO_TOP, HERO_BOT = "#14161a", "#0c3a34", "#121418"
CARD, EDGE, TEXT, MUTED = "#22252b", "#30343c", "#eceef1", "#8b919c"
ACCENT, ACCENT_HI, ORANGE = "#00b89b", "#00d4b3", "#ff8a3d"


def _mix(a, b, t):
    a, b = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _dpi_aware():
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _dark_titlebar(root):
    """Windows 11: dark title bar in the same colour as the window."""
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        if dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4):
            dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(on), 4)
        r, g, b = (int(BG[i:i + 2], 16) for i in (1, 3, 5))
        col = ctypes.c_int(r | g << 8 | b << 16)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(col), 4)       # caption colour
    except Exception:
        pass


def _rrect(cv, x0, y0, x1, y1, r, **kw):
    p = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
         x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(p, smooth=True, **kw)


TERMS = """ArcLayer is free software, made by one person in their spare time. It is experimental: it is
provided "as is", without warranty of any kind, express or implied, including fitness for a particular purpose.

Non-planar (curved layer) printing moves the nozzle up and down within a layer. The toolhead, fan duct or
cables can hit the print, the bed clips or the printer, even when the collision check finds nothing. Wrong
settings, presets, start G-code, templates or calibration values can also damage a printer or a print.

You are responsible for:
  - checking every G-code / print file in the preview before printing it,
  - the printer settings, presets and start G-code you use,
  - watching your printer while it prints, and stopping it if something goes wrong,
  - following your printer maker's safety instructions.

To the fullest extent the law allows, the author(s) of ArcLayer are not liable for any damage or loss
from using it or from prints made with it, including damage to printers, nozzles, beds or other equipment,
failed prints, wasted filament, damage to property, fire, or injury.

If you don't agree, click Cancel and don't install ArcLayer."""

TERMS_TICKS = [
    "I understand ArcLayer is free, experimental software, provided as is, without any warranty.",
    "I understand curved layers can make the toolhead hit the print or the printer, and I will check the preview "
    "and watch my printer while it prints.",
    "I accept that the author(s) are not responsible for any damage, collision, failed print, loss or injury "
    "from using this software.",
]


def _payload_mb():
    try:
        with zipfile.ZipFile(PAYLOAD) as z:
            return sum(i.file_size for i in z.infolist()) / 1e6
    except Exception:
        return 0


def gui_install():
    import math
    import tkinter as tk
    from tkinter import filedialog, messagebox

    _dpi_aware()
    root = tk.Tk()
    k = root.winfo_fpixels("1i") / 96.0

    def S(v):
        return int(round(v * k))

    W, H, HW = S(780), S(480), S(290)
    root.title(f"{APP} Setup")
    root.configure(bg=BG)
    root.resizable(False, False)
    root.geometry(f"{W}x{H}+{(root.winfo_screenwidth() - W) // 2}+{(root.winfo_screenheight() - H) // 3}")
    try:
        root.iconbitmap(os.path.join(HERE, "icon.ico"))
    except Exception:
        pass
    _dark_titlebar(root)
    F = "Segoe UI"
    FS = "Segoe UI Semibold"

    # ---------------- hero: curved (non-planar) layers being printed
    hero = tk.Canvas(root, width=HW, height=H, bg=BG, highlightthickness=0, bd=0)
    hero.place(x=0, y=0)
    for y in range(0, H, 2):
        c = _mix(HERO_TOP, HERO_BOT, min(1, (y / H) ** 0.8))
        hero.create_rectangle(0, y, HW, y + 2, fill=c, outline="")
    hero.create_line(HW - 1, 0, HW - 1, H, fill=EDGE)

    # logo + name
    lx, ly = S(32), S(34)
    try:
        logo = tk.PhotoImage(file=os.path.join(HERE, "logo.png"))      # 128 px
        if k < 1.4:
            logo = logo.subsample(2)
        hero.logo = logo
        hero.create_image(lx, ly, image=logo, anchor="nw")
        tx = lx + logo.width() + S(12)
    except Exception:
        tx = lx
    hero.create_text(tx, ly + S(30), text="Arc", anchor="w", fill=ACCENT_HI, font=(FS, 24), tags="arc")
    x_after = hero.bbox("arc")[2]
    hero.create_text(x_after, ly + S(30), text="Layer", anchor="w", fill=TEXT, font=(FS, 24))
    hero.create_text(S(32), S(112), anchor="nw", width=HW - S(60), fill="#a9c9c3", font=(F, 10),
                     text="Curved-layer slicing for your 3D printer: the nozzle moves up and down "
                          "inside each layer, so tops come out smooth.")
    hero.create_text(S(32), H - S(14), anchor="sw", fill="#6f8f89", font=(F, 9), text=f"Version {VERSION}")

    # ---------------- a nozzle printing non-planar layers: it rides the wave up and down
    N, gap, lw = 8, S(15), max(3, S(11))
    plate = H - S(44)
    x0, x1 = S(30), HW - S(30)
    base = plate - S(12)                                   # bottom layer's centre line
    amp = S(30)
    layers = [hero.create_line(0, 0, 1, 1, width=lw, capstyle="round", joinstyle="round", smooth=True)
              for _ in range(N + 1)]
    ahead = hero.create_line(0, 0, 1, 1, width=max(1, S(2)), dash=(1, 6), fill="#3e5a56", smooth=True)
    _rrect(hero, x0 - S(16), plate, x1 + S(16), plate + S(10), S(4), fill="#2b2f36", outline="")
    hero.create_line(x0 - S(14), plate + 1, x1 + S(14), plate + 1, fill="#4a505a", width=1)
    glow = [hero.create_oval(0, 0, 1, 1, fill=_mix(HERO_BOT, "#ffc98f", 0.3 + 0.23 * i), outline="") for i in range(4)]
    noz_break = hero.create_polygon(0, 0, 0, 0, fill="#9aa3ad", outline="")
    noz_block = hero.create_polygon(0, 0, 0, 0, fill=ORANGE, outline="")
    noz_cone = hero.create_polygon(0, 0, 0, 0, fill="#e8ecf0", outline="")
    noz_fan = hero.create_polygon(0, 0, 0, 0, fill="#3a3f48", outline="")
    anim = {"t": 0.0, "speed": 1.0, "p": 0.0, "shift": 0.0, "phase": 0.0}

    def wave(x, e, phase):
        """Height of a layer at x. e = slot (0 = the layer being printed, N = the bottom one).
        High layers follow the wavy top surface, low ones flatten out towards the bed."""
        u = (x - x0) / (x1 - x0)
        a = amp * max(0.0, 1 - e / N) ** 1.4
        return base - (N - e) * gap - a * (0.5 + 0.5 * math.sin(u * math.tau * 1.25 + phase))

    def pts(e, ua, ub, phase, n=36):
        out = []
        for i in range(n + 1):
            x = x0 + (x1 - x0) * (ua + (ub - ua) * i / n)
            out += [x, wave(x, e, phase)]
        return out

    def tick():
        sp = anim["speed"]
        if anim["shift"] > 0:                                  # layer done: the stack settles one step down
            anim["shift"] = min(1.0, anim["shift"] + 0.06 * sp)
            if anim["shift"] >= 1.0:
                anim["shift"], anim["p"] = 0.0, 0.0
                anim["phase"] += 0.35
        else:
            anim["p"] += 0.0075 * sp
            if anim["p"] >= 1.0:
                anim["p"], anim["shift"] = 1.0, 0.001
        sh, pr, ph = anim["shift"], anim["p"], anim["phase"]
        for i, ln in enumerate(layers):                        # i = 0: the layer being printed
            e = i + sh
            col = _mix("#19e0bf", HERO_BOT, min(0.92, 0.08 + 0.1 * e))
            layer_phase = ph - 0.35 * i                        # each layer was printed with an older wave
            if i == 0:
                hero.coords(ln, *pts(e, 0, max(0.002, pr), layer_phase))
            elif e <= N + 0.5:
                hero.coords(ln, *pts(e, 0, 1, layer_phase))
            else:
                hero.coords(ln, 0, 0, 0, 0)
            hero.itemconfigure(ln, fill=col)
        hero.coords(ahead, *(pts(sh, pr, 1, ph, 24) if pr < 0.999 else (0, 0, 0, 0)))
        # the nozzle sits on the end of the fresh line (lifts a little while the stack settles)
        x = x0 + (x1 - x0) * pr
        y = wave(x, sh, ph) - lw / 2 - S(2) - S(10) * math.sin(math.pi * sh)
        hero.coords(noz_cone, x - S(7), y - S(12), x + S(7), y - S(12), x + S(2), y, x - S(2), y)
        hero.coords(noz_block, x - S(16), y - S(30), x + S(16), y - S(30), x + S(16), y - S(12), x - S(16), y - S(12))
        hero.coords(noz_break, x - S(5), y - S(44), x + S(5), y - S(44), x + S(5), y - S(30), x - S(5), y - S(30))
        hero.coords(noz_fan, x + S(16), y - S(28), x + S(26), y - S(28), x + S(26), y - S(10), x + S(16), y - S(14))
        for i, g in enumerate(glow):
            r = S(9) - i * S(2)
            hero.coords(g, x - r, y - r + S(2), x + r, y + r + S(2))
            hero.itemconfigure(g, state="hidden" if sh > 0 else "normal")
        root.after(30, tick)
    tick()

    # ---------------- small custom widgets
    def button(parent, text, cmd, kind="accent", width=120):
        bg = parent["bg"]
        cv = tk.Canvas(parent, width=S(width), height=S(38), bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        fill = {"accent": ACCENT, "ghost": bg}[kind]
        hover = {"accent": ACCENT_HI, "ghost": CARD}[kind]
        rect = _rrect(cv, 1, 1, S(width) - 1, S(38) - 1, S(10), fill=fill,
                      outline=EDGE if kind == "ghost" else "")
        lab = cv.create_text(S(width) / 2, S(19), text=text, fill="#06231f" if kind == "accent" else TEXT,
                             font=(FS, 10))
        st = {"on": True, "cmd": cmd}
        cv.bind("<Enter>", lambda e: st["on"] and cv.itemconfigure(rect, fill=hover))
        cv.bind("<Leave>", lambda e: st["on"] and cv.itemconfigure(rect, fill=fill))
        cv.bind("<ButtonRelease-1>", lambda e: st["on"] and st["cmd"] and st["cmd"]())

        def config(text=None, enabled=None, cmd=None):
            if text is not None:
                cv.itemconfigure(lab, text=text)
            if cmd is not None:
                st["cmd"] = cmd
            if enabled is not None:
                st["on"] = enabled
                cv.itemconfigure(rect, fill=fill if enabled else (CARD if kind == "accent" else bg))
                cv.itemconfigure(lab, fill=(("#06231f" if kind == "accent" else TEXT) if enabled else MUTED))
                cv.configure(cursor="hand2" if enabled else "arrow")
        cv.config_btn = config
        return cv

    def toggle(parent, var, title, sub):
        bg = parent["bg"]
        row = tk.Frame(parent, bg=bg, cursor="hand2")
        tw, th = S(40), S(22)
        cv = tk.Canvas(row, width=tw, height=th, bg=bg, highlightthickness=0, bd=0)
        cv.pack(side="left", padx=(0, S(14)), anchor="n", pady=(S(2), 0))
        track = _rrect(cv, 1, 1, tw - 1, th - 1, th / 2 - 1, fill=ACCENT, outline="")
        knob = cv.create_oval(0, 0, 1, 1, fill="white", outline="")
        txt = tk.Frame(row, bg=bg)
        txt.pack(side="left", fill="x")
        tk.Label(txt, text=title, bg=bg, fg=TEXT, font=(F, 10)).pack(anchor="w")
        if sub:
            tk.Label(txt, text=sub, bg=bg, fg=MUTED, font=(F, 9)).pack(anchor="w")
        pos = {"p": 1.0 if var.get() else 0.0}

        def draw():
            p = pos["p"]
            cv.itemconfigure(track, fill=_mix("#3a3e47", ACCENT, p))
            x = S(3) + p * (tw - th)
            cv.coords(knob, x, S(3), x + th - S(6), th - S(3))

        def slide():
            goal = 1.0 if var.get() else 0.0
            pos["p"] += (goal - pos["p"]) * 0.45
            if abs(goal - pos["p"]) < 0.02:
                pos["p"] = goal
            draw()
            if pos["p"] != goal:
                root.after(15, slide)

        def flip(_e=None):
            var.set(not var.get())
            slide()
        for w in [row, cv, txt] + list(txt.winfo_children()):
            w.bind("<Button-1>", flip)
        draw()
        return row

    # ---------------- pages
    body = tk.Frame(root, bg=BG)
    body.place(x=HW + S(40), y=S(36), width=W - HW - S(80), height=H - S(72))
    pages = {}
    for name in ("terms", "options", "progress", "done"):
        pages[name] = tk.Frame(body, bg=BG)

    def show(name):
        for p in pages.values():
            p.place_forget()
        pages[name].place(x=0, y=0, relwidth=1, relheight=1)

    old = _registered_dir()
    updating = bool(old and os.path.exists(os.path.join(old, EXE)))
    old_named = _old_install_dir()
    if not updating and old_named:                     # NonPlanar Slicer installed: ArcLayer goes next to it
        updating = True
        o = os.path.normpath(old_named)
        old = os.path.join(os.path.dirname(o), APP) if os.path.basename(o) == OLD["app"] else DEFAULT_DIR

    # terms page: three ticks, then Accept
    p = pages["terms"]
    tk.Label(p, text="Before you install", bg=BG, fg=TEXT, font=(FS, 18)).pack(anchor="w")
    tk.Label(p, text="Please read this and tick all three boxes to continue.", bg=BG, fg=MUTED,
             font=(F, 10)).pack(anchor="w", pady=(S(2), S(10)))
    box = tk.Frame(p, bg=CARD, highlightthickness=1, highlightbackground=EDGE)
    box.pack(fill="x")
    txt = tk.Text(box, height=7, wrap="word", bg=CARD, fg="#c9ccd2", relief="flat", bd=0, font=(F, 9),
                  padx=S(10), pady=S(8), insertwidth=0, highlightthickness=0, cursor="arrow")
    sb = tk.Scrollbar(box, command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    txt.pack(side="left", fill="both", expand=True)
    import re as _re
    # join the hard-wrapped lines (the box wraps by itself), keep paragraphs and the list
    txt.insert("1.0", _re.sub(r"(?<!\n)\n(?![\n ])", " ", TERMS).replace("\n  - ", "\n\u2022 "))
    txt.configure(state="disabled")
    ticks = [tk.BooleanVar(value=False) for _ in TERMS_TICKS]

    def check(parent, var, text):
        bg = parent["bg"]
        row = tk.Frame(parent, bg=bg, cursor="hand2")
        sz = S(18)
        cv = tk.Canvas(row, width=sz, height=sz, bg=bg, highlightthickness=0, bd=0)
        cv.pack(side="left", anchor="n", padx=(0, S(10)), pady=(S(1), 0))
        lab = tk.Label(row, text=text, bg=bg, fg=TEXT, font=(F, 9), justify="left", anchor="w",
                       wraplength=W - HW - S(80) - sz - S(14))
        lab.pack(side="left", fill="x")

        def draw():
            cv.delete("all")
            on = var.get()
            _rrect(cv, 1, 1, sz - 1, sz - 1, S(4), fill=ACCENT if on else CARD, outline=ACCENT if on else "#5a606b")
            if on:
                cv.create_line(sz * .25, sz * .52, sz * .43, sz * .7, sz * .76, sz * .32, fill="#06231f",
                               width=max(2, S(2.4)), capstyle="round", joinstyle="round")

        def flip(_e=None):
            var.set(not var.get())
            draw()
            update_accept()
        for w_ in (row, cv, lab):
            w_.bind("<Button-1>", flip)
        draw()
        return row

    for v, t_ in zip(ticks, TERMS_TICKS):
        check(p, v, t_).pack(anchor="w", fill="x", pady=(S(10), 0))
    tfoot = tk.Frame(p, bg=BG)
    tfoot.pack(side="bottom", fill="x")
    accept = button(tfoot, "Accept", lambda: show("options"), width=120)
    accept.pack(side="right")
    button(tfoot, "Cancel", root.destroy, "ghost", 100).pack(side="right", padx=(0, S(10)))
    tk.Label(tfoot, text="", bg=BG).pack(side="left")

    def update_accept():
        accept.config_btn(enabled=all(v.get() for v in ticks))
    update_accept()

    # options page
    p = pages["options"]
    tk.Label(p, text=f"Update {APP}" if updating else f"Install {APP}", bg=BG, fg=TEXT,
             font=(FS, 18)).pack(anchor="w")
    tk.Label(p, text=(("NonPlanar Slicer is now ArcLayer. Your presets and projects come along."
                       if old_named else "Your presets and projects are kept.") if updating else
                      "Installs for your user only, no admin rights needed."),
             bg=BG, fg=MUTED, font=(F, 10)).pack(anchor="w", pady=(S(2), S(22)))

    tk.Label(p, text="INSTALL LOCATION", bg=BG, fg=MUTED, font=(FS, 8)).pack(anchor="w")
    card = tk.Frame(p, bg=CARD, highlightthickness=1, highlightbackground=EDGE, highlightcolor=ACCENT)
    card.pack(fill="x", pady=(S(6), S(22)))
    dir_var = tk.StringVar(value=old or DEFAULT_DIR)
    ent = tk.Entry(card, textvariable=dir_var, relief="flat", bd=0, bg=CARD, fg=TEXT, insertbackground=TEXT,
                   font=(F, 10), selectbackground=ACCENT)
    ent.pack(side="left", fill="x", expand=True, padx=(S(12), S(6)), pady=S(10))

    def browse(_e=None):
        d = filedialog.askdirectory(initialdir=os.path.dirname(dir_var.get()) or "C:\\")
        if d:
            d = os.path.normpath(d)
            dir_var.set(d if os.path.basename(d) == APP else os.path.join(d, APP))
    br = tk.Label(card, text="Browse", bg=CARD, fg=ACCENT_HI, font=(FS, 9), cursor="hand2")
    br.pack(side="right", padx=S(12))
    br.bind("<Button-1>", browse)

    desk, assoc, launch = tk.BooleanVar(value=True), tk.BooleanVar(value=True), tk.BooleanVar(value=True)
    toggle(p, desk, "Desktop shortcut", None).pack(anchor="w", fill="x", pady=(0, S(12)))
    toggle(p, assoc, "Open .3mf and .stl files", "Double-click a model to open it here").pack(
        anchor="w", fill="x", pady=(0, S(12)))
    toggle(p, launch, f"Start {APP} when setup finishes", None).pack(anchor="w", fill="x")

    foot = tk.Frame(p, bg=BG)
    foot.pack(side="bottom", fill="x")
    mb = _payload_mb()
    if mb:
        tk.Label(foot, text=f"Needs about {mb:.0f} MB", bg=BG, fg=MUTED, font=(F, 9)).pack(side="left")
    go = button(foot, "Update" if updating else "Install", None)
    go.pack(side="right")
    cancel = button(foot, "Cancel", root.destroy, "ghost", 100)
    cancel.pack(side="right", padx=(0, S(10)))

    # progress page
    p = pages["progress"]
    head = tk.Label(p, text="Installing…", bg=BG, fg=TEXT, font=(FS, 18))
    head.pack(anchor="w")
    tk.Label(p, text="This takes a few seconds.", bg=BG, fg=MUTED, font=(F, 10)).pack(anchor="w")
    pct = tk.Label(p, text="0%", bg=BG, fg=TEXT, font=(FS, 40))
    pct.pack(anchor="w", pady=(S(60), S(6)))
    BW = W - HW - S(80)
    bar = tk.Canvas(p, width=BW, height=S(8), bg=BG, highlightthickness=0, bd=0)
    bar.pack(anchor="w")
    _rrect(bar, 0, 0, BW, S(8), S(4), fill=CARD, outline="")
    status = tk.Label(p, text="", bg=BG, fg=MUTED, font=(F, 9))
    status.pack(anchor="w", pady=(S(10), 0))
    prog = {"shown": 0.0, "goal": 0.0}

    def draw_bar():
        g = prog["goal"]
        prog["shown"] += (g - prog["shown"]) * 0.18
        v = prog["shown"]
        x1 = max(S(8), BW * v)
        bar.delete(prog.get("id"))
        prog["id"] = _rrect(bar, 0, 0, x1, S(8), S(4), fill=ACCENT, outline="")
        pct.configure(text=f"{round(v * 100)}%")
        if prog.get("run"):
            root.after(16, draw_bar)

    # done page
    p = pages["done"]
    ck = tk.Canvas(p, width=S(76), height=S(76), bg=BG, highlightthickness=0, bd=0)
    ck.pack(anchor="w", pady=(S(30), S(18)))
    ring = ck.create_oval(S(3), S(3), S(73), S(73), outline=ACCENT, width=S(3))
    tick_line = ck.create_line(0, 0, 0, 0, fill=ACCENT_HI, width=S(5), capstyle="round", joinstyle="round")
    ck.itemconfigure(ring, state="hidden")
    done_head = tk.Label(p, text="You're all set", bg=BG, fg=TEXT, font=(FS, 20))
    done_head.pack(anchor="w")
    done_sub = tk.Label(p, text="", bg=BG, fg=MUTED, font=(F, 10), justify="left", wraplength=BW)
    done_sub.pack(anchor="w", pady=(S(4), 0))
    dfoot = tk.Frame(p, bg=BG)
    dfoot.pack(side="bottom", fill="x")
    open_btn = button(dfoot, "Launch", None, width=130)
    close_btn = button(dfoot, "Close", root.destroy, "ghost", 100)

    def draw_check(step=0):
        ck.itemconfigure(ring, state="normal")
        a = (S(22), S(39)), (S(34), S(51)), (S(55), S(28))
        t = min(1.0, step / 14)
        if t <= 0.4:
            u = t / 0.4
            pts = [a[0], (a[0][0] + (a[1][0] - a[0][0]) * u, a[0][1] + (a[1][1] - a[0][1]) * u)]
        else:
            u = (t - 0.4) / 0.6
            pts = [a[0], a[1], (a[1][0] + (a[2][0] - a[1][0]) * u, a[1][1] + (a[2][1] - a[1][1]) * u)]
        ck.coords(tick_line, *[c for xy in pts for c in xy])
        if t < 1:
            root.after(18, draw_check, step + 1)

    # ---------------- actions
    def set_progress(frac, text):
        def upd():
            prog["goal"] = frac
            status.configure(text=text)
        root.after(0, upd)

    def work():
        try:
            exe, taken = install(dir_var.get(), desk.get(), set_progress, assoc.get())
        except Exception as e:
            def fail(msg=str(e)):
                anim["speed"] = 1.0
                prog["run"] = False
                show("options")
                messagebox.showerror("Setup", msg)
            root.after(0, fail)
            return

        def finish():
            prog["goal"] = 1.0
            prog["run"] = False
            anim["speed"] = 0.6
            show("done")
            draw_check()
            msg = f"{APP} {VERSION} is installed. Find it in the Start menu" + (
                " and on your desktop." if desk.get() else ".")
            if taken:
                msg += (f"\n\nTo open {' and '.join(taken)} files with it by default: right-click one, "
                        f"Open with, Choose another app, {APP}, Always.")
            done_sub.configure(text=msg)
            if launch.get():
                _start_app(exe)
                close_btn.pack(side="right")
                if not taken:
                    root.after(2600, root.destroy)
            else:
                open_btn.config_btn(cmd=lambda: (_start_app(exe), root.after(300, root.destroy)))
                open_btn.pack(side="right")
                close_btn.pack(side="right", padx=(0, S(10)))
        root.after(500, finish)

    def start():
        anim["speed"] = 3.0
        head.configure(text="Updating…" if updating else "Installing…")
        show("progress")
        prog["run"] = True
        draw_bar()
        threading.Thread(target=work, daemon=True).start()
    go.config_btn(cmd=start)
    root.bind("<Return>", lambda e: pages["options"].winfo_ismapped() and start())
    root.bind("<Escape>", lambda e: pages["progress"].winfo_ismapped() or root.destroy())

    show("terms")
    root.mainloop()


def gui_uninstall(target):
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    if not messagebox.askyesno(f"Uninstall {APP}", f"Remove {APP} from this computer?"):
        return
    purge = messagebox.askyesno(f"Uninstall {APP}",
                                "Also delete your presets and imported models?\n"
                                f"({DATA_DIR})\n\nExported prints in Documents are kept either way.")
    try:
        uninstall(target, purge)
        messagebox.showinfo(f"Uninstall {APP}", f"{APP} was removed.")
    except Exception as e:
        messagebox.showerror(f"Uninstall {APP}", str(e))


# ------------------------------------------------------------------ entry

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--silent", action="store_true")
    ap.add_argument("--dir")
    ap.add_argument("--no-desktop", action="store_true")
    ap.add_argument("--no-launch", action="store_true")
    ap.add_argument("--no-associate", action="store_true", help="don't register .3mf / .stl")
    ap.add_argument("--purge", action="store_true")
    ap.add_argument("--from-temp", action="store_true", help=argparse.SUPPRESS)
    a, _ = ap.parse_known_args()

    if a.uninstall:
        target = a.dir or os.path.dirname(sys.executable)
        if not a.from_temp:
            argv = ["--uninstall", "--dir", target] + (["--silent"] if a.silent else []) + \
                   (["--purge"] if a.purge else [])
            _relaunch_from_temp(argv)
            return 0
        time.sleep(1.0)                       # let the original Uninstall.exe exit first
        if a.silent:
            uninstall(target, a.purge)
        else:
            gui_uninstall(target)
        # the temp copy can't delete itself; ask Windows to remove it a few seconds later
        subprocess.Popen(f'cmd /c ping -n 4 127.0.0.1 >nul & del "{sys.executable}"',
                         creationflags=NO_WINDOW, close_fds=True)
        return 0

    if a.silent:
        exe, taken = install(a.dir or DEFAULT_DIR, not a.no_desktop, file_types=not a.no_associate)
        if taken:
            print("Another app is the user's choice for:", ", ".join(taken))
        if not a.no_launch:
            _start_app(exe)
        return 0
    gui_install()
    return 0


if __name__ == "__main__":
    sys.exit(main())
