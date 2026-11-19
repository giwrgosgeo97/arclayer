"""Builds the Windows installer.   python build_installer.py

Steps:
  1. ArcLayer.exe  (PyInstaller, folder build: fast start-up)
  2. Uninstall.exe         (small, goes inside the app folder)
  3. payload.zip           (the app folder)
  4. ArcLayer-Setup-<version>.exe  (one file: the installer with the payload inside)

Output: dist/ArcLayer-Setup-<version>.exe  - that single file is what you share.
"""
import os
import shutil
import subprocess
import sys
import zipfile

VERSION = "0.8.4"
ROOT = os.path.dirname(os.path.abspath(__file__))
INST = os.path.join(ROOT, "installer")
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")
ICON = os.path.join(INST, "icon.ico")
SEP = os.pathsep

# big optional packages that may be installed but the app never uses
EXCLUDE = ["matplotlib", "IPython", "jupyter", "notebook", "PyQt5", "PyQt6", "PySide2", "PySide6",
           "pandas", "sympy", "pytest", "torch", "tensorflow", "cv2", "numba", "pyglet", "sklearn"]


def version_file(name, description):
    """Windows version details (Properties > Details) for an exe."""
    v = tuple(int(x) for x in VERSION.split(".")) + (0,) * (4 - len(VERSION.split(".")))
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'ArcLayer'),
      StringStruct('FileDescription', '{description}'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', '{name}'),
      StringStruct('OriginalFilename', '{name}.exe'),
      StringStruct('ProductName', 'ArcLayer'),
      StringStruct('ProductVersion', '{VERSION}'),
      StringStruct('LegalCopyright', 'ArcLayer')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"""
    os.makedirs(BUILD, exist_ok=True)
    path = os.path.join(BUILD, f"version_{name.replace(' ', '_')}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def pyinstaller(*args):
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
           "--workpath", BUILD, "--distpath", DIST, "--specpath", BUILD, *args]
    print(">", " ".join(cmd[3:]))
    subprocess.run(cmd, check=True, cwd=ROOT)


def main():
    if not os.path.exists(ICON):
        subprocess.run([sys.executable, os.path.join(INST, "make_icon.py")], check=True)
    with open(os.path.join(INST, "version.txt"), "w") as f:
        f.write(VERSION)
    shutil.rmtree(DIST, ignore_errors=True)

    KEYS = os.path.join(ROOT, "app", "supporter_key.txt")
    if not os.path.exists(KEYS):
        raise SystemExit("app/supporter_key.txt is missing: supporter codes would not work. "
                         "Copy it from your backup, or make one: python make_supporter_code.py --new-key")
    print("\n[1/4] App")
    excl = [x for m in EXCLUDE for x in ("--exclude-module", m)]
    pyinstaller("--windowed", "--name", "ArcLayer", "--icon", ICON,
                "--version-file", version_file("ArcLayer", "ArcLayer - non-planar 3D printing slicer"),
                "--add-data", f"{os.path.join(ROOT, 'app', 'ui')}{SEP}app/ui",
                "--add-data", f"{os.path.join(ROOT, 'nonplanar', 'bambu_gcode')}{SEP}nonplanar/bambu_gcode",
                "--add-data", f"{os.path.join(ROOT, 'nonplanar', 'printer_gcode.json')}{SEP}nonplanar",
                "--add-data", f"{os.path.join(ROOT, 'nonplanar', 'clearance.json')}{SEP}nonplanar",
                "--add-data", f"{KEYS}{SEP}app",                      # supporter code keys (private, not in git)
                "--add-data", f"{os.path.join(INST, 'version.txt')}{SEP}.",   # the app knows its version (updates, reports)
                "--collect-data", "trimesh", "--hidden-import", "app.server",
                # PyAV (FFmpeg) for your own camera (RTSP / HTTP)
                "--hidden-import", "av", "--collect-binaries", "av",
                # trimesh imports these lazily (3MF and other formats)
                "--hidden-import", "networkx", "--hidden-import", "lxml.etree",
                # SciPy 1.18 loads these lazily; PyInstaller's hook misses them
                "--collect-submodules", "scipy._external", "--collect-submodules", "scipy.ndimage",
                "--paths", ROOT, *excl, os.path.join(INST, "launch.py"))
    app_dir = os.path.join(DIST, "ArcLayer")

    print("\n[2/4] Uninstaller")
    pyinstaller("--onefile", "--windowed", "--name", "Uninstall", "--icon", ICON,
                "--version-file", version_file("Uninstall", "ArcLayer uninstaller"),
                "--add-data", f"{os.path.join(INST, 'version.txt')}{SEP}.",
                os.path.join(INST, "setup_main.py"))
    shutil.move(os.path.join(DIST, "Uninstall.exe"), os.path.join(app_dir, "Uninstall.exe"))

    print("\n[3/4] Packing")
    payload = os.path.join(BUILD, "payload.zip")
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _, files in os.walk(app_dir):
            for fn in files:
                full = os.path.join(root, fn)
                z.write(full, os.path.relpath(full, app_dir))

    print("\n[4/4] Setup")
    name = f"ArcLayer-Setup-{VERSION}"
    pyinstaller("--onefile", "--windowed", "--name", name, "--icon", ICON,
                "--version-file", version_file(name, "ArcLayer setup"),
                "--add-data", f"{payload}{SEP}.", "--add-data", f"{os.path.join(INST, 'version.txt')}{SEP}.",
                "--add-data", f"{ICON}{SEP}.", "--add-data", f"{os.path.join(INST, 'logo.png')}{SEP}.",
                os.path.join(INST, "setup_main.py"))
    out = os.path.join(DIST, name + ".exe")
    print(f"\nDone: {out}  ({os.path.getsize(out) / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
