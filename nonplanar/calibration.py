"""Calibration prints, after OrcaSlicer's calibration guide.

Every test is printed with FLAT layers (cone angle 0). A test is either
  * a model (temperature tower, flow blocks, PA tower, retraction towers, max flow, VFA, tolerance)
    that goes through the normal slicer, plus hooks that change one setting by height or by block, or
  * toolpaths made directly (PA line and PA pattern), written by the same G-code writer.

The hooks (Calib.layer_start / speed / flow) are called by nonplanar.gcode.write_gcode.
Numbers are embossed with a 7-segment font so the print can be read without a table.
"""
import math

import numpy as np
import shapely
import trimesh
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union
from shapely.prepared import prep

from .slicer import Layer, Path

# ------------------------------------------------------------------ test list (drives the UI)

def _p(key, label, unit="", step=1, digits=0, **defaults):
    return {"key": key, "label": label, "unit": unit, "step": step, "digits": digits, "defaults": defaults}


TEMP_DEFAULTS = {"PLA": (230, 190), "PLA-CF": (230, 200), "PETG": (250, 230), "PETG-CF": (260, 240),
                 "ABS": (270, 240), "ASA": (270, 240), "TPU": (240, 210), "PA": (290, 260), "PC": (290, 260)}

TESTS = [
    {"id": "temp_tower", "group": "Filament", "name": "Temperature tower",
     "about": "A tower of 10 mm blocks, each printed at a different nozzle temperature (hottest at the bottom). "
              "Each block has a 45° overhang, a 10 mm bridge and its temperature on the side.",
     "read": "Pick the block with the cleanest overhang and bridge, sharp corners and no stringing, then check "
             "layer adhesion by trying to snap the tower: hotter bonds better.",
     "params": [_p("start", "Start (bottom)", "°C", 5), _p("end", "End (top)", "°C", 5), _p("step", "Step", "°C", 1)],
     "result": {"cat": "filament", "key": "nozzle_temp", "label": "Nozzle temperature (other layers)"}},
    {"id": "flow", "group": "Filament", "name": "Flow rate",
     "about": "Square blocks, each printed with the current flow ratio changed by the percentage written in "
              "front of it. Run the coarse pass first, apply it, then the fine pass.",
     "read": "Look at the top surfaces in good light: pick the smoothest one, with no gaps between lines "
             "(too little flow) and no ridges or rough patches (too much).",
     "params": [_p("start", "From", "%", 1), _p("end", "To", "%", 1), _p("step", "Step", "%", 0.5, 1)],
     "presets": [{"label": "Pass 1 (coarse)", "values": {"start": -20, "end": 20, "step": 5}},
                 {"label": "Pass 2 (fine)", "values": {"start": -5, "end": 5, "step": 1}},
                 {"label": "YOLO (0.5 %)", "values": {"start": -4, "end": 3.5, "step": 0.5}}],
     "result": {"cat": "filament", "key": "flow", "label": "Flow ratio", "mode": "flow"}},
    {"id": "first_layer", "group": "Filament", "name": "First layer",
     "about": "One-layer squares, each printed with the first-layer flow changed by the number cut into it. "
              "Also shows how even your bed levelling and first-layer squish are.",
     "read": "Feel and look at each square: pick the one that is smooth and closed, with no gaps between the lines "
             "(too little) and no ridges or a rough, shiny surface (too much). Sets the process's first layer flow ratio.",
     "params": [_p("start", "From", "%", 1), _p("end", "To", "%", 1), _p("step", "Step", "%", 0.5, 1)],
     "presets": [{"label": "Coarse", "values": {"start": -15, "end": 15, "step": 5}},
                 {"label": "Fine", "values": {"start": -6, "end": 6, "step": 2}}],
     "result": {"cat": "process", "key": "first_layer_flow_ratio", "label": "First layer flow ratio", "mode": "flow"}},
    {"id": "top_flow", "group": "Filament", "name": "Top surface flow",
     "about": "Blocks whose top surface (only) is printed with more or less flow, the change written in front of "
              "each. Run it after the Flow rate test: it fine-tunes how full and smooth the top looks.",
     "read": "Look at the tops at a low angle in good light: pick the smoothest one, with no gaps between lines and "
             "no ridges. Sets the process's top surface flow ratio.",
     "params": [_p("start", "From", "%", 1), _p("end", "To", "%", 1), _p("step", "Step", "%", 0.5, 1)],
     "presets": [{"label": "Coarse", "values": {"start": -10, "end": 10, "step": 5}},
                 {"label": "Fine", "values": {"start": -4, "end": 4, "step": 2}}],
     "result": {"cat": "process", "key": "top_surface_flow_ratio", "label": "Top surface flow ratio", "mode": "flow"}},
    {"id": "ironing", "group": "Filament", "name": "Ironing",
     "about": "Blocks whose tops are ironed with a different ironing flow, written in front of each.",
     "read": "Pick the smoothest, most even top: too little ironing flow leaves the lines visible, too much leaves "
             "ridges or blobs at the edges. Sets the process's ironing flow (and turns ironing on).",
     "params": [_p("start", "From", "%", 1), _p("end", "To", "%", 1), _p("step", "Step", "%", 0.5, 1)],
     "result": {"cat": "process", "key": "ironing_flow", "label": "Ironing flow", "mode": "pct"}},
    {"id": "pa_line", "group": "Filament", "name": "Pressure advance: line",
     "about": "One line per pressure-advance value, each printed slow → fast → slow on the first layer, "
              "with the value next to every other line. Quick and uses little filament.",
     "read": "Pick the line that stays the same width through both speed changes: no bulge where it slows "
             "down and no thin spot where it speeds up.",
     "params": [_p("start", "Start K", "", 0.001, 3), _p("end", "End K", "", 0.001, 3), _p("step", "Step", "", 0.001, 3)],
     "result": {"cat": "filament", "key": "pressure_advance", "label": "Pressure advance (K)", "mode": "pa"}},
    {"id": "pa_pattern", "group": "Filament", "name": "Pressure advance: pattern",
     "about": "Nested chevrons (“<”), one per pressure-advance value, a few layers tall and printed fast, "
              "with the value above each one. Shows the effect on real corners.",
     "read": "Pick the chevron with the sharpest tip and walls of even width: rounded or bulging tips mean too "
             "little, gaps or thin walls just after the tip mean too much.",
     "params": [_p("start", "Start K", "", 0.001, 3), _p("end", "End K", "", 0.001, 3), _p("step", "Step", "", 0.001, 3)],
     "result": {"cat": "filament", "key": "pressure_advance", "label": "Pressure advance (K)", "mode": "pa"}},
    {"id": "pa_tower", "group": "Filament", "name": "Pressure advance: tower",
     "about": "A hollow square tower printed fast; the pressure-advance value goes up every 1 mm of height.",
     "read": "Measure the height where the corners look sharpest without gaps next to them, and pick that "
             "height in the list below.",
     "params": [_p("start", "Start K", "", 0.001, 3), _p("end", "End K", "", 0.001, 3), _p("step", "Step per mm", "", 0.001, 3)],
     "result": {"cat": "filament", "key": "pressure_advance", "label": "Pressure advance (K)", "mode": "pa"}},
    {"id": "retraction", "group": "Filament", "name": "Retraction",
     "about": "Two thin towers on a base; the retraction length goes up every 1 mm of height.",
     "read": "Find the lowest height where the strings between the towers stop, and pick that height below. "
             "Use the shortest length that works.",
     "params": [_p("start", "Start", "mm", 0.1, 2), _p("end", "End", "mm", 0.1, 2), _p("step", "Step per mm", "mm", 0.05, 2)],
     "result": {"cat": "printer", "key": "retract_length", "label": "Retraction length"}},
    {"id": "max_flow", "group": "Filament", "name": "Max volumetric speed",
     "about": "A thin-walled box printed with one wall; the flow (and so the speed) goes up every 1 mm of height.",
     "read": "Find the height where the wall first turns rough, gappy or underextruded and pick it below. The "
             "app sets about 85 % of that flow as the filament's limit.",
     "params": [_p("start", "Start", "mm³/s", 0.5, 1), _p("end", "End", "mm³/s", 0.5, 1), _p("step", "Step per mm", "mm³/s", 0.1, 1)],
     "result": {"cat": "filament", "key": "max_volumetric_speed", "label": "Max volumetric speed", "mode": "mvs"}},
    {"id": "nonplanar", "group": "Non-planar", "name": "Non-planar: wavy cup",
     "about": "A cup with a wavy rim, printed in the 'Follow the top surface' mode: the lower layers are flat, the "
              "top layers bend so the last lines run along the waves instead of stepping up and down. The wall flares "
              "out a little, and the rim's slope is kept within what your toolhead clears.",
     "read": "Look at the rim from the side: smooth curved lines along the waves (no stair steps) means it works. "
             "If the slicer had to smooth the waves (see the log), measure your hotend under Printer settings → "
             "Toolhead clearance: a nozzle that sticks out further allows steeper waves.",
     "params": [_p("waves", "Waves on the rim", "", 1), _p("amp", "Wave height", "mm", 0.5, 1),
                _p("height", "Height", "mm", 5), _p("flare", "Wall flare (from vertical)", "°", 5)],
     "result": None},
    {"id": "vfa", "group": "Advanced", "name": "VFA (speed tower)",
     "about": "A hollow rounded tower; the wall speed goes up every 5 mm to show speeds that cause vertical "
              "fine artifacts (ripples from vibration).",
     "read": "Find the bands with the smoothest walls and pick one: its speed is a good outer wall speed.",
     "params": [_p("start", "Start", "mm/s", 5), _p("end", "End", "mm/s", 5), _p("step", "Step per 5 mm", "mm/s", 5)],
     "result": {"cat": "process", "key": "outer_wall_speed", "label": "Outer wall speed"}},
    {"id": "tolerance", "group": "Advanced", "name": "Tolerance",
     "about": "A plate with six hexagonal holes (10 mm across flats plus the clearance written next to each) and a "
              "10 mm hex peg. Run this last, after the other calibrations.",
     "read": "Try the peg in each hole. The smallest clearance it slides into is the clearance to design into "
             "parts that must fit together.",
     "params": [], "result": None},
    {"id": "cornering", "group": "Advanced", "name": "Cornering (jerk / junction deviation)", "available": False,
     "about": "Not built yet. On Bambu printers it isn't needed: the firmware sets cornering itself and ignores "
              "Marlin's M205 jerk and junction-deviation commands. On Marlin / Klipper printers use the printer's "
              "own cornering test for now."},
    {"id": "input_shaping", "group": "Advanced", "name": "Input shaping", "available": False,
     "about": "Not built yet. Bambu printers measure their own vibration: tick “Vibration compensation” under "
              "Device → Send print. Klipper printers: use an accelerometer (SHAPER_CALIBRATE) or the ringing tower "
              "from the Klipper docs."},
    {"id": "adaptive_pa", "group": "Advanced", "name": "Adaptive pressure advance", "available": False,
     "about": "Not built yet. It needs firmware that switches pressure advance by flow and acceleration (Klipper, "
              "newer Marlin); Bambu firmware has one K value. Use the PA tests, or on Bambu printers tick “Flow "
              "dynamics calibration” under Device → Send print to let the printer measure K itself."},
]
TEST_BY_ID = {t["id"]: t for t in TESTS}


