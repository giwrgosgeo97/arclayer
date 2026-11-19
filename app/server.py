"""Local HTTP server behind the app window. Listens on 127.0.0.1 only."""
import webbrowser
import base64
import json
import math
import mimetypes
mimetypes.add_type("text/javascript", ".js")   # Windows may map .js to text/plain
mimetypes.add_type("image/svg+xml", ".svg")
import os
import re
import subprocess
import threading
import time
import traceback
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

import trimesh

from nonplanar.slicer import load_parts, slice_model, placement_matrix
from nonplanar.gcode import write_gcode
from nonplanar.collision import check, safe_angle
from nonplanar.config import Settings
from nonplanar.threemf import load_3mf_parts
from nonplanar import bambu, project, calibration, machine
from nonplanar.transforms import ConicalTransform
from . import profiles, supporter
from . import printers as printers_mod
from .device import BambuPrinter
from .camera import Cameras
from . import sync as sync_mod
from . import hosts
from . import report as report_mod
from . import updates
from nonplanar import sequence as sequence_mod
from nonplanar import arrange as arrange_mod

from .paths import UI, WORK, OUTPUT, DATA
from . import paths
TEMPLATES = os.path.join(profiles.ROOT, "templates")
RECENT = os.path.join(DATA, "recent.json")
APP_SETTINGS = os.path.join(DATA, "app_settings.json")
UI_STATE = os.path.join(DATA, "ui_state.json")          # what the page remembers: last presets, slots, tabs…    # small app-wide flags (supporter, …)
PRINTER_IMAGES = os.path.join(DATA, "printer_images")      # your own printer photos: "<printer model>.png"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")


def _printer_images():
    """{printer model: file name} for the photos in PRINTER_IMAGES (file name = the model, e.g. "Prusa MK4.jpg")."""
    try:
        return {os.path.splitext(f)[0]: f for f in sorted(os.listdir(PRINTER_IMAGES))
                if f.lower().endswith(IMAGE_EXT)}
    except OSError:
        return {}
MODEL_EXT = (".stl", ".obj", ".3mf", ".ply")
WINDOW = None          # the pywebview window (None when the app runs in a web browser)
for d in (WORK, OUTPUT, TEMPLATES):
    os.makedirs(d, exist_ok=True)


def _b64(a):
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


def _clean_name(name):
    base = os.path.basename(name or "model")
    return re.sub(r"[^A-Za-z0-9._ -]", "_", base) or "model"



# "Support the project" link shown in the top bar; empty hides the button
DONATE_URL = os.environ.get("NPS_DONATE_URL", "https://ko-fi.com/georgies")

class State:
    def __init__(self):
        self.model_path = None
        self.model_name = None
        self.mesh = None
        self.parts = []              # separate parts (for colours), same coordinates as mesh
        self.calib = None            # nonplanar.calibration.Calib when a calibration print is loaded
        self.job = {"state": "idle", "progress": 0, "text": "", "log": [], "error": None}
        self.result = None           # {"gcode", "preview_json", "stats", "collision", "template"}
        self.printer = BambuPrinter()        # the printer the Device tab shows and sends to
        self.devices = {}                    # serial -> BambuPrinter: every printer connected (several A1s…)
        self.dev_names = {}                  # serial -> printer preset name
        self.cams = Cameras()
        self.lock = threading.Lock()
        self.pending = []            # files to open, from the command line or a second app start
        self.dirty = False           # the page reports unsaved project changes here
        self.title = ""
        self.close_requested = False
        self.allow_close = False


S = State()


# ------------------------------------------------------------------ actions

def load_model(name, data=None, path=None, as_project=True):
    """Load a model from uploaded bytes (data) or a file on disk (path).
    A 3MF saved by this app also returns its project data (settings, colours, placement)."""
    name = _clean_name(name or (os.path.basename(path) if path else "model.stl"))
    if not name.lower().endswith(MODEL_EXT):
        raise ValueError("Supported model files: STL, OBJ, 3MF, PLY")
    if path is None:
        path = os.path.join(WORK, name)
        with open(path, "wb") as f:
            f.write(data)
    elif not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")
    meta = project.read_meta(path) if name.lower().endswith(".3mf") else None
    try:
        if meta:
            parts = []
            for pname, m in load_3mf_parts(path, use_build=False):
                m.merge_vertices()
                m.metadata["name"] = pname
                parts.append(m)
        else:
            parts = load_parts(path)
    except Exception as e:
        if name.lower().endswith(".3mf") and project.is_sliced_3mf(path):
            raise ValueError("This is a sliced print file (.gcode.3mf): it holds G-code, not a model to edit. "
                             "Open the original model or project instead.") from e
        raise
    shown = (meta or {}).get("model_name") or name
    out = _use_parts(parts, shown, path)
    S.calib = None
    cal = (meta or {}).get("calibration")
    if meta and as_project:
        out["project"] = meta
        if cal:                       # a saved calibration print: rebuild its per-height changes
            try:
                S.calib = calibration.build(cal["test"], cal["params"], cal.get("nozzle", 0.4), cal.get("layer_height", 0.2))
                out["calibration"] = S.calib.summary()
            except Exception as e:
                print("calibration in project:", e)
    return out


def _use_parts(parts, shown, path=None):
    """Make these meshes the model on the plate (centred, on the bed). Returns the model info for the page."""
    mesh = trimesh.util.concatenate(parts) if len(parts) > 1 else parts[0].copy()
    lo, hi = mesh.bounds
    shift = [-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]]
    mesh.apply_translation(shift)
    for m in parts:
        m.apply_translation(shift)
    S.model_path, S.model_name, S.mesh, S.parts, S.result = path, shown, mesh, parts, None
    base = os.path.splitext(shown)[0]
    return {"name": shown, "file": os.path.basename(path) if path else shown, "faces": int(len(mesh.faces)),
            "size": mesh.extents.round(2).tolist(),
            "parts": [{"name": base if len(parts) == 1 else (m.metadata.get("name") or f"Part {k + 1}"),
                       "faces": int(len(m.faces)),
                       "tris": _b64(m.triangles.astype(np.float32).reshape(-1, 3))} for k, m in enumerate(parts)]}


def arrange_model(req):
    """Auto arrange: spread the parts over the plate (gap from the print sequence / toolhead clearance)."""
    if S.mesh is None:
        raise ValueError("No model loaded")
    if len(S.parts) < 2:
        raise ValueError("The model is one part: there is nothing to arrange")
    presets = profiles.list_all()
    printer = {**presets["printer"][req["printer"]], **(req.get("printer_overrides") or {})}
    pname = req["process"]
    if pname not in presets["process"]:
        pname = re.sub(r" @A1(?: (\d\.\d+) nozzle)?$", lambda m: f" @{float(m.group(1)):g} nozzle" if m.group(1) else "", pname)
    process = {**presets["process"][pname], **(req.get("process_overrides") or {})}
    fil = presets["filament"].get(req.get("filament")) or next(iter(presets["filament"].values()))
    s = profiles.build_settings(printer, fil, process)
    M = placement_matrix(S.mesh, s, req.get("placement") or {})
    gap, why = arrange_mod.gap_for(s)
    if req.get("gap") is not None:
        gap, why = float(req["gap"]), "your setting"
    by_object = s.print_sequence == "by_object"
    r = arrange_mod.arrange(S.parts, M[:3, :3], float(s.bed_x), float(s.bed_y), gap,
                            min_gap=None if by_object else 2.0)
    for m, d in zip(S.parts, r["moves"]):
        m.apply_translation(d)
    mesh = trimesh.util.concatenate(S.parts)
    lo, hi = mesh.bounds
    shift = [-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]]
    for m in S.parts:
        m.apply_translation(shift)
    mesh.apply_translation(shift)
    S.mesh, S.result = mesh, None
    n = len(r["groups"])
    text = f"Arranged {n} objects, {r['gap']:.0f} mm apart ({why})"
    if r["gap"] < gap - 1e-6:
        text = f"Arranged {n} objects, but only {r['gap']:.0f} mm apart: with {gap:.0f} mm ({why}) they don't fit"
    if n < len(S.parts):
        text += f". Parts that touch stay together ({len(S.parts)} parts in {n} groups)"
    return {"text": text, "gap": r["gap"], "groups": r["groups"], "faces": int(len(mesh.faces)),
            "size": mesh.extents.round(2).tolist(),
            "parts": [{"faces": int(len(m.faces)), "tris": _b64(m.triangles.astype(np.float32).reshape(-1, 3))}
                      for m in S.parts]}