def defaults(test_id, filament_type="PLA", nozzle=0.4):
    """Default parameters for a test and filament type."""
    t = (filament_type or "PLA").upper()
    if test_id == "temp_tower":
        hi, lo = TEMP_DEFAULTS.get(t, TEMP_DEFAULTS["PLA"])
        return {"start": hi, "end": lo, "step": 5}
    if test_id == "flow":
        return {"start": -20, "end": 20, "step": 5}
    if test_id == "first_layer":
        return {"start": -15, "end": 15, "step": 5}
    if test_id == "top_flow":
        return {"start": -10, "end": 10, "step": 5}
    if test_id == "ironing":
        return {"start": 5, "end": 25, "step": 5}
    if test_id in ("pa_line", "pa_pattern"):
        return {"start": 0.0, "end": 0.1 if t != "TPU" else 0.5, "step": 0.005 if t != "TPU" else 0.025}
    if test_id == "pa_tower":
        return {"start": 0.0, "end": 0.1 if t != "TPU" else 0.5, "step": 0.002 if t != "TPU" else 0.01}
    if test_id == "retraction":
        return {"start": 0.0, "end": 2.0 if t != "TPU" else 4.0, "step": 0.1 if t != "TPU" else 0.2}
    if test_id == "max_flow":
        k = (nozzle / 0.4) ** 2
        return {"start": round(5 * k, 1), "end": round(25 * k, 1) if t != "TPU" else 8, "step": round(0.5 * k, 1)}
    if test_id == "vfa":
        return {"start": 40, "end": 200, "step": 10}
    if test_id == "nonplanar":
        return {"waves": 3, "amp": 2.0, "height": 25, "flare": 20}
    return {}


def values(p):
    """start..end in steps (either direction), rounded to the step's precision."""
    a, b, st = float(p["start"]), float(p["end"]), abs(float(p["step"]))
    if st <= 0:
        raise ValueError("The step must be more than 0")
    n = int(math.floor(abs(b - a) / st + 1e-6)) + 1
    if n > 200:
        raise ValueError(f"That is {n} steps: use a bigger step or a smaller range (200 at most)")
    sgn = 1 if b >= a else -1
    dec = max(0, -int(math.floor(math.log10(st) + 1e-9))) + 2
    return [round(a + sgn * k * st, dec) for k in range(n)]


def fmt(v):
    """Short label for a value: 230, -5, +5, 0.025 -> .025, 3.5."""
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s.replace("0.", ".", 1) if s.startswith(("0.", "-0.")) else s


# ------------------------------------------------------------------ geometry

def _orient_tri(t, up=True):
    c = np.asarray(t.exterior.coords)[:3]
    cross = (c[1, 0] - c[0, 0]) * (c[2, 1] - c[0, 1]) - (c[1, 1] - c[0, 1]) * (c[2, 0] - c[0, 0])
    return c if (cross > 0) == up else c[::-1]


def prism(shape, axis="z", a0=0.0, a1=1.0):
    """Watertight mesh of a 2-D shape (with holes) extruded from a0 to a1 along an axis.
    axis z: shape in (x, y). axis y: shape in (x, z). axis x: shape in (y, z)."""
    polys = [shape] if isinstance(shape, Polygon) else list(getattr(shape, "geoms", []))
    verts, faces, index = [], [], {}

    def vid(u, v, w):
        key = (round(u, 6), round(v, 6), round(w, 6))
        if key not in index:
            index[key] = len(verts)
            verts.append(key)
        return index[key]

    for poly in polys:
        if poly.is_empty or poly.area < 1e-9:
            continue
        poly = shapely.geometry.polygon.orient(poly, 1.0)
        tris = shapely.constrained_delaunay_triangles(poly)
        for t in getattr(tris, "geoms", []):
            if t.area < 1e-12:
                continue
            c = _orient_tri(t, True)
            faces.append([vid(*c[0], a1), vid(*c[1], a1), vid(*c[2], a1)])          # top
            faces.append([vid(*c[2], a0), vid(*c[1], a0), vid(*c[0], a0)])          # bottom
        for ring in [poly.exterior, *poly.interiors]:
            c = np.asarray(ring.coords)
            for p, q in zip(c[:-1], c[1:]):
                i0, i1 = vid(p[0], p[1], a0), vid(q[0], q[1], a0)
                j0, j1 = vid(p[0], p[1], a1), vid(q[0], q[1], a1)
                faces.append([i0, i1, j1])
                faces.append([i0, j1, j0])
    uvw = np.asarray(verts, float)
    if axis == "z":
        v = uvw
    elif axis == "y":
        v = np.column_stack([uvw[:, 0], uvw[:, 2], uvw[:, 1]])
    else:
        v = np.column_stack([uvw[:, 2], uvw[:, 0], uvw[:, 1]])
    m = trimesh.Trimesh(v, np.asarray(faces), process=True)
    trimesh.repair.fix_normals(m)
    if m.volume < 0:
        m.invert()
    return m


# 7-segment font: segments in a 0..1 x 0..2 cell
_SEG = {"a": ((0, 2), (1, 2)), "b": ((1, 2), (1, 1)), "c": ((1, 1), (1, 0)), "d": ((0, 0), (1, 0)),
        "e": ((0, 0), (0, 1)), "f": ((0, 1), (0, 2)), "g": ((0, 1), (1, 1))}
_DIG = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd", "6": "afgedc",
        "7": "abc", "8": "abcdefg", "9": "abcdfg", "-": "g"}


def _glyph_strokes(ch):
    """[(p, q)] in a cell 1 wide and 2 tall, plus the advance width."""
    if ch == ".":
        return [((0.0, 0.0), (0.12, 0.0))], 0.5
    if ch == "+":
        return [_SEG["g"], ((0.5, 0.5), (0.5, 1.5))], 1.6
    if ch == " ":
        return [], 1.0
    return [_SEG[s] for s in _DIG.get(ch, "")], 1.6