def refine_model(cap=1_500_000):
    """Split big triangles (for support painting: the brush paints whole triangles). Same shape."""
    if S.mesh is None:
        raise ValueError("No model loaded")
    before = sum(len(m.faces) for m in S.parts)
    for edge in (1.5, 3.0, 6.0, 12.0):
        new = [trimesh.remesh.subdivide_to_size(m.vertices, m.faces, max_edge=edge, max_iter=10) for m in S.parts]
        if sum(len(f) for _, f in new) <= max(cap, before):
            break
    else:
        new = [(m.vertices, m.faces) for m in S.parts]
    parts = []
    for (v, f), old in zip(new, S.parts):
        m = trimesh.Trimesh(v, f, process=False)
        m.metadata.update(old.metadata)
        parts.append(m)
    S.parts = parts
    S.mesh = trimesh.util.concatenate(parts) if len(parts) > 1 else parts[0].copy()
    S.result = None
    after = sum(len(m.faces) for m in parts)
    return {"changed": after != before, "faces": int(after),
            "parts": [{"faces": int(len(m.faces)), "tris": _b64(m.triangles.astype(np.float32).reshape(-1, 3))}
                      for m in parts]}


def create_calibration(req):
    c = calibration.build(req["test"], req.get("params") or {}, float(req.get("nozzle", 0.4)),
                          float(req.get("layer_height", 0.2)))
    parts = []
    for name, m in c.parts:
        m = m.copy()                  # the calibration keeps its own (uncentred) copies
        m.metadata["name"] = name
        parts.append(m)
    info = _use_parts(parts, c.meta["name"] + ".3mf")
    S.calib = c
    info["calibration"] = c.summary()
    return info


def open_path(path, as_project=True):
    info = load_model(os.path.basename(path), path=path, as_project=as_project)
    if info.get("project"):
        info["path"] = os.path.abspath(path)       # Save writes back here
    _add_recent(path)
    return info


# ------------------------------------------------------------------ files: dialogs, recent, project

MODEL_TYPES = ("Models (*.3mf;*.stl;*.obj;*.ply)", "All files (*.*)")
PROJECT_TYPES = ("3MF project (*.3mf)",)


def _dialog(kind, directory="", save_filename="", file_types=()):
    """Native Open/Save dialog of the app window. Returns a path, None if cancelled,
    or False when there is no app window (running in a web browser)."""
    if WINDOW is None:
        return False
    import webview
    t = {"open": webview.FileDialog.OPEN, "save": webview.FileDialog.SAVE, "folder": webview.FileDialog.FOLDER}[kind]
    r = WINDOW.create_file_dialog(t, directory=directory or "", save_filename=save_filename,
                                  file_types=tuple(file_types))
    if not r:
        return None
    return r if isinstance(r, str) else r[0]