def text_strokes(text, height, x=0.0, y=0.0):
    """Line segments of a label `height` mm tall (for single-line printing), bottom-left at (x, y)."""
    sc = height / 2.0
    out, cx = [], x
    for ch in text:
        segs, adv = _glyph_strokes(ch)
        for (p, q) in segs:
            out.append(((cx + p[0] * sc, y + p[1] * sc), (cx + q[0] * sc, y + q[1] * sc)))
        cx += adv * sc
    return out, cx - x - 0.6 * sc


def text_shape(text, height, stroke, x=0.0, y=0.0):
    """Filled label (stroke mm thick lines), bottom-left of the glyph box at (x, y)."""
    inset = stroke / 2
    segs, width = text_strokes(text, height - stroke, x + inset, y + inset)
    parts = []
    for p, q in segs:
        if np.hypot(q[0] - p[0], q[1] - p[1]) < 0.3:              # the dot
            parts.append(box(p[0] - inset, p[1] - inset, p[0] + inset, p[1] + inset))
        else:
            parts.append(LineString([p, q]).buffer(inset, cap_style="square", join_style="mitre"))
    return unary_union(parts), width + stroke


def _box(x0, y0, z0, x1, y1, z1):
    return prism(box(x0, y0, x1, y1), "z", z0, z1)


def _hexagon(across_flats, cx=0.0, cy=0.0):
    r = across_flats / math.sqrt(3)
    return Polygon([(cx + r * math.cos(math.radians(60 * k)), cy + r * math.sin(math.radians(60 * k))) for k in range(6)])


# ------------------------------------------------------------------ the calibration object

class Calib:
    """A built test: parts (model tests) or direct layers, the setting changes it needs,
    the steps (value per block / height band) and the G-code hooks."""

    def __init__(self, test, params, nozzle):
        self.test, self.params, self.nozzle = test, dict(params), float(nozzle)
        self.meta = TEST_BY_ID[test]
        self.parts = []              # [(name, mesh)]
        self.overrides = {}          # Settings attributes
        self.notes = []              # what the test changes, for the user
        self.steps = []              # [{"label", "value", "z0", "z1"}]
        self.regions = []            # flow test: [(polygon in model XY, multiplier)]
        self.band = None             # "temp" | "pa" | "retract" | "speed" | "mvs" | None
        self.band_from = 0.0         # heights below this use the first value
        self.direct = None           # function(s) -> list of Layer (PA line / pattern)
        self.flat = True             # printed with flat layers (all but the non-planar test)
        self.region_kinds = None     # line types the per-block flow applies to (None = all)
        self._M = np.eye(4)
        self._placed = []
        self._last = None

    # -- set up for one slice
    def bind(self, M):
        """M: the placement matrix (model -> bed). Places the flow regions."""
        self._M = np.asarray(M, float)
        # the app centres the model on X/Y before placing it: undo that, then place
        c0 = self.center()
        a = [self._M[0, 0], self._M[0, 1], self._M[1, 0], self._M[1, 1], self._M[0, 3], self._M[1, 3]]
        self._placed = [(prep(affinity.affine_transform(affinity.translate(poly, -c0[0], -c0[1]), a)), mult)
                        for poly, mult in self.regions]
        self._last = None

    def center(self):
        """X/Y centre of the test's parts (in its own coordinates)."""
        lo = np.min([m.bounds[0] for _, m in self.parts], axis=0)
        hi = np.max([m.bounds[1] for _, m in self.parts], axis=0)
        return (lo[:2] + hi[:2]) / 2

    def _step_at(self, z):
        if not self.steps:
            return None
        for st in self.steps:
            if z < st["z1"]:
                return st
        return self.steps[-1]

    # -- hooks used by write_gcode
    def layer_start(self, n, zp, thickness, w):
        """G-code lines at the start of a layer; may also change the writer's settings."""
        if self.band is None:
            return []
        zmid = zp - thickness / 2
        st = self._step_at(max(zmid, self.band_from))
        if st is None or st is self._last:
            return []
        self._last = st
        v = st["value"]
        if self.band == "temp":
            return [f"M104 S{int(round(v))} ; calibration: {st['label']}"]
        if self.band == "pa":
            return [f"M900 K{v:.4f} L1000 M10 ; calibration: K {fmt(v)}"]
        if self.band == "retract":
            w.s.retract_length = float(v)
            return [f"; calibration: retraction {v:.2f} mm"]
        return [f"; calibration: {st['label']}"]

    def speed(self, p, zp, thickness, sp, n):
        if self.band not in ("speed", "mvs") or n == 0:
            return sp
        st = self._step_at(max(zp - thickness / 2, self.band_from))
        if st is None:
            return sp
        if self.band == "speed":
            return float(st["value"]) if p.kind in ("outer_wall", "inner_wall", "overhang_wall") else sp
        area = thickness * (p.width - thickness * (1 - math.pi / 4))
        return float(st["value"]) / max(area, 1e-6)

    def flow(self, p):
        if not self._placed or (self.region_kinds and p.kind not in self.region_kinds):
            return 1.0
        c = Point(np.asarray(p.pts, float).mean(0))
        for poly, mult in self._placed:
            if poly.contains(c):
                return mult
        return 1.0

    def summary(self):
        zs = [m.bounds[1][2] for _, m in self.parts] if self.parts else [0]
        return {"test": self.test, "name": self.meta["name"], "params": self.params, "nozzle": self.nozzle, "flat": self.flat,
                "steps": self.steps, "notes": self.notes, "read": self.meta.get("read", ""),
                "result": self.meta.get("result"), "direct": self.direct is not None,
                "height": round(float(max(zs)), 1)}


def _flat(c, notes=None, **kw):
    c.overrides.update({"mode": "conical", "cone_angle": 0.0, "enable_support": False, "ironing_type": "no ironing",
                        "slow_down_layers": 0, "enable_pressure_advance": False, "skirt_loops": 1,
                        "brim_type": "no_brim", **kw})
    c.notes.insert(0, "Flat layers (cone angle 0)")
    if notes:
        c.notes.extend(notes)


# ------------------------------------------------------------------ the tests

def build(test, params, nozzle=0.4, layer_height=0.2):
    if test not in TEST_BY_ID:
        raise ValueError(f"Unknown calibration {test!r}")
    meta = TEST_BY_ID[test]
    if meta.get("available") is False:
        raise ValueError(meta["about"])
    c = Calib(test, params, nozzle)
    stroke = max(0.9, 2.2 * nozzle)                  # embossed numbers: at least two lines wide
    globals()["_build_" + test](c, params, nozzle, stroke, layer_height)
    return c


def _build_temp_tower(c, p, nz, stroke, lh):
    temps = values(p)
    base, H, D, W = 1.0, 10.0, 18.0, 36.0
    c.parts.append(("Base", _box(-3, -2, 0, W + 3, D + 2, base)))
    # block profile in X/Z (extruded along Y): core, 45° overhang on the left, 13 mm bridge tunnel
    outer = Polygon([(9, 0), (W, 0), (W, H), (0, H), (0, H - 1), (9, 1)])
    tunnel = box(15, 1.2, 28, 6.0)
    prof = outer.difference(tunnel)
    for k, t in enumerate(temps):
        z0 = base + k * H
        blk = prism(affinity.translate(prof, 0, z0), "y", 0, D)
        c.parts.append((f"{int(t)} °C", blk))
        # the temperature, embossed on the right face (in Y/Z, standing out 0.6 mm)
        lab, wdt = text_shape(str(int(round(t))), 6.4, stroke)
        lab = affinity.translate(lab, (D - wdt) / 2, z0 + 1.8)
        c.parts.append((f"label {int(t)}", prism(lab, "x", W, W + 0.6)))
        c.steps.append({"label": f"{int(round(t))} °C (block {k + 1} from the bottom)", "value": float(t),
                        "z0": z0, "z1": z0 + H})
    c.steps[0]["z0"] = 0.0
    c.band = "temp"
    t0 = int(round(temps[0]))
    _flat(c, [f"Nozzle {t0} °C at the start, then {fmt(temps[1] - temps[0]) if len(temps) > 1 else 0} °C "
              f"per 10 mm block"], nozzle_temp=t0, nozzle_temp_first=t0)


def _build_flow(c, p, nz, stroke, lh):
    mods = values(p)
    size, gap, tab = 28.0, 6.0, 10.0
    h = round(max(2.6, 10 * lh), 2)
    t0 = round(2 * lh, 2)                            # the tab under the number: 2 layers
    cols = min(len(mods), 3 if len(mods) <= 9 else 4)
    for k, m in enumerate(mods):
        r, col = divmod(k, cols)
        x0, y0 = col * (size + gap), -r * (size + gap + tab)
        blk = box(x0, y0, x0 + size, y0 + size)
        c.parts.append((f"{fmt(m)} %", prism(blk, "z", 0, h)))
        # a thin tab in front of the block, joined to it, with the number standing on top:
        # the number is easy to read and comes off the bed together with the block
        c.parts.append((f"tab {fmt(m)}", prism(box(x0, y0 - tab, x0 + size, y0 + 0.5), "z", 0, t0)))
        lab, wdt = text_shape(("+" if m > 0 else "") + fmt(m), 6.0, stroke)
        lab = affinity.translate(lab, x0 + (size - wdt) / 2, y0 - tab + 2.0)
        c.parts.append((f"label {fmt(m)}", prism(lab, "z", t0, t0 + max(0.6, 3 * lh))))
        c.regions.append((blk.buffer(0.5), 1.0 + m / 100.0))
        c.steps.append({"label": f"{'+' if m > 0 else ''}{fmt(m)} %", "value": float(m), "z0": 0, "z1": h})
    _flat(c, ["Each block's flow is the current flow ratio × (100 + its number) %", "5 top layers, monotonic top surface"],
          top_layers=5, bottom_layers=3, top_surface_pattern="monotonic", skirt_loops=1)


def _tab_blocks(c, items, h, lh, stroke, size=28.0, gap=6.0, tab=10.0):
    """Square blocks with a thin numbered tab in front of each (like the flow test).
    items: [(label text, flow multiplier for the block, step dict)]."""
    t0 = round(2 * lh, 2)
    cols = min(len(items), 3 if len(items) <= 9 else 4)
    for k, (text, mult, step) in enumerate(items):
        r, col = divmod(k, cols)
        x0, y0 = col * (size + gap), -r * (size + gap + tab)
        blk = box(x0, y0, x0 + size, y0 + size)
        c.parts.append((text, prism(blk, "z", 0, h)))
        c.parts.append((f"tab {text}", prism(box(x0, y0 - tab, x0 + size, y0 + 0.5), "z", 0, t0)))
        lab, wdt = text_shape(text, 6.0, stroke)
        lab = affinity.translate(lab, x0 + (size - wdt) / 2, y0 - tab + 2.0)
        c.parts.append((f"label {text}", prism(lab, "z", t0, t0 + max(0.6, 3 * lh))))
        c.regions.append((blk.buffer(0.5), mult))
        c.steps.append({**step, "z0": 0, "z1": h})


def _build_first_layer(c, p, nz, stroke, lh):
    mods = values(p)
    h = round(nz / 2, 2)                          # exactly one layer
    size, gap = 30.0, 5.0
    cols = min(len(mods), 3 if len(mods) <= 9 else 4)
    for k, m in enumerate(mods):
        r, col = divmod(k, cols)
        x0, y0 = col * (size + gap), -r * (size + gap)
        blk = box(x0, y0, x0 + size, y0 + size)
        text = ("+" if m > 0 else "") + fmt(m)
        lab, wdt = text_shape(text, 10.0, max(stroke, 1.2))
        lab = affinity.translate(lab, x0 + (size - wdt) / 2, y0 + 3.0)      # cut into the square, near its front
        c.parts.append((f"{text} %", prism(blk.difference(lab), "z", 0, h)))
        c.regions.append((blk.buffer(0.5), 1.0 + m / 100.0))
        c.steps.append({"label": f"{text} %", "value": float(m), "z0": 0, "z1": h})
    _flat(c, ["One layer only: the first-layer settings (height, speed, temperature) are what is tested",
              "Each square's flow is the current first layer flow × (100 + its number) %"],
          first_layer_height=h, skirt_loops=1, bottom_surface_pattern="monotonic")