def _app_settings():
    try:
        with open(APP_SETTINGS, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _set_app_settings(**kw):
    from_sync = kw.pop("_from_sync", False)
    if not from_sync and any(k in sync_mod.SHARED_KEYS for k in kw):
        kw["shared_time"] = time.time()          # synced to your other PCs (File > Sync)
        if "sync" in globals():
            sync.kick()
    d = {**_app_settings(), **kw}
    try:
        with open(APP_SETTINGS, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
    except OSError:
        pass
    return d


def _recent():
    try:
        with open(RECENT, encoding="utf-8") as f:
            items = json.load(f)
        return [p for p in items if isinstance(p, str) and os.path.isfile(p)][:10]
    except (OSError, ValueError):
        return []


def _add_recent(path):
    path = os.path.abspath(path)
    if os.path.dirname(path).lower() == os.path.abspath(WORK).lower():
        return                                    # browser uploads: not a file the user can find again
    items = [path] + [p for p in _recent() if p.lower() != path.lower()]
    try:
        with open(RECENT, "w", encoding="utf-8") as f:
            json.dump(items[:10], f, indent=1)
    except OSError:
        pass


def _last_dir():
    r = _recent()
    return os.path.dirname(r[0]) if r else os.path.join(os.path.expanduser("~"), "Documents")


def open_dialog(kind):
    p = _dialog("open", _last_dir(), file_types=PROJECT_TYPES + MODEL_TYPES if kind == "project" else MODEL_TYPES)
    if p is False:
        return {"nodialog": True}
    if p is None:
        return {"cancelled": True}
    return open_path(p, as_project=(kind != "model"))


def save_project(req):
    if S.mesh is None:
        raise ValueError("Nothing to save: import a model first")
    meta = req.get("project") or {}
    path = None if req.get("save_as") else req.get("path")
    if path and not path.lower().endswith(".3mf"):
        path = None
    if not path:
        base = os.path.splitext(os.path.splitext(S.model_name or "project")[0])[0] + ".3mf"
        start_dir = os.path.dirname(req.get("path") or "") or _last_dir()
        path = _dialog("save", start_dir, base, PROJECT_TYPES)
        if path is None:
            return {"cancelled": True}
        if path is False:                          # web browser: save into the output folder
            os.makedirs(os.path.join(OUTPUT, "projects"), exist_ok=True)
            path = os.path.join(OUTPUT, "projects", base)
        if not path.lower().endswith(".3mf"):
            path += ".3mf"
    pl = req.get("placement") or {}
    M = placement_matrix(S.mesh, Settings(), pl)
    names = [m.metadata.get("name") or f"Part {k + 1}" for k, m in enumerate(S.parts)]
    if len(S.parts) == 1:
        names = [os.path.splitext(S.model_name or "model")[0]]
    meta = {**meta, "model_name": S.model_name, "placement": pl}
    if S.calib is not None:
        meta["calibration"] = {"test": S.calib.test, "params": S.calib.params, "nozzle": S.calib.nozzle}
    project.write_project(path, S.parts, names, M, meta)
    _add_recent(path)
    S.dirty = False
    return {"path": os.path.abspath(path), "name": os.path.basename(path)}


def _set_title(title):
    S.title = title
    if WINDOW is not None:
        try:
            WINDOW.set_title(title)
        except Exception:
            pass


def _bring_to_front():
    if WINDOW is None:
        return
    try:
        WINDOW.restore()
        WINDOW.on_top = True
        WINDOW.on_top = False
    except Exception:
        pass


def on_closing():
    """pywebview's closing event (GUI thread): with unsaved changes, cancel the close and let the page
    ask Save / Don't save / Cancel. The page calls /api/quit to really close."""
    if S.allow_close or not S.dirty:
        return True
    S.close_requested = True
    return False


def quit_app():
    S.allow_close = True
    if WINDOW is not None:
        threading.Timer(0.05, WINDOW.destroy).start()


def _job_log(msg):
    S.job["log"].append(str(msg))


def _job_progress(frac, text):
    S.job["progress"], S.job["text"] = round(float(frac), 3), text


def _set_angle(tool_settings, angle):
    for ts in tool_settings:      # process settings are shared by every filament's Settings
        ts.cone_angle = float(angle)


START_MARK = "; NPS_BUILTIN_START"

# build plates: name in the app -> the printer's bed_type
PLATES = {"Textured PEI Plate": "textured_plate", "Cool Plate": "cool_plate", "Engineering Plate": "eng_plate",
          "High Temp Plate": "hot_plate", "Cool Plate (SuperTack)": "supertack_plate"}
COOL_OK = {"PLA": 35, "PLA-CF": 35, "PVA": 35, "TPU": 30}           # cool plate bed temperatures
SUPERTACK = {"PLA": 40, "PLA-CF": 40, "PETG": 70, "PETG-CF": 70, "TPU": 35, "PVA": 40}


CALIB_FAST = {"outer_wall_speed": 150, "inner_wall_speed": 200, "solid_speed": 200, "top_surface_speed": 150,
              "infill_speed": 200, "travel_speed": 400, "default_acceleration": 6000, "outer_wall_acceleration": 4000,
              "top_surface_acceleration": 4000, "travel_acceleration": 8000}


def _plate_temps(tool_settings, filaments, plate):
    """Bed temperature for the plate (Bambu's per-plate values). Returns warnings."""
    notes = []
    for ts, f in zip(tool_settings, filaments):
        key = PLATES.get(plate)
        own = getattr(ts, key + "_temp", 0) if key else 0
        if own and own > 0:                          # the filament has its own temperature for this plate
            ts.bed_temp = own
            ts.bed_temp_first = getattr(ts, key + "_temp_initial_layer", 0) or own
            continue
        t = (f.get("type") or "PLA").upper()
        table = COOL_OK if plate == "Cool Plate" else SUPERTACK if plate == "Cool Plate (SuperTack)" else None
        if table is None:
            continue
        if t in table:
            ts.bed_temp = ts.bed_temp_first = table[t]
        else:
            notes.append(f"{t} isn't recommended on the {plate}: kept {ts.bed_temp_first:g} °C. Use glue or another plate")
    return notes


def _is_stock_gcode(printer):
    """True when the printer preset still has the start G-code it came with (not one the user wrote)."""
    g = (printer.get("start_gcode") or "").strip()
    if not g or g == profiles.A1_START_NOTE.strip():
        return True
    vendor = printer.get("vendor") or ""
    model = (printer.get("model") or "")[len(vendor):].strip()
    for v, m, bx, by, _bz, _ext in printers_mod.PRINTERS:
        if v == vendor and m == model:
            return g == printers_mod.start_gcode(v, m, bx, by).strip()
    return False


def _machine_gcode(printer):
    """The printer's own start / end (and Bambu filament-change) G-code, or None."""
    model = printer.get("model")
    return bambu.builtin(model) or machine.gcode_for(model)


# printers whose firmware swaps filament on a plain T command, though their Orca profile has no change G-code
T_FIRMWARE = ("Anycubic Kobra 3", "Anycubic Kobra 3 Combo", "Anycubic Kobra S1", "Prusa XL", "Snapmaker J1", "Snapmaker U1")


def _non_bambu_change(printer, change):
    """Filament change for a non-Bambu printer: its maker's own sequence (Orca profile), plus the T command the
    firmware swaps filament on (CFS, ACE, Qidi Box, toolchangers). Never Bambu's M620 / M621."""
    base = change if change and "M620" not in change else ((machine.gcode_for(printer.get("model")) or {}).get("change") or "")
    if not base.strip():
        model = printer.get("model") or ""
        if any(model.endswith(m) for m in T_FIRMWARE):
            base = "G1 Z{max_layer_z + 3.0} F1200"            # the firmware swaps filament on T (ACE, toolchanger)
        else:                                                # single nozzle, no filament system: swap by hand
            base = "G1 Z{max_layer_z + 3.0} F1200\nM600 ; change filament: the printer waits for you"
    if "M600" not in base and not re.search(r"(?m)^\s*T(\[next_extruder\]|\{next_extruder\}|\d+\b)", base) \
            and "next_extruder" not in base.split("T")[-1][:20]:
        base = base.rstrip() + "\nT[next_extruder]\nM109 S[new_filament_temp]"
    return base


def _render_builtin(text, builtin, preview, preview_tools, tool_settings, filaments, plate, printer=None, names=None):
    """Put the printer's own start and end G-code, rendered for this print, in place of the markers."""
    printer = printer or {}
    lay0 = next((k for k, layer in enumerate(preview) if layer), None)
    bbox, first_tool = None, 0
    if lay0 is not None:
        p0 = np.vstack([np.asarray(p, float) for _, p in preview[lay0]])
        bbox = ((float(p0[:, 0].min()), float(p0[:, 1].min())), (float(p0[:, 0].max()), float(p0[:, 1].max())))
        first_tool = int(preview_tools[lay0][0]) if preview_tools[lay0] else 0
    max_z = max((float(np.asarray(p)[:, 2].max()) for layer in preview for _, p in layer), default=0.0)
    layers = sum(1 for layer in preview if layer)
    env = machine.env(tool_settings, filaments, first_tool, bbox, max_z,
                      "Supertack Plate" if plate == "Cool Plate (SuperTack)" else plate, layers,
                      builtin.get("printer_model") or printer.get("model", ""), names)
    st, u1 = machine.render(builtin["start"], env)
    en, u2 = machine.render(builtin["end"], env)
    if u1 | u2:
        _job_log("Printer start/end G-code: unknown values set to 0: " + ", ".join(sorted(u1 | u2)))
    text = text.replace(START_MARK, st.strip("\n"), 1)
    i = text.rfind(bambu.END_MARK)
    return text[:i] + en.strip("\n") + text[i + len(bambu.END_MARK):]


def run_slice(req):
    try:
        presets = profiles.list_all()
        printer = presets["printer"][req["printer"]]
        pname = req["process"]
        if pname not in presets["process"]:            # a name from before 0.7.3 ("... @A1")
            pname = re.sub(r" @A1(?: (\d\.\d+) nozzle)?$", lambda m: f" @{float(m.group(1)):g} nozzle" if m.group(1) else "", pname)
        process = {**presets["process"][pname], **(req.get("process_overrides") or {})}
        printer = {**printer, **(req.get("printer_overrides") or {})}
        slots = req.get("filaments") or [{"preset": req["filament"], "overrides": req.get("filament_overrides")}]
        fil_presets = []
        for k, f in enumerate(slots):
            if f["preset"] not in presets["filament"]:
                raise ValueError(f"Filament {k + 1}: preset '{f['preset']}' not found")
            d = {**presets["filament"][f["preset"]], **(f.get("overrides") or {})}
            if f.get("color"):
                d["color"] = f["color"]
            fil_presets.append(d)
        tool_settings = [profiles.build_settings(printer, d, process) for d in fil_presets]
        s = tool_settings[0]
        filaments = [{"type": d.get("type", "PLA"), "color": d.get("color", "#808080")} for d in fil_presets]
        plate = req.get("plate") if req.get("plate") in PLATES else "Textured PEI Plate"
        for note in _plate_temps(tool_settings, filaments, plate):
            _job_log("Note: " + note)
        _job_log(f"Plate: {plate}, bed {s.bed_temp_first:g} °C")
        placement = req.get("placement") or {}
        n_fil = len(slots)
        part_tools = [int(x) for x in (req.get("part_tools") or [])][:len(S.parts)]
        part_tools += [0] * (len(S.parts) - len(part_tools))
        changes = [(int(c["layer"]), int(c["tool"])) for c in (req.get("tool_changes") or [])]
        for t in part_tools + [c[1] for c in changes] + [
                getattr(s, k) - 1 for k in ("wall_filament", "sparse_infill_filament", "solid_infill_filament",
                                            "support_filament", "support_interface_filament") if getattr(s, k) > 0]:
            if not 0 <= t < n_fil:
                raise ValueError(f"Filament {t + 1} is used but only {n_fil} filament slot(s) are set up")
        nz = float(s.nozzle_diameter)
        if s.layer_height > 0.75 * nz + 1e-9:
            raise ValueError(f"Layer height {s.layer_height:g} mm is too thick for a {nz:g} mm nozzle "
                             f"(at most about {0.7 * nz:.2f} mm). Pick a process preset for this nozzle.")
        pn = process.get("compatible_nozzle")
        if pn and abs(float(pn) - nz) > 1e-6:
            _job_log(f"Note: the process preset is meant for a {float(pn):g} mm nozzle, the printer has {nz:g} mm")
        _job_log(f"Printer: {req['printer']} | Process: {req['process']} | Filaments: " +
                 ", ".join(f"{k + 1}: {f['preset']}" for k, f in enumerate(slots)))

        start = end = builtin = None
        template = profiles.resolve(printer.get("template_3mf") or "")
        if template:
            if not os.path.exists(template):
                raise FileNotFoundError(f"Template not found: {template}")
            start, end = bambu.read_template(template)
            change = bambu.read_change_gcode(template)
            _job_log("Using start/end G-code from the Bambu template")
            tn = bambu.template_nozzle(template)
            if tn and abs(tn - nz) > 1e-6:
                _job_log(f"WARNING: the Bambu template was sliced for a {tn:g} mm nozzle but this printer preset "
                         f"has {nz:g} mm. The printer may refuse the file or warn about the nozzle: export a "
                         f"template for this nozzle size.")
        elif _machine_gcode(printer) and _is_stock_gcode(printer):
            # the printer's own start / end G-code, from its maker's slicer profile
            builtin = _machine_gcode(printer)
            is_bbl = (printer.get("vendor") or "Bambu Lab") == "Bambu Lab"
            start, end = START_MARK, bambu.END_MARK
            change = builtin.get("change") if is_bbl else None
            src = builtin.get("source") or "Bambu Studio"
            _job_log(f"Using {printer.get('model')}'s own start/end G-code ({src})")
        else:
            start, end = printer.get("start_gcode") or None, printer.get("end_gcode") or None
            change = None
            _job_log("No Bambu template set: using the custom start/end G-code")
        if not change and (printer.get("change_filament_gcode") or "").strip() and \
                printer.get("change_filament_gcode").strip() != profiles.DEFAULT_CHANGE.strip():
            change = printer["change_filament_gcode"]
        if (printer.get("vendor") or "Bambu Lab") != "Bambu Lab" and not template:
            change = _non_bambu_change(printer, change)

        cal = S.calib
        if cal is not None:            # calibration print: flat layers and the test's own settings
            for ts in tool_settings:
                for k, v in cal.overrides.items():
                    if hasattr(ts, k):
                        setattr(ts, k, type(getattr(ts, k))(v) if getattr(ts, k) is not None else v)
                if not cal.flat:               # the non-planar test: cone axis at the cup's centre
                    ts.cone_center = None
                if cal.flat and ts.gcode_flavor in ("bambu", "klipper"):
                    # fast printers: flat test prints at speeds close to Bambu's own (the test's own speeds win)
                    for k, v in CALIB_FAST.items():
                        if k not in cal.overrides:
                            setattr(ts, k, max(getattr(ts, k), v))
            _job_log(f"Calibration: {cal.meta['name']} ({len(cal.steps)} steps). " + "; ".join(cal.notes))
        requested = s.cone_angle
        auto = s.collision_avoidance == "lower_angle" and (cal is None or not cal.flat)
        limit = safe_angle(s)
        surface = s.mode == "surface"
        if auto and abs(requested) > limit and not surface:
            _set_angle(tool_settings, round(math.copysign(limit, requested) - math.copysign(0.05, requested), 1))
            _job_log(f"Cone angle {requested:+.1f} deg is steeper than the toolhead allows "
                     f"(about {limit:.1f} deg for {s.nozzle_free_height} mm clearance over {s.head_radius} mm): "
                     f"using {s.cone_angle:+.1f} deg")
        by_object = s.print_sequence == "by_object" and cal is None
        if by_object and len(S.parts or []) < 2:
            _job_log("Print by object: the model is one object, so it prints as usual")
            by_object = False
        if by_object and req.get("paint"):
            _job_log("WARNING: painted supports are not used when printing by object")
        seq_warn, order = [], []
        for attempt in range(4):
            objects = None
            if cal is not None:
                cal.bind(placement_matrix(S.mesh, s, placement))
            if cal is not None and cal.direct is not None:
                tf, layers = _direct_layers(cal, s, placement, part_tools[0] if part_tools else 0)
            elif by_object:
                objs = sequence_mod.slice_objects(S.parts, s, placement_matrix(S.mesh, s, placement), slice_model,
                                                  log=_job_log, progress=_job_progress, part_tools=part_tools,
                                                  tool_changes=changes)
                order = [o["index"] + 1 for o in objs]
                _job_log("Print by object, in this order (lowest first): " + " → ".join(
                    f"{o['index'] + 1} ({o['height']:.0f} mm)" for o in objs))
                seq_warn = sequence_mod.check(objs, s)
                for wv in seq_warn:
                    _job_log("WARNING: " + wv["text"])
                objects = [(o["layers"], o["tf"]) for o in objs]
                tf, layers = objs[0]["tf"], None
            else:
                mesh, tf, layers = slice_model(S.mesh, s, log=_job_log, progress=_job_progress, placement=placement,
                                               parts=S.parts, part_tools=part_tools, tool_changes=changes,
                                               paint=req.get("paint"))
            text, preview, stats = write_gcode(layers, s, tf, start, end, log=_job_log, progress=_job_progress,
                                               tool_settings=tool_settings, filaments=filaments, change_gcode=change,
                                               calib=cal, objects=objects)
            if builtin:
                text = _render_builtin(text, builtin, preview, stats["preview_tools"], tool_settings, filaments, plate,
                                       printer, {"printer": req["printer"], "process": pname, "filament": slots[0]["preset"]})
            _job_progress(0.93, "Checking toolhead collisions")
            if objects:                      # each object on its own (objects near each other: sequence check)
                coll, start_n = {"hits": 0, "worst_mm": 0.0, "worst_layer": None, "points": []}, 0
                for lay, _ in objects:
                    cnt = sum(1 for L in lay if L.paths)
                    c = check(preview[start_n:start_n + cnt], s, log=lambda m: None)
                    coll["hits"] += c["hits"]
                    coll["points"] += [[x, y, z, n + start_n, o] for x, y, z, n, o in c["points"]]
                    if c["worst_mm"] > coll["worst_mm"]:
                        coll["worst_mm"], coll["worst_layer"] = c["worst_mm"], (c["worst_layer"] or 0) + start_n
                    start_n += cnt
                _job_log(f"WARNING: possible head collision at {coll['hits']} points" if coll["hits"]
                         else "Collision check: OK (each object, with the configured head geometry)")
            else:
                coll = check(preview, s, log=_job_log)
            if surface:
                cur = s.surface_max_angle if s.surface_max_angle > 0 else limit
                if not (auto and coll["hits"]) or attempt == 3 or cur <= 6:
                    break
                _job_log(f"Collision found: re-slicing with the top surface smoothed to {cur - 3:.0f}°")
                _job_progress(0.05, f"Collision: smoothing the top to {cur - 3:.0f}°")
                for ts in tool_settings:
                    ts.surface_max_angle = cur - 3
                continue
            if not (auto and coll["hits"]) or attempt == 3 or abs(s.cone_angle) < 1.5:
                break
            new = s.cone_angle - math.copysign(1.0, s.cone_angle)
            _job_log(f"Collision found at {s.cone_angle:+.1f} deg: re-slicing at {new:+.1f} deg")
            _job_progress(0.05, f"Collision: re-slicing at {new:.1f}°")
            _set_angle(tool_settings, new)
        coll["sequence"] = seq_warn                      # print by object: objects too close / too tall
        coll["order"] = order
        coll["angle_requested"] = requested
        coll["angle_used"] = s.cone_angle
        ptools = stats.pop("preview_tools")
        pspeeds = stats.pop("preview_speeds", None)
        pextra = (stats.pop("preview_attrs", None), stats.pop("preview_thick", None), stats.pop("layer_times", None))
        per_tool = stats.pop("per_tool")
        used = stats.pop("used_tools")
        fil_out = [{"id": t + 1, "slot": t, "preset": slots[t]["preset"], "type": filaments[t]["type"],
                    "color": filaments[t]["color"], "used_m": per_tool[t]["m"], "used_g": per_tool[t]["g"]}
                   for t in used]
        _job_progress(0.97, "Building preview")
        tbk = stats.pop("time_by_kind", {})
        st = {k: float(v) for k, v in stats.items()}
        st["time_by_kind"] = {k: round(float(v), 1) for k, v in sorted(tbk.items(), key=lambda kv: -kv[1])}
        if st.get("time_cool_extra", 0) > 60:
            _job_log(f"Speed check: slowing down for layer cooling adds about {st['time_cool_extra'] / 60:.0f} min "
                     f"({int(st.get('slowed_layers', 0))} layers). Filament > Cooling > 'layers shorter than' sets it.")
        if st.get("time_mvs_extra", 0) > 60:
            _job_log(f"Speed check: the filament's max volumetric speed adds about {st['time_mvs_extra'] / 60:.0f} min.")
        st["filaments"] = fil_out
        S.result = {"gcode": text, "preview": _preview_payload(preview, ptools, s, tf, pspeeds, pextra),
                    "stats": st, "collision": coll, "template": template, "settings": s, "filaments": fil_out,
                    "thumb": (preview, ptools, [f["color"] for f in filaments]),
                    "process": process,
                    "builtin": printer.get("model") if (builtin and (printer.get("vendor") or "Bambu Lab") == "Bambu Lab")
                    else None, "plate": plate, "model": printer.get("model")}
        S.job.update(state="done", progress=1.0, text="Slicing finished")
    except Exception as e:
        traceback.print_exc()
        S.job.update(state="error", error=str(e), text="Slicing failed")
        _job_log("ERROR: " + str(e))


from nonplanar.slicer import KINDS


def _direct_layers(cal, s, placement, tool):
    """PA line / pattern: toolpaths made directly, moved to where the plate preview shows them."""
    M = placement_matrix(S.mesh, s, placement)
    A = M[:2, :2]
    # the model was centred when it was put on the plate: undo that shift, then place
    c0 = cal.center()
    layers = cal.direct(s)
    for L in layers:
        for p in L.paths:
            p.pts = (np.asarray(p.pts, float) - c0) @ A.T + M[:2, 3]
            p.tool = tool
    allp = np.vstack([p.pts for L in layers for p in L.paths])
    if (allp < 0).any() or (allp[:, 0] > s.bed_x).any() or (allp[:, 1] > s.bed_y).any():
        raise ValueError("The calibration pattern doesn't fit on the plate: use fewer steps or move it")
    cx, cy = (allp.min(0) + allp.max(0)) / 2
    return ConicalTransform(0.0, cx, cy, 1.0), layers


# per-point values for the preview's colour modes: (name, scale) stored as uint16 value * scale
ATTRS = (("width", 1000), ("flow", 100), ("fan", 1), ("temp", 1), ("accel", 1), ("pa", 10000))


def _preview_payload(preview, ptools, s, tf, pspeeds=None, extra=None):
    pos, kind, tool, idx, layer_end, layer_z, speed = [], [], [], [], [], [], []
    attrs, thick, ltimes = extra or (None, None, None)
    cols = {name: [] for name, _ in ATTRS}
    height = []
    n = 0
    nidx = 0
    if pspeeds is None:
        pspeeds = [[s.outer_wall_speed] * len(lay) for lay in preview]
    for lay, tl, sl in zip(preview, ptools, pspeeds):
        zmax = 0.0
        for (k, pts), t, sp in zip(lay, tl, sl):
            m = len(pts)
            speed.append(np.full(m, min(65535, max(1, round(sp))), np.uint16))
            pos.append(pts.astype(np.float32))
            kind.append(np.full(m, KINDS.index(k) if k in KINDS else 0, np.uint8))
            tool.append(np.full(m, t, np.uint8))
            a = np.arange(n, n + m - 1, dtype=np.uint32)
            idx.append(np.column_stack([a, a + 1]).ravel())
            nidx += 2 * (m - 1)
            n += m
            zmax = max(zmax, float(pts[:, 2].max()))
        if attrs is not None:
            li = len(layer_end)
            for e, (k, pts) in enumerate(lay):
                m = len(pts)
                for (name, sc), v in zip(ATTRS, attrs[li][e]):
                    cols[name].append(np.full(m, min(65535, max(0, round(v * sc))), np.uint16))
                height.append(np.clip(np.rint(thick[li][e] * 1000), 0, 65535).astype(np.uint16))
        layer_end.append(nidx)
        layer_z.append(round(zmax, 3))
    return {"pos": _b64(np.vstack(pos)), "kind": _b64(np.concatenate(kind)), "tool": _b64(np.concatenate(tool)),
            "idx": _b64(np.concatenate(idx)), "speed": _b64(np.concatenate(speed)),
            "travel_speed": float(s.travel_speed),
            **({"attrs": {name: _b64(np.concatenate(cols[name])) for name, _ in ATTRS},
                "attr_scale": dict(ATTRS), "height": _b64(np.concatenate(height)),
                "layer_time": [round(float(t), 2) for t in ltimes]} if attrs is not None and height else {}),
            "layers": layer_end, "layer_z": layer_z, "kinds": KINDS,
            "cone": {"cx": tf.cx, "cy": tf.cy, "angle": tf.angle, "ramp": tf.H}}


def _out_name():
    """File name from the process's Filename format (Orca's placeholders), without extension."""
    model = os.path.splitext(os.path.splitext(S.model_name)[0])[0]
    fmt = ((S.result.get("process") or {}).get("filename_format") or "").strip() or "{input_filename_base}"
    st, s = S.result["stats"], S.result["settings"]
    t = int(st.get("time_s", 0))
    vals = {"input_filename_base": model + "_nonplanar", "input_filename": model, "layer_height": f"{s.layer_height:g}",
            "filament_type": "+".join(dict.fromkeys(f["type"] for f in S.result.get("filaments") or [])) or "PLA",
            "print_time": f"{t // 3600}h{t % 3600 // 60}m", "printer_model": S.result.get("model") or "",
            "nozzle_diameter": f"{s.nozzle_diameter:g}", "total_weight": f"{st.get('filament_g', 0):.0f}g"}
    name = re.sub(r"[{\[](\w+)(?:\[\d+\])?[}\]]", lambda m: str(vals.get(m.group(1), m.group(0))), fmt)
    for ext in (".gcode", ".3mf"):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip(" .")
    return name or model + "_nonplanar"


def _post_process(text):
    """Run the process's post-processing scripts on the G-code (each gets the file path, like Orca)."""
    cmds = [c.strip() for c in ((S.result.get("process") or {}).get("post_process") or "").splitlines()
            if c.strip() and not c.strip().startswith("#")]
    if not cmds:
        return text
    import subprocess
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".gcode")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        for c in cmds:
            r = subprocess.run(f'{c} "{tmp}"', shell=True, capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                raise ValueError(f"Post-processing script failed ({c}): {(r.stderr or r.stdout).strip()[:300]}")
        with open(tmp, encoding="utf-8") as f:
            return f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def export(kind, ask=False, quick=False):
    """Write the sliced plate. ask=True shows a Save dialog (app window only)."""
    if not S.result:
        raise ValueError("Slice first")
    base = _out_name()
    if kind not in ("gcode", "3mf"):
        raise ValueError(kind)
    has_tpl = bool(S.result["template"]) and S.result["template"].lower().endswith(".3mf")
    if kind == "3mf" and not has_tpl and not S.result.get("builtin"):
        raise ValueError("A .gcode.3mf is for Bambu printers. Export G-code for this printer "
                         "(or, if you changed its start G-code, set a Bambu template under Printer > Machine G-code).")
    ext = ".gcode" if kind == "gcode" else ".gcode.3mf"
    path = os.path.join(OUTPUT, base + ext)
    if ask:
        types = ("G-code (*.gcode)",) if kind == "gcode" else ("Bambu print file (*.gcode.3mf;*.3mf)",)
        p = _dialog("save", OUTPUT, base + ext, types)
        if p is None:
            return None
        if p:
            if kind == "gcode":
                path = p if p.lower().endswith(".gcode") else p + ".gcode"
            else:
                path = p if p.lower().endswith(".3mf") else p + ".gcode.3mf"
    text = bambu.quick_start(S.result["gcode"]) if quick else S.result["gcode"]
    text = _post_process(text)
    if kind == "gcode":
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    elif has_tpl:
        bambu.write_3mf(S.result["template"], text, path, S.result.get("filaments"))
    else:
        try:
            pv, pt, cols = S.result["thumb"]
            thumb = {"plate": bambu.thumbnail(pv, pt, cols, 512), "small": bambu.thumbnail(pv, pt, cols, 128)}
        except Exception as e:                      # a picture is nice to have, never a reason to fail
            print("thumbnail:", e)
            thumb = None
        bambu.build_3mf(text, path, S.result["builtin"], float(S.result["settings"].nozzle_diameter),
                        S.result.get("filaments"), S.result["stats"], thumb)
    return path


SEND_KEYS = ("bed_leveling", "timelapse", "flow_cali", "vibration_cali", "quick_start")


def _ams_mapping(opts, used):
    """(use_ams, ams_mapping): file filament k (0-based) -> AMS tray (0-15), from the Send print choices."""
    choice = opts.get("ams_map")
    if not isinstance(choice, dict):          # older page: slot k of the app is tray k of the AMS lite
        use_ams = bool(opts.get("use_ams", False)) or len(used) > 1
        return use_ams, ([(k if k in used else -1) for k in range(max(used) + 1)] if (use_ams and used) else [])
    trays = {k: int(choice.get(str(k), k)) for k in used}
    if all(t < 0 for t in trays.values()):
        if len(used) > 1:
            raise ValueError("A print with several filaments needs the AMS: pick an AMS slot for each filament")
        return False, []                       # the external spool
    if any(t < 0 for t in trays.values()):
        raise ValueError("Pick an AMS slot for every filament, or the external spool for a one-filament print")
    return True, [trays.get(k, -1) for k in range(max(used) + 1)]


def send_to_printer(opts):
    if not S.result:
        raise ValueError("Slice first")
    if not S.printer.connected:
        raise ConnectionError("Connect to the printer first (Device tab)")
    target = next((S.dev_names.get(s) for s, d in S.devices.items() if d is S.printer), None)
    if target:
        tm = profiles.list_all()["printer"].get(target, {}).get("model")
        if tm and S.result.get("model") and tm != S.result.get("model"):
            raise ValueError(f"The plate was sliced for a {S.result['model']}, but the printer chosen in the Device "
                             f"tab ({target}) is a {tm}. Pick that printer in the Device tab, or slice for it.")
    if not isinstance(S.printer, BambuPrinter):          # Klipper / OctoPrint / PrusaLink: plain G-code
        path = export("gcode")
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(os.path.basename(path))[0]).strip("_")[:60] or "print"
        S.printer.upload_and_print(path, stem + ".gcode")
        _save_live(stem)
        return stem + ".gcode"
    _set_app_settings(send_options={k: bool(opts[k]) for k in SEND_KEYS if k in opts})
    used = [f["slot"] for f in S.result.get("filaments") or []]
    use_ams, mapping = _ams_mapping(opts, used)        # check the choices before uploading
    path = export("3mf", quick=bool(opts.get("quick_start")))
    # printer-safe name: ASCII letters, digits, - and _ only (spaces and ° can stop the printer starting it)
    stem = os.path.basename(path)[:-len(".gcode.3mf")] if path.lower().endswith(".gcode.3mf") else os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")[:60] or "print"
    remote = S.printer.upload(path, stem + ".gcode.3mf")
    with zipfile.ZipFile(path) as z:            # the plate number inside the file (Metadata/plate_N.gcode)
        m = re.search(r"plate_(\d+)\.gcode$", bambu._gcode_name(z.namelist()))
    plate = int(m.group(1)) if m else 1
    S.printer.start_print(remote, plate=plate, bed_leveling=bool(opts.get("bed_leveling", True)),
                          bed_type=PLATES.get(S.result.get("plate"), "textured_plate"),
                          use_ams=use_ams, ams_mapping=mapping, timelapse=bool(opts.get("timelapse", False)),
                          flow_cali=bool(opts.get("flow_cali", False)),
                          vibration_cali=bool(opts.get("vibration_cali", False)))
    _save_live(stem)
    return remote


LIVE_FILE = os.path.join(DATA, "live_print.json")


def _save_live(name):
    """Keep the preview of the print just sent, for the live view (survives closing the app)."""
    try:
        live = {"name": name, "sent": time.time(), "preview": S.result["preview"],
                "stats": {k: v for k, v in S.result["stats"].items()}, "collision": S.result.get("collision") or {}}
        tmp = LIVE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(live, f)
        os.replace(tmp, LIVE_FILE)
    except Exception as e:                          # the live view is a nice extra, never a reason to fail a print
        print("live view:", e)


def _load_live():
    try:
        with open(LIVE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_template(name, data):
    name = _clean_name(name)
    if not name.lower().endswith(".3mf"):
        raise ValueError("The template must be a .gcode.3mf exported from Bambu Studio or Orca")
    path = os.path.join(TEMPLATES, name)
    with open(path, "wb") as f:
        f.write(data)
    try:
        bambu.read_template(path)   # validate it now, not at slice time
    except Exception:
        os.remove(path)
        raise
    return "templates/" + name


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _camera(self, name):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        def write(b):
            self.wfile.write(b)
            self.wfile.flush()
        try:
            S.cams.stream(name, write)
        except OSError:
            pass                                  # the viewer closed it

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    def _json(self):
        b = self._body()
        return json.loads(b) if b else {}

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path.startswith("/api/"):
                return self._send(200, self.api_get(url.path[5:], urllib.parse.parse_qs(url.query)))
            if url.path in ("/camera/printer.mjpg", "/camera/extra.mjpg"):
                return self._camera(url.path.split("/")[-1].split(".")[0])
            if url.path.startswith("/printer-images/"):     # your own printer photos
                fn = os.path.basename(urllib.parse.unquote(url.path[len("/printer-images/"):]))
                path = os.path.join(PRINTER_IMAGES, fn)
                if not fn.lower().endswith(IMAGE_EXT) or not os.path.isfile(path):
                    return self._send(404, {"error": "not found"})
                with open(path, "rb") as f:
                    return self._send(200, f.read(), mimetypes.guess_type(path)[0] or "image/png")
            rel = url.path.lstrip("/") or "index.html"
            path = os.path.normpath(os.path.join(UI, rel))
            if not path.startswith(UI) or not os.path.isfile(path):
                return self._send(404, {"error": "not found"})
            with open(path, "rb") as f:
                self._send(200, f.read(), mimetypes.guess_type(path)[0] or "application/octet-stream")
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": str(e)})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        try:
            self._send(200, self.api_post(url.path[5:]))
        except Exception as e:
            traceback.print_exc()
            self._send(400, {"error": str(e)})

    def api_get(self, ep, q):
        if ep == "schema":
            return {"schema": profiles.SCHEMA, "percent": sorted(profiles.PERCENT_KEYS)}
        if ep == "profiles":
            return profiles.list_all()
        if ep == "job":
            j = S.job
            return {**{k: j[k] for k in ("state", "progress", "text", "error")}, "log": j["log"][-200:],
                    "stats": S.result["stats"] if S.result and j["state"] == "done" else None,
                    "collision": S.result["collision"] if S.result and j["state"] == "done" else None}
        if ep == "live-print":
            return _load_live()
        if ep == "preview":
            if not S.result:
                raise ValueError("Nothing sliced yet")
            return S.result["preview"]
        if ep == "device/status":
            return {**S.printer.status(), "cameras": S.cams.status(), "runout_events": S.runout.events[-5:],
                    "printers": _known_printers(),
                    "active": next((S.dev_names.get(s) for s, d in S.devices.items() if d is S.printer), None),
                    "runout_cfg": _app_settings().get("runout") or {"mode": "pause", "order": [1, 2, 3, 4]}}
        if ep == "model":
            return {"name": S.model_name}
        if ep == "update":
            return updates.status()
        if ep == "ui-state":
            try:
                with open(UI_STATE, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                return {}
        if ep == "ping":
            return {"app": "arclayer"}
        if ep == "app-info":
            st = _app_settings()
            os.makedirs(PRINTER_IMAGES, exist_ok=True)
            return {"donate_url": DONATE_URL, "supporter": supporter.check(st.get("supporter_code", "")),
                    "visible": st.get("visible"), "printer_images": _printer_images(),
                    "printer_images_dir": PRINTER_IMAGES, "send_options": st.get("send_options") or {},
                    "plate": st.get("plate") or "Textured PEI Plate", "plates": list(PLATES),
                    "camera_url": st.get("camera_url") or "", "autoconnect": st.get("autoconnect"),
                    "intro_seen": bool(st.get("intro_seen"))}
        if ep == "recent":
            return {"items": _recent()}
        if ep == "calibration/tests":
            ftype = (q.get("type") or ["PLA"])[0]
            nz = float((q.get("nozzle") or ["0.4"])[0])
            return {"tests": [{**t, "defaults": calibration.defaults(t["id"], ftype, nz)} for t in calibration.TESTS]}
        if ep == "pending":
            with S.lock:
                items, S.pending = S.pending, []
                close, S.close_requested = S.close_requested, False
            return {"open": items, "close": close, "window": WINDOW is not None}
        raise ValueError(f"Unknown endpoint {ep}")

    def api_post(self, ep):
        if ep == "model":
            name = urllib.parse.unquote(self.headers.get("X-Filename", "model.stl"))
            return load_model(name, self._body(), as_project=self.headers.get("X-Geometry-Only") != "1")
        if ep == "template":
            name = urllib.parse.unquote(self.headers.get("X-Filename", "template.gcode.3mf"))
            return {"path": save_template(name, self._body())}
        req = self._json()
        if ep == "ui-state":                      # what the page remembers between starts
            if isinstance(req, dict) and len(json.dumps(req)) < 2_000_000:
                tmp = UI_STATE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(req, f)
                os.replace(tmp, UI_STATE)
            return {"ok": True}
        if ep == "profiles/save":
            name = profiles.save(req["category"], req["name"], req["data"])
            sync.kick()
            return {"name": name}
        if ep == "profiles/reset":
            profiles.reset(req["category"], req["name"])
            sync.kick()
            return {"ok": True}
        if ep == "profiles/delete":
            profiles.delete(req["category"], req["name"])
            sync.deleted(f"{req['category']}/{profiles._safe(req['name'])}.json")
            return {"ok": True}
        if ep == "sync/status":
            return _sync_status()
        if ep == "sync/now":
            sync.run()
            return _sync_status()
        if ep == "sync/choose":
            p = _dialog("folder", req.get("start") or os.path.expanduser("~"))
            return {"path": p or ""}
        if ep == "sync/config":
            d = str(req.get("dir") or "").strip()
            if d and not os.path.isdir(d):
                raise ValueError("That folder doesn't exist on this PC")
            _set_app_settings(sync_dir=d)
            if d:
                sync.run()
            return _sync_status()
        if ep == "slice":
            if S.mesh is None:
                raise ValueError("Import a model first")
            if S.job["state"] == "running":
                raise ValueError("Already slicing")
            S.job = {"state": "running", "progress": 0, "text": "Starting", "log": [], "error": None}
            S.result = None
            threading.Thread(target=run_slice, args=(req,), daemon=True).start()
            return {"ok": True}
        if ep == "export":
            path = export(req.get("kind", "gcode"), ask=bool(req.get("ask")))
            return {"cancelled": True} if path is None else {"path": path}
        if ep == "open":
            return open_dialog(req.get("kind", "project"))
        if ep == "open-path":
            return open_path(req["path"], as_project=not req.get("geometry_only"))
        if ep == "open-request":               # a second start of the app with a file
            with S.lock:
                S.pending.append(req["path"])
            _bring_to_front()
            return {"ok": True}
        if ep == "project/save":
            return save_project(req)
        if ep == "model/refine":
            return refine_model()
        if ep == "model/arrange":
            return arrange_model(req)
        if ep == "calibration/create":
            return create_calibration(req)
        if ep == "calibration/result":
            return {"value": calibration.result_value(req["test"], float(req["chosen"]), float(req.get("current") or 0))}
        if ep == "state":
            S.dirty = bool(req.get("dirty"))
            if req.get("title") and req["title"] != S.title:
                _set_title(req["title"])
            return {"ok": True}
        if ep == "quit":
            quit_app()
            return {"ok": True}
        if ep == "visible":                  # which built-in printers / filaments the lists show
            vis = {"printer": [str(x) for x in req.get("printer") or []],
                   "filament": [str(x) for x in req.get("filament") or []]}
            if not vis["printer"] or not vis["filament"]:
                raise ValueError("Pick at least one printer and one filament")
            _set_app_settings(visible=vis)
            return {"ok": True}
        if ep == "supporter":
            code = str(req.get("code") or "").strip()
            if not supporter.check(code):
                return {"ok": False, "error": "That code doesn't look right. Copy it from your Ko-fi thank-you message."}
            _set_app_settings(supporter_code=code.upper())
            return {"ok": True}
        if ep == "open-release":                 # only ever opens ArcLayer's own release page
            webbrowser.open(updates.status()["url"] if updates.status()["url"].startswith("https://github.com/" + updates.REPO)
                            else updates.PAGE)
            return {"ok": True}
        if ep == "update-check":
            updates.check(force=True)
            return updates.status()
        if ep == "donate":
            if DONATE_URL:                # only ever opens this one link
                webbrowser.open(DONATE_URL)
            return {"ok": bool(DONATE_URL)}
        if ep == "report":                       # Report a problem: a .zip + a ready email (nothing is sent by the app)
            pic = req.get("picture") or ""
            png = base64.b64decode(pic.split(",", 1)[1]) if pic.startswith("data:image/png;base64,") else None
            job = S.job or {}
            extra = {"printer": (req.get("settings") or {}).get("printer_name", ""),
                     "connected": getattr(S.printer, "connected", False),
                     "printer_kind": S.printer.status().get("kind", "") if getattr(S.printer, "connected", False) else "",
                     "last_slice": job.get("state"), "last_error": (job.get("error") or "")[:500]}
            settings = dict(req.get("settings") or {})
            settings["slice_log_tail"] = (job.get("log") or [])[-60:]
            r = report_mod.build({**req, "picture": png}, extra, settings)
            if os.name == "nt" and req.get("open", True):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(r["path"])])
                if r["mailto"]:
                    try:
                        os.startfile(r["mailto"])
                    except OSError:
                        r["mailto_failed"] = True
            return r
        if ep == "open-folder":
            path = req.get("path") or OUTPUT
            if os.name == "nt":
                if os.path.isfile(path):
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                else:
                    os.startfile(os.path.normpath(path))
            return {"ok": True}
        if ep == "device/connect":
            _connect(req["printer"])
            return S.printer.status()
        if ep == "device/use":               # switch the Device tab to another (connected or known) printer
            _connect(req["printer"])
            return {"ok": True}
        if ep == "device/disconnect":
            dev = S.printer
            dev.disconnect()
            gone = [s for s, d in S.devices.items() if d is dev]
            for s in gone:
                S.devices.pop(s, None)
                nm = S.dev_names.pop(s, None)
                _set_app_settings(autoconnect=[n for n in _autoconnect_names() if n != nm])
            others = [d for d in S.devices.values() if d.connected]
            if others:                       # show another connected printer
                serial = next(s for s, d in S.devices.items() if d is others[0])
                _connect(S.dev_names[serial])
            else:
                S.printer = BambuPrinter()
            return {"ok": True}
        if ep == "device/temp":
            S.printer.set_temp(req["what"], req["temp"])
            return {"ok": True}
        if ep == "device/speed":
            S.printer.set_speed(req["level"])
            return {"ok": True}
        if ep == "intro-seen":
            _set_app_settings(intro_seen=True)
            return {"ok": True}
        if ep == "send-options":
            _set_app_settings(send_options={k: bool(req[k]) for k in SEND_KEYS if k in req})
            return {"ok": True}
        if ep == "plate":
            if req.get("plate") not in PLATES:
                raise ValueError("Unknown plate")
            _set_app_settings(plate=req["plate"])
            return {"ok": True}
        if ep == "runout":
            mode = req.get("mode", "pause")
            if mode not in RUNOUT_MODES:
                raise ValueError("Unknown runout mode")
            order = parse_order(req.get("order")) if mode == "order" else \
                (_app_settings().get("runout") or {}).get("order") or [1, 2, 3, 4]
            _set_app_settings(runout={"mode": mode, "order": order})
            sent = False
            if S.printer.connected:              # the printer's own auto refill is on only in "printer" mode
                S.printer.set_auto_refill(mode == "printer")
                sent = True
            return {"ok": True, "order": order, "printer_told": sent}
        if ep == "camera/scan":
            from .camera import scan_rtsp
            skip = (S.cams.printer[0],) if S.cams.printer else ()
            return {"found": scan_rtsp(skip)}
        if ep == "camera/test":
            from .camera import test_url
            url = str(req.get("url") or "").strip()
            if not re.match(r"^(rtsp|rtsps|http|https)://", url, re.I):
                raise ValueError("The camera address must start with rtsp:// or http://")
            ok, msg = test_url(url)
            return {"ok": ok, "message": msg}
        if ep == "camera/config":
            url = str(req.get("url") or "").strip()
            if url and not re.match(r"^(rtsp|rtsps|http|https)://", url, re.I):
                raise ValueError("The camera address must start with rtsp:// or http://")
            _set_app_settings(camera_url=url)
            S.cams.extra_url = url
            S.cams.restart("extra")
            return {"ok": True}
        if ep == "device/command":
            S.printer.command(req["command"])
            return {"ok": True}
        if ep == "device/send":
            return {"remote": send_to_printer(req)}
        raise ValueError(f"Unknown endpoint {ep}")


# ------------------------------------------------------------------ when a spool runs out
RUNOUT_MODES = ("pause", "printer", "order")


def parse_order(text):
    """'1>3>4>2', '1 3 4 2' or '1,3,4,2' -> [1, 3, 4, 2] (slot numbers from 1)."""
    nums = [int(x) for x in re.findall(r"\d+", str(text or ""))]
    if not nums:
        raise ValueError("Type the slots in the order to use them, e.g. 1>3>4>2")
    if len(set(nums)) != len(nums):
        raise ValueError("Each slot can be in the order only once")
    if any(not 1 <= n <= 16 for n in nums):
        raise ValueError("Slots are numbered 1 to 4 (AMS lite), up to 16 with more AMS units")
    return nums


def next_slot(order, current, trays, tried=()):
    """The slot (0-based index) to switch to when `current` ran out: the next one after it in the order that has
    filament in it. None if there is none. order: slot numbers from 1."""
    idx = [n - 1 for n in order]
    start = idx.index(current) + 1 if current in idx else 0
    have = {t["index"]: t for t in trays}
    for i in idx[start:]:
        t = have.get(i)
        if i == current or i in tried or not t or not t.get("type"):
            continue
        rem = t.get("remain")
        if isinstance(rem, (int, float)) and rem == 0:
            continue
        return i
    return None


class RunoutGuard:
    """Watches the printer. On a filament runout, in "order" mode, loads the next slot of the user's order
    and resumes. Runs inside the app, so the app must be open (it can be minimised) during the print."""

    def __init__(self):
        self.handled = {}
        self.events = []               # [{"time", "text"}] for the Device tab

    def note(self, text):
        print("runout:", text)
        self.events = (self.events + [{"time": time.time(), "text": text}])[-10:]

    def loop(self):
        while True:
            time.sleep(2)
            try:
                self.tick()
            except Exception as e:
                self.note(f"Could not switch the filament: {e}")
                time.sleep(10)

    def tick(self):
        for serial, p in list(S.devices.items()) or [("", S.printer)]:
            self.tick_one(serial, p)

    def tick_one(self, serial, p):
        cfg = _app_settings().get("runout") or {}
        if cfg.get("mode") != "order" or not p.connected:
            return
        who = f"{S.dev_names.get(serial)}: " if len(S.devices) > 1 and S.dev_names.get(serial) else ""
        say = lambda text: self.note(who + text)
        st = p.status()
        if not st.get("runout") or st.get("gcode_state") not in ("PAUSE", "FAILED"):
            if not st.get("runout"):
                self.handled.pop(serial, None)
            return
        key = (st.get("print_error"), st.get("subtask_name"), st.get("layer_num"))
        if key == self.handled.get(serial):
            return
        self.handled[serial] = key
        cur = st.get("tray_now", 255)
        order = cfg.get("order") or []
        nxt = next_slot(order, cur, st.get("ams") or [])
        cur_txt = f"slot {cur + 1}" if 0 <= cur < 16 else "the spool"
        if nxt is None:
            say(f"{cur_txt.capitalize()} ran out at layer {st.get('layer_num')}, and no slot later in your order "
                f"({'>'.join(map(str, order))}) has filament: the printer waits for you.")
            return
        temp = int(st.get("nozzle_target_temper") or st.get("nozzle_temper") or 220)
        say(f"{cur_txt.capitalize()} ran out at layer {st.get('layer_num')}: loading slot {nxt + 1}…")
        threading.Thread(target=self._switch, args=(p, nxt, temp, say), daemon=True).start()

    @staticmethod
    def _switch(p, nxt, temp, say):
        """Load the next slot, wait for it, resume. In its own thread: other printers keep being watched."""
        try:
            p.load_slot(nxt, temp, temp)
            t0 = time.time()
            while time.time() - t0 < 300:              # unloading the old one and loading the new one takes a while
                time.sleep(3)
                s2 = p.status()
                if s2.get("tray_now") == nxt and s2.get("gcode_state") in ("PAUSE", "FAILED", "RUNNING"):
                    break
                if not p.connected:
                    raise ConnectionError("lost the connection to the printer")
            else:
                say(f"Slot {nxt + 1} did not load within 5 minutes: the printer waits for you.")
                return
            time.sleep(3)
            if p.status().get("gcode_state") != "RUNNING":
                p.command("resume")
            say(f"Switched to slot {nxt + 1} and resumed the print.")
        except Exception as e:
            say(f"Could not switch the filament: {e}")

S.runout = RunoutGuard()


sync = sync_mod.Sync(profiles.ROOT, lambda: _app_settings(), lambda **kw: _set_app_settings(**kw), log=print)


def _sync_status():
    return {"dir": _app_settings().get("sync_dir") or "", "last": sync.last, "changes": sync.changes,
            "suggestions": sync_mod.suggestions()}


def _connect(name, active=True):
    """Connect to a printer (several can be connected at once). active: the Device tab shows and sends to it."""
    p = profiles.list_all()["printer"][name]
    kind = p.get("host_type") or profiles.guess_host(p)
    if kind == "bambu":
        serial = (p.get("serial") or "").strip()
        if not serial:
            raise ValueError("Fill in the printer's serial number (Printer settings → Connection)")
    else:
        if not (p.get("host") or "").strip():
            raise ValueError("Fill in the printer's address (Printer settings → Connection)")
        serial = kind + ":" + p["host"].strip()
    dev = S.devices.get(serial)
    if dev is not None and (kind == "bambu") != isinstance(dev, BambuPrinter):
        dev.disconnect()
        dev = None
    if dev is None:
        if kind == "bambu":
            dev = S.printer if (not S.devices and not S.printer.connected and isinstance(S.printer, BambuPrinter)) \
                else BambuPrinter()
        else:
            dev = hosts.KINDS[kind]()
        S.devices[serial] = dev
    S.dev_names[serial] = name
    if not dev.connected:
        if kind == "bambu":
            dev.connect(p.get("ip", ""), serial, p.get("access_code", ""))
        else:
            dev.connect(p["host"], p.get("api_key", ""), "maker", p.get("host_password", ""))
    if active:
        changed = S.printer is not dev
        S.printer = dev
        if kind == "bambu":
            S.cams.printer = (p.get("ip", "").strip(), p.get("access_code", "").strip(), p.get("model", ""))
        else:
            S.cams.printer = ("url", dev.camera_url(), p.get("model", ""))
        if changed:
            S.cams.restart("printer")
    names = _autoconnect_names()
    if name not in names:
        _set_app_settings(autoconnect=names + [name])     # reconnect by itself next time the app starts


def _autoconnect_names():
    a = _app_settings().get("autoconnect")
    return [a] if isinstance(a, str) and a else [x for x in (a or []) if isinstance(x, str)]


def _known_printers():
    """Printer presets with connection details, one per physical printer (by serial)."""
    out, seen = [], set()
    pr = profiles.list_all()["printer"]
    names = sorted(pr, key=lambda n: (S.dev_names.get((pr[n].get("serial") or "").strip()) != n, n))
    for n in names:
        p = pr[n]
        kind = p.get("host_type") or profiles.guess_host(p)
        if kind == "bambu":
            serial = (p.get("serial") or "").strip()
            if not serial or not (p.get("ip") or "").strip() or not (p.get("access_code") or "").strip():
                continue
        else:
            if not (p.get("host") or "").strip():
                continue
            serial = kind + ":" + p["host"].strip()
        if serial in seen:
            continue
        seen.add(serial)
        dev = S.devices.get(serial)
        st = dev.status() if dev else {}
        out.append({"name": S.dev_names.get(serial, n), "model": p.get("model", ""), "tail": serial[-4:],
                    "connected": bool(dev and dev.connected), "active": dev is S.printer and bool(dev),
                    "state": st.get("gcode_state"), "percent": st.get("mc_percent"),
                    "remaining": st.get("mc_remaining_time"), "layer": st.get("layer_num"),
                    "total": st.get("total_layer_num")})
    return out


def _autoconnect():
    """Reconnect to every printer that was connected when the app last closed (keeps trying while one is off)."""
    first = True
    while True:
        todo = [n for n in _autoconnect_names()
                if not any(d.connected and S.dev_names.get(s) == n for s, d in S.devices.items())]
        if not todo:
            return
        for name in todo:
            try:
                _connect(name, active=first or not S.printer.connected)
                first = False
            except KeyError:                       # that printer preset is gone
                _set_app_settings(autoconnect=[n for n in _autoconnect_names() if n != name])
            except Exception as e:
                print("autoconnect:", e)
        time.sleep(30)


def serve(port=0, open_file=None):
    S.cams.extra_url = _app_settings().get("camera_url") or ""
    threading.Thread(target=_autoconnect, daemon=True).start()
    threading.Thread(target=S.runout.loop, daemon=True).start()
    threading.Thread(target=sync.loop, daemon=True).start()
    if paths.FROZEN:
        updates.start()                                              # is there a newer ArcLayer? (once a day)          # presets shared with your other PCs     # spool runs out: switch in your order
    if open_file:
        S.pending.append(os.path.abspath(open_file))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, httpd.server_address[1]