def _build_top_flow(c, p, nz, stroke, lh):
    mods = values(p)
    h = round(max(2.6, 10 * lh), 2)
    _tab_blocks(c, [(("+" if m > 0 else "") + fmt(m), 1.0 + m / 100.0,
                     {"label": f"{'+' if m > 0 else ''}{fmt(m)} %", "value": float(m)}) for m in mods], h, lh, stroke)
    c.region_kinds = ("top_surface",)
    _flat(c, ["Only the top surface's flow changes: the current top surface flow × (100 + the number) %",
              "4 top layers, monotonic line top surface"],
          top_layers=4, bottom_layers=3, top_surface_pattern="monotonicline")


def _build_ironing(c, p, nz, stroke, lh):
    pcts = values(p)
    if min(pcts) <= 0:
        raise ValueError("Ironing flow must be more than 0 %")
    h = round(max(2.6, 10 * lh), 2)
    base = 0.10
    _tab_blocks(c, [(fmt(v), (v / 100.0) / base, {"label": f"{fmt(v)} % ironing flow", "value": float(v)})
                    for v in pcts], h, lh, stroke)
    c.region_kinds = ("ironing",)
    c.overrides.update({"ironing_type": "top", "ironing_flow": base})
    _flat(c, ["Tops ironed, each block with the ironing flow written in front of it", "4 top layers"],
          top_layers=4, bottom_layers=3)
    c.overrides.update({"ironing_type": "top", "ironing_flow": base})     # _flat turns ironing off: back on


def _build_pa_tower(c, p, nz, stroke, lh):
    ks = values(p)
    base = 1.0
    height = base + len(ks) * 1.0
    c.parts.append(("PA tower", prism(box(0, 0, 40, 40), "z", 0, height)))
    for k, v in enumerate(ks):
        c.steps.append({"label": f"{base + k:.0f}–{base + k + 1:.0f} mm: K {v:g}", "value": float(v),
                        "z0": base + k, "z1": base + k + 1})
    c.band, c.band_from = "pa", base
    _flat(c, ["2 walls, no infill, no top: a hollow tower", "Walls at 120 mm/s, 5000 mm/s² so the corners show PA",
              f"K goes up by {fmt(abs(ks[1] - ks[0])) if len(ks) > 1 else 0} every 1 mm (from {base:g} mm)"],
          wall_count=2, infill_density=0.0, top_layers=0, bottom_layers=3, outer_wall_speed=120,
          inner_wall_speed=120, outer_wall_acceleration=5000, inner_wall_acceleration=5000, default_acceleration=5000,
          slow_down_for_layer_cooling=False, max_volumetric_speed=0.0)
    c.notes.append("No slow-down for layer cooling: each 40 mm layer still gets about 3 s to cool")


def _build_retraction(c, p, nz, stroke, lh):
    lens = values(p)
    base = 1.0
    height = base + len(lens) * 1.0
    c.parts.append(("Base", _box(-6, -6, 0, 40, 6, base)))
    for x in (0.0, 34.0):
        c.parts.append(("Tower", prism(Point(x, 0).buffer(2.5, 24), "z", 0, height)))
    for k, v in enumerate(lens):
        c.steps.append({"label": f"{base + k:.0f}–{base + k + 1:.0f} mm: {v:.2f} mm", "value": float(v),
                        "z0": base + k, "z1": base + k + 1})
    c.band, c.band_from = "retract", base
    _flat(c, [f"Retraction goes up by {abs(lens[1] - lens[0]) if len(lens) > 1 else 0:.2f} mm every 1 mm",
              "Retracts on every travel between the towers"], retract_min_travel=1.0, wall_count=2, skirt_loops=0)


def _build_max_flow(c, p, nz, stroke, lh):
    vols = values(p)
    base = 1.0
    height = base + len(vols) * 1.0
    c.parts.append(("Flow test", prism(box(0, 0, 70, 16).buffer(2, join_style="round"), "z", 0, height)))
    for k, v in enumerate(vols):
        c.steps.append({"label": f"{base + k:.0f}–{base + k + 1:.0f} mm: {v:g} mm³/s", "value": float(v),
                        "z0": base + k, "z1": base + k + 1})
    c.band, c.band_from = "mvs", base
    _flat(c, ["One wall, no infill or top (like a vase)", "Speed set from the flow for each 1 mm band; the filament's "
              "own limit is switched off for this test", "Cooling slow-down off"],
          wall_count=1, infill_density=0.0, top_layers=0, bottom_layers=1, max_volumetric_speed=0.0,
          slow_down_for_layer_cooling=False, skirt_loops=1)


def _build_vfa(c, p, nz, stroke, lh):
    speeds = values(p)
    band, base = 5.0, 1.0
    height = base + len(speeds) * band
    c.parts.append(("VFA tower", prism(box(-15, -15, 15, 15).buffer(5, join_style="round", quad_segs=16), "z", 0, height)))
    for k, v in enumerate(speeds):
        c.steps.append({"label": f"{base + k * band:.0f}–{base + (k + 1) * band:.0f} mm: {v:g} mm/s", "value": float(v),
                        "z0": base + k * band, "z1": base + (k + 1) * band})
    c.band, c.band_from = "speed", base
    _flat(c, ["2 walls, no infill or top", f"Wall speed changes every {band:g} mm"],
          wall_count=2, infill_density=0.0, top_layers=0, bottom_layers=3, slow_down_for_layer_cooling=False)


def _build_nonplanar(c, p, nz, stroke, lh):
    flare = max(0.0, min(45.0, float(p.get("flare", 20))))
    H = max(12.0, min(120.0, float(p.get("height", 25))))
    waves = int(max(0, min(24, round(float(p.get("waves", 3))))))
    amp = max(0.0, min(10.0, float(p.get("amp", 2.0))))
    c.parts.append(("Wavy cup", wavy_cup(H, flare, waves, amp=amp)))
    c.flat = False
    c.overrides.update({"mode": "surface", "surface_depth": 2 * amp + 0.5, "cone_angle": 0.0,
                        "enable_support": False, "ironing_type": "no ironing", "skirt_loops": 1, "brim_type": "no_brim",
                        "auto_center": True, "wall_count": 3, "infill_density": 0.15, "top_layers": 3})
    c.notes.extend(["Mode: follow the top surface (flat below, curved at the top)",
                    f"{waves} waves, {amp:g} mm high, on the rim" if waves else "Flat rim",
                    f"Wall flares {flare:g}° out from vertical",
                    "Steeper waves than your toolhead clears are smoothed (Printer settings → Toolhead clearance)"])


def wavy_cup(H, flare, waves, r0=14.0, wall=2.6, floor=1.6, amp=3.0, n=144, rows=40):
    """Watertight cup: a floor, a wall that flares out by `flare` degrees from vertical,
    and a rim that rises and falls `waves` times (amp mm) around the cup."""
    tf = math.tan(math.radians(flare))
    th = np.linspace(0, 2 * math.pi, n, endpoint=False)
    ztop = H - amp + amp * np.cos(waves * th) if waves else np.full(n, H)
    verts, faces = [], []

    def add(x, y, z):
        verts.append((x, y, z))
        return len(verts) - 1

    def ring(r_of_z, z):
        return [add(r_of_z(z[k]) * math.cos(th[k]), r_of_z(z[k]) * math.sin(th[k]), z[k]) for k in range(n)]

    r_out = lambda z: r0 + z * tf
    r_in = lambda z: r0 + z * tf - wall / math.cos(math.radians(flare))
    outer = [ring(r_out, ztop * s) for s in np.linspace(0, 1, rows + 1)]                 # bottom -> rim
    inner = [ring(r_in, floor + (ztop - floor) * s) for s in np.linspace(0, 1, rows + 1)]
    cb, cf = add(0, 0, 0), add(0, 0, floor)

    def strip(a, b, flip):
        for k in range(n):
            q = (k + 1) % n
            f1, f2 = [a[k], a[q], b[q]], [a[k], b[q], b[k]]
            faces.extend([f1[::-1], f2[::-1]] if flip else [f1, f2])

    for lo, hi in zip(outer[:-1], outer[1:]):
        strip(lo, hi, False)                      # outside wall
    for lo, hi in zip(inner[:-1], inner[1:]):
        strip(lo, hi, True)                       # inside wall
    strip(outer[-1], inner[-1], False)            # rim
    for k in range(n):
        q = (k + 1) % n
        faces.append([cb, outer[0][q], outer[0][k]])      # underside
        faces.append([cf, inner[0][k], inner[0][q]])      # floor inside
    m = trimesh.Trimesh(np.asarray(verts), np.asarray(faces), process=True)
    trimesh.repair.fix_normals(m)
    if m.volume < 0:
        m.invert()
    return m


TOLERANCES = [0.0, 0.05, 0.1, 0.15, 0.2, 0.3]


def _build_tolerance(c, p, nz, stroke, lh):
    af, pitch, T = 10.0, 15.0, 6.0
    plate = box(-pitch / 2 - 1, -11.5, len(TOLERANCES) * pitch - pitch / 2 + 1, 9.5)
    holes = [_hexagon(af + t, k * pitch, 2.5) for k, t in enumerate(TOLERANCES)]
    c.parts.append(("Plate", prism(plate.difference(unary_union(holes)), "z", 0, T)))
    for k, t in enumerate(TOLERANCES):
        lab, wdt = text_shape(fmt(t), 4.6, stroke)
        lab = affinity.translate(lab, k * pitch - wdt / 2, -10.3)
        c.parts.append((f"label {fmt(t)}", prism(lab, "z", T, T + max(0.4, 2 * lh))))
        c.steps.append({"label": f"{t:.2f} mm clearance", "value": t, "z0": 0, "z1": T})
    c.parts.append(("Hex peg", prism(_hexagon(af, (len(TOLERANCES) - 1) * pitch / 2, -24), "z", 0, 14)))
    _flat(c, ["Plain print: nothing changes during it"])


# ------------------------------------------------------------------ direct toolpaths (PA line / pattern)

def _thin_mesh(segments, w, h):
    """A flat mesh showing where direct toolpaths go (for the Prepare view and placement)."""
    shape = unary_union([LineString([p, q]).buffer(w / 2, cap_style="flat") for p, q in segments if p != q])
    return prism(shape, "z", 0, h)


def _label_paths(text, height, x, y, w, rotate=False):
    segs, _ = text_strokes(text, height, 0, 0)
    out = []
    for p, q in segs:
        if rotate:                   # read from the bottom up (text along +Y)
            p, q = (-p[1], p[0]), (-q[1], q[0])
        p, q = (p[0] + x, p[1] + y), (q[0] + x, q[1] + y)
        if np.hypot(q[0] - p[0], q[1] - p[1]) < 0.3:
            q = (p[0] + 0.4, p[1])
        out.append(Path("outer_wall", np.array([p, q], float), False, width=w, speed=20))
    return out


def _build_pa_line(c, p, nz, stroke, lh):
    ks = values(p)
    w = round(1.2 * nz, 2)
    gap = max(3.5, 8 * w)
    slow, fast = 20.0, 120.0
    L1, L2, L3 = 20.0, 40.0, 20.0
    x0 = -(L1 + L2 + L3) / 2
    y0 = -(len(ks) - 1) * gap / 2
    segs = []
    for k in range(len(ks)):
        y = y0 + k * gap
        segs.append(((x0, y), (x0 + L1 + L2 + L3, y)))
    # anchor frame lines at the speed changes, and the labels
    ytop = y0 + (len(ks) - 1) * gap
    marks = [((x0 + L1, y0 - 3), (x0 + L1, y0 - 1)), ((x0 + L1 + L2, y0 - 3), (x0 + L1 + L2, y0 - 1)),
             ((x0 + L1, ytop + 1), (x0 + L1, ytop + 3)), ((x0 + L1 + L2, ytop + 1), (x0 + L1 + L2, ytop + 3))]
    prime = ((x0 - 6, y0 - 4), (x0 - 6, ytop + 4))
    lab_h = min(3.0, gap * 1.4)

    def layers(s):
        h = s.first_layer_height
        paths = [Path("skirt", np.array(prime, float), False, width=w, speed=slow)]
        for k, kv in enumerate(ks):
            y = y0 + k * gap
            xs = [x0, x0 + L1, x0 + L1 + L2, x0 + L1 + L2 + L3]
            for (a, b), spd in zip(zip(xs[:-1], xs[1:]), (slow, fast, slow)):
                pth = Path("outer_wall", np.array([(a, y), (b, y)], float), False, width=w, speed=spd)
                if a == x0:
                    pth.gcode = f"M900 K{kv:.4f} L1000 M10 ; calibration: K {fmt(kv)}"
                paths.append(pth)
            if k % 2 == 0:
                paths += _label_paths(fmt(kv), lab_h, xs[-1] + 3, y - lab_h / 2, w * 0.9)
        for m in marks:
            paths.append(Path("outer_wall", np.array(m, float), False, width=w, speed=slow))
        return [Layer(0, h, paths, h)]

    c.direct = layers
    c.parts.append(("PA lines", _thin_mesh(segs + marks + [prime], w, lh)))
    for kv in ks:
        c.steps.append({"label": f"K {kv:g}", "value": float(kv), "z0": 0, "z1": lh})
    _flat(c, [f"First layer only, {slow:g} → {fast:g} → {slow:g} mm/s", "2000 mm/s² acceleration"],
          first_layer_speed=fast, first_layer_infill_speed=fast, initial_layer_acceleration=2000, skirt_loops=0)


def _build_pa_pattern(c, p, nz, stroke, lh):
    ks = values(p)
    w = round(1.1 * nz, 2)
    walls, arm, n_layers = 3, 20.0, 4
    a = arm / math.sqrt(2)
    pitch = walls * w * math.sqrt(2) + max(2.5, 4 * w)
    x0 = -(len(ks) - 1) * pitch / 2 - a / 2
    y0 = -a
    fast = 100.0

    def chevron(k, wall):
        x = x0 + k * pitch + wall * w * math.sqrt(2)
        return np.array([(x + a, y0), (x, y0 + a), (x + a, y0 + 2 * a)], float)

    segs = []
    for k in range(len(ks)):
        for wl in range(walls):
            q = chevron(k, wl)
            segs += [(tuple(q[0]), tuple(q[1])), (tuple(q[1]), tuple(q[2]))]
    lab_h = min(3.0, pitch * 0.9)

    def layers(s):
        out, z = [], 0.0
        for n in range(n_layers):
            h = s.first_layer_height if n == 0 else s.layer_height
            z += h
            paths = []
            if n == 0:   # a frame around the pattern holds it down
                xa, xb = x0 - 3, x0 + (len(ks) - 1) * pitch + a + walls * w * 2 + 3
                paths.append(Path("skirt", np.array([(xa, y0 - 3), (xb, y0 - 3), (xb, y0 + 2 * a + 3),
                                                      (xa, y0 + 2 * a + 3), (xa, y0 - 3)], float), True, width=w, speed=30))
            for k, kv in enumerate(ks):
                for wl in range(walls):
                    q = chevron(k, wl)
                    if wl % 2:
                        q = q[::-1]
                    pth = Path("outer_wall", q, False, width=w, speed=fast)
                    if wl == 0:
                        pth.gcode = f"M900 K{kv:.4f} L1000 M10 ; calibration: K {fmt(kv)}"
                    paths.append(pth)
            if n == 0:
                for k, kv in enumerate(ks):
                    paths += _label_paths(fmt(kv), lab_h, x0 + k * pitch + a / 2 + lab_h / 2, y0 + 2 * a + 6,
                                          w * 0.9, rotate=True)
            out.append(Layer(n, z, paths, h))
        return out

    c.direct = layers
    c.parts.append(("PA pattern", _thin_mesh(segs, w, n_layers * lh)))
    for kv in ks:
        c.steps.append({"label": f"K {kv:g}", "value": float(kv), "z0": 0, "z1": n_layers * lh})
    _flat(c, [f"{n_layers} layers of {walls}-wall chevrons at {fast:g} mm/s"],
          first_layer_speed=fast, first_layer_infill_speed=fast, initial_layer_acceleration=2000,
          outer_wall_acceleration=5000, default_acceleration=5000, skirt_loops=0, slow_down_for_layer_cooling=False)


def result_value(test, chosen, current):
    """The setting value for the chosen step. current: the setting's value now."""
    mode = (TEST_BY_ID[test].get("result") or {}).get("mode")
    if mode == "flow":
        return round(float(current) * (1 + chosen / 100.0), 4)
    if mode == "mvs":
        return round(chosen * 0.85, 1)
    if mode == "param:cone":
        return chosen
    if mode == "pct":
        return round(chosen / 100.0, 4)
    return chosen
