"""Bend flat toolpaths back into curved 3D paths and write G-code.

Extrusion is computed per segment from the TRUE local layer thickness (the gap
between neighbouring curved layers, measured perpendicular to the layer), so
layers that get thinner or thicker while bending still get the right amount of plastic.
"""
import math
import re

import numpy as np
from shapely.geometry import LineString
from shapely.prepared import prep

from .slicer import WALLS
from . import mmu

# Orca / Bambu Studio feature names, so Bambu Studio's G-code viewer colours them the same way
FEATURE = {"outer_wall": "Outer wall", "inner_wall": "Inner wall", "overhang_wall": "Overhang wall",
           "top_surface": "Top surface", "bottom_surface": "Bottom surface", "solid": "Internal solid infill",
           "bridge": "Bridge", "infill": "Sparse infill", "ironing": "Ironing", "skirt": "Skirt",
           "brim": "Brim", "support": "Support", "support_interface": "Support interface", "prime_tower": "Prime tower"}

DEFAULT_START = """; ---- generic start (use --template for a Bambu printer's real start sequence) ----
M140 S{bed_temp}
M104 S150
G28
M190 S{bed_temp}
M109 S{nozzle_temp}
G90
M83
G92 E0
G1 Z5 F3000
G1 X10 Y5 F12000
G1 Z0.3 F600
G1 X110 Y5 E12 F1200
G1 X110 Y5.5
G1 X10 Y5.5 E8 F1500
G92 E0
; ---- end of start ----
"""

DEFAULT_END = """; ---- end ----
G91
G1 Z10 F600
G90
M104 S0
M140 S0
M106 S0
G1 X128 Y250 F6000
M84
"""


def resample(pts, max_seg):
    """Split a polyline so no segment is longer than max_seg (in X/Y)."""
    out = [pts[:1]]
    for a, b in zip(pts[:-1], pts[1:]):
        d = np.linalg.norm(b - a)
        n = max(1, int(np.ceil(d / max_seg)))
        t = np.linspace(0, 1, n + 1)[1:, None]
        out.append(a + (b - a) * t)
    return np.vstack(out)


def adaptive_resample(pts, zp, tf, tol=0.01, max_len=5.0, min_len=0.25):
    """Split each segment only as much as the curved layer needs: the bent-back
    path may not deviate from the true surface by more than `tol` mm."""
    return adaptive_resample_many([pts], zp, tf, tol, max_len, min_len)[0]


def adaptive_resample_many(polys, zp, tf, tol=0.01, max_len=5.0, min_len=0.25):
    """adaptive_resample for many polylines at once (one transform call for all of them)."""
    if not polys:
        return []
    counts = np.array([len(q) for q in polys])
    allp = np.vstack(polys).astype(float)
    ends = np.cumsum(counts)
    valid = np.ones(max(0, len(allp) - 1), bool)
    valid[ends[:-1] - 1] = False                  # no segment from one polyline into the next
    a, b = allp[:-1][valid], allp[1:][valid]
    if len(a) == 0:
        return [q.copy() for q in polys]
    m = 0.5 * (a + b)
    z = tf.inverse(np.concatenate([a[:, 0], b[:, 0], m[:, 0]]),
                   np.concatenate([a[:, 1], b[:, 1], m[:, 1]]),
                   np.full(3 * len(a), zp))
    za, zb, zm = np.split(z, 3)
    sag = np.abs(zm - 0.5 * (za + zb))
    length = np.linalg.norm(b - a, axis=1)
    n = np.ceil(np.sqrt(sag / tol))
    n = np.maximum(n, np.ceil(length / max_len))
    n = np.minimum(n, np.maximum(1, np.floor(length / min_len)))
    n = np.maximum(n, 1).astype(int)
    # every segment becomes n points: a + (b-a) * k/n for k = 1..n
    seg = np.repeat(np.arange(len(a)), n)
    first = np.repeat(np.cumsum(n) - n, n)
    k = (np.arange(n.sum()) - first + 1) / np.repeat(n, n)
    pieces = a[seg] + (b[seg] - a[seg]) * k[:, None]
    seg_end = np.cumsum(np.maximum(counts - 1, 0))
    piece_end = np.concatenate([[0], np.cumsum(n)])
    out, s0 = [], 0
    for q, e in zip(polys, seg_end):
        out.append(np.vstack([q[:1], pieces[piece_end[s0]:piece_end[e]]]))
        s0 = e
    return out


class GcodeWriter:
    def __init__(self, s, tf, tool_settings=None):
        self.s, self.tf = s, tf
        self.ts = list(tool_settings or [s])  # Settings per filament slot (filament values differ)
        self.tool = None                      # loaded filament slot
        self.e_by_tool = [0.0] * len(self.ts)
        self.preview_tools = []               # per layer: tool of each preview entry
        self.preview_speeds = []              # per layer: print speed (mm/s) of each preview entry
        self.preview_attrs = []               # per layer: (width, flow mm3/s, fan %, temp, accel, PA) per entry
        self.preview_thick = []               # per layer: layer height at each point of each entry
        self.layer_times = []                 # seconds each layer takes
        self.temp = None                      # nozzle temperature last set
        self.travel_sp = None                 # travel speed override (initial layer)
        self.jerk = None
        self.last_pts = None                  # the path just printed (for wiping)
        self.layer_n = 0                      # printed layer number (0 = first layer)
        self.time_by_kind = {}                # seconds per line type (and "travel")
        self.mvs_extra = 0.0                  # seconds added by the max volumetric speed
        self.cool_extra = 0.0                 # seconds added by slowing down for layer cooling
        self.cool_factor = 1.0                # this path's cooling slow-down (for the bookkeeping)
        self.pa = 0.0                         # pressure advance last set
        self.max_z = 0.0                      # highest point printed so far
        self.safe_z = None                    # after a filament change: travel at this height first
        self.changes = 0
        self.flush_mm3 = 0.0
        self.flush_g = 0.0
        self.lines = []
        self.pos = None            # current real (x, y, z)
        self.retracted = False
        self.e_total = 0.0
        self.fil_area = np.pi * (s.filament_diameter / 2) ** 2
        self.preview = []          # per layer: list of (kind, (N,3) points)
        self.min_t = np.inf
        self.max_t = 0.0
        self.time = 0.0            # seconds (distance / feed rate, plus a fudge factor at the end)
        self.accel = None
        self.fan = None
        self._warned = set()
        self.reprime = None

    def emit(self, line):
        self.lines.append(line)
        c = line[:4]
        if c in ("M104", "M109", "M900"):
            m = re.search(r"\b([SK])(-?[\d.]+)", line)
            if m and m.group(1) == "S" and c != "M900":
                self.temp = float(m.group(2))
            elif m and m.group(1) == "K" and c == "M900":
                self.pa = float(m.group(2))

    def _move(self, p, f, e=None):
        x, y, z = p
        cmd = f"G1 X{x:.3f} Y{y:.3f}"
        if self.pos is None or abs(z - self.pos[2]) > 5e-4:
            cmd += f" Z{z:.3f}"
        if e is not None:
            cmd += f" E{e:.5f}"
            self.max_z = max(self.max_z, z)
        cmd += f" F{f * 60:.0f}"
        self.emit(cmd)
        p = np.array(p, float)
        if self.pos is not None:
            self.time += np.linalg.norm(p - self.pos) / max(f, 1e-3)
        self.pos = p

    def set_accel(self, a):
        if self.s.default_acceleration <= 0:
            return
        a = a if a > 0 else self.s.default_acceleration
        if a != self.accel:
            self.emit(f"M204 S{a:.0f}")
            self.accel = a

    def set_jerk(self, j):
        s = self.s
        if s.default_jerk <= 0 or s.gcode_flavor == "bambu":
            return
        j = j if j > 0 else s.default_jerk
        if j != self.jerk:
            if s.gcode_flavor == "klipper":
                self.emit(f"SET_VELOCITY_LIMIT SQUARE_CORNER_VELOCITY={j:g}")
            elif s.gcode_flavor == "reprapfirmware":
                self.emit(f"M566 X{j * 60:.0f} Y{j * 60:.0f}")
            else:
                self.emit(f"M205 X{j:g} Y{j:g}")
            self.jerk = j

    def set_fan(self, percent):
        v = int(round(255 * max(0, min(100, percent)) / 100))
        if v != self.fan:
            self.emit(f"M106 S{v}")
            self.fan = v

    def retract(self):
        s = self.s
        if not self.retracted and s.retract_length > 0:
            left = s.retract_length
            if (s.wipe and s.wipe_distance > 0 and self.last_pts is not None and len(self.last_pts) > 1
                    and self.pos is not None and np.allclose(self.last_pts[-1], self.pos, atol=1e-3)):
                # wipe: go back along the line just printed while pulling the filament in
                pts = self.last_pts[::-1]
                f = max(10.0, 0.8 * s.travel_speed)
                todo = s.wipe_distance
                for a, b in zip(pts[:-1], pts[1:]):
                    d = float(np.linalg.norm(b - a))
                    if d < 1e-6:
                        continue
                    if d > todo:
                        b, d = a + (b - a) * (todo / d), todo
                    de = s.retract_length * d / s.wipe_distance
                    self._move(tuple(b), f, -de)
                    left -= de
                    todo -= d
                    if todo <= 1e-6:
                        break
            if left > 1e-5:
                self.emit(f"G1 E{-left:.3f} F{s.retract_speed * 60:.0f}")
                self.time += left / s.retract_speed
            self.retracted = True
            self.retract_amount = s.retract_length      # re-prime exactly this (it can change by height)

    def unretract(self):
        if self.retracted:
            sp = self.s.deretraction_speed or self.s.retract_speed
            length = self.reprime if self.reprime else \
                getattr(self, "retract_amount", self.s.retract_length) + self.s.retract_restart_extra
            self.emit(f"G1 E{length:.3f} F{sp * 60:.0f}")
            self.retracted = False
            self.reprime = None
            self.time += length / sp

    def lift_to(self, xy, zp):
        """Real-space points along the curved surface z' = zp above the given X/Y points."""
        z = self.tf.inverse(xy[:, 0], xy[:, 1], np.full(len(xy), zp))
        return np.column_stack([xy, z])

    def _travel_line(self, a, b, zp, tol=0.05, step=1.0):
        """Points of a travel move along the curved surface z' = zp, from a to b (X/Y).
        Sampled finely in one transform call, then thinned to what the curve needs."""
        d = float(np.hypot(*(b - a)))
        n = max(1, int(np.ceil(d / step)))
        u = np.linspace(0.0, 1.0, n + 1)
        xy = a + (b - a) * u[:, None]
        z = self.tf.inverse(xy[:, 0], xy[:, 1], np.full(len(xy), zp))
        if n > 2:
            prof = LineString(np.column_stack([u * d, z])).simplify(tol, preserve_topology=False)
            keep = np.asarray(prof.coords)[:, 0] / max(d, 1e-9)
            idx = np.unique(np.clip(np.rint(keep * n).astype(int), 0, n))
            xy, z = xy[idx], z[idx]
        return np.column_stack([xy, z])

    def travel(self, target_xy, zp, allow_retract=True, tgt=None):
        s = self.s
        if tgt is None:
            tgt = self.lift_to(np.asarray(target_xy, float)[None], zp)[0]
        if self.pos is None:
            z = max(tgt[2], 0) + 2
            if self.safe_z is not None:          # back from a filament change: stay above the print
                z = max(z, self.safe_z)
                self.emit(f"G1 Z{z:.3f} F1200")
            self._move((tgt[0], tgt[1], z), self.travel_sp or s.travel_speed)
            self._move(tgt, 10)
            self.safe_z = None
            return
        d = np.hypot(*(tgt[:2] - self.pos[:2]))
        if d < 1e-3:
            if abs(tgt[2] - self.pos[2]) > 1e-3:
                self._move(tgt, 10)
            return
        long = d >= s.retract_min_travel and allow_retract
        self.set_accel(s.travel_acceleration)
        self.set_jerk(s.travel_jerk)
        if long:
            self.retract()
        lift = s.travel_lift if long else 0.0
        z_now = float(self.pos[2])
        if lift and ((s.retract_lift_above > 0 and z_now < s.retract_lift_above) or
                     (s.retract_lift_below > 0 and z_now > s.retract_lift_below)):
            lift = 0.0
        pts = self._travel_line(self.pos[:2].astype(float), np.asarray(tgt[:2], float), zp + lift)
        # never dip below the current position while leaving (protects what we just printed)
        pts[0, 2] = max(pts[0, 2], self.pos[2] + lift)
        if lift:
            self._move((self.pos[0], self.pos[1], pts[0, 2]), 15)
        for p in pts[1:]:
            self._move(p, self.travel_sp or s.travel_speed)
        self._move(tgt, 15)

    def prepare_layer(self, paths, zp, h):
        """Bend all paths of a layer back to real space in one go (much faster than per path).
        Returns per path (real points, perpendicular layer thickness per segment)."""
        if not paths:
            return []
        xys = adaptive_resample_many([p.pts for p in paths], zp, self.tf, tol=self.s.curve_tolerance)
        allxy = np.vstack(xys)
        z = self.tf.inverse(allxy[:, 0], allxy[:, 1], np.full(len(allxy), zp))
        allpts = np.column_stack([allxy, z])
        cut = np.cumsum([len(q) for q in xys])[:-1]
        pts_list = np.split(allpts, cut)
        mids = np.vstack([0.5 * (q[1:] + q[:-1]) for q in pts_list])
        gx, gy, gz = self.tf.grad(mids[:, 0], mids[:, 1], mids[:, 2])
        thick = h / np.sqrt(gx ** 2 + gy ** 2 + gz ** 2)          # perpendicular layer thickness
        th_list = np.split(thick, np.cumsum([len(q) - 1 for q in pts_list])[:-1])
        return list(zip(pts_list, th_list))

    def extrude_path(self, path, zp, h, speed, allow_retract=True, pre=None):
        s = self.s
        if pre is None:
            pre = self.prepare_layer([path], zp, h)[0]
        pts, thick = pre
        if path.closed and path.kind in WALLS and s.seam_gap > 0:
            pts, thick = _trim_end(pts, thick, s.seam_gap / 100.0 * s.nozzle_diameter)
        t0 = self.time
        self.travel(pts[0, :2], zp, allow_retract, tgt=pts[0])
        self.unretract()
        t1 = self.time
        self.time_by_kind["travel"] = self.time_by_kind.get("travel", 0.0) + (t1 - t0)
        thick = np.clip(thick, 0.04, s.nozzle_diameter * 0.8)
        self.min_t = min(self.min_t, thick.min())
        self.max_t = max(self.max_t, thick.max())
        w = path.width
        if path.kind == "bridge" and s.thick_bridges:
            area = np.full_like(thick, math.pi * (w / 2) ** 2)     # round strand in the air
        else:
            area = thick * (w - thick * (1 - math.pi / 4))         # Orca's rounded-rectangle model
        seg = np.linalg.norm(pts[1:] - pts[:-1], axis=1)
        length = float(seg.sum())
        if s.max_volumetric_speed > 0:
            cap = s.max_volumetric_speed / float(area.mean())
            if cap < speed:
                self.mvs_extra += length / max(cap, 1e-3) - length / max(speed, 1e-3)
                speed = cap
        if self.cool_factor < 1:
            self.cool_extra += length / max(speed, 1e-3) * (1 - self.cool_factor)
        k = s.print_flow_ratio * (s.top_surface_flow_ratio if path.kind == "top_surface" else
                                  s.bottom_surface_flow_ratio if path.kind == "bottom_surface" else 1.0)
        if self.layer_n == 0:
            k *= s.first_layer_flow_ratio
        e = s.flow * k * path.flow * area * seg / self.fil_area
        self._extrude_moves(pts, speed, e)
        self.time_by_kind[path.kind] = self.time_by_kind.get(path.kind, 0.0) + (self.time - t1)
        self.e_by_tool[self.tool or 0] += float(e.sum())
        self.preview[-1].append((path.kind, pts))
        self.preview_tools[-1].append(self.tool or 0)
        self.preview_speeds[-1].append(float(speed))
        self.preview_attrs[-1].append((float(w), float(speed * area.mean()), 100.0 * (self.fan or 0) / 255,
                                       float(self.temp or s.nozzle_temp), float(self.accel or 0), float(self.pa)))
        self.preview_thick[-1].append(np.append(thick, thick[-1]).astype(np.float32))
        self.last_pts = pts

    def _extrude_moves(self, pts, speed, e):
        """Many G1 extrusion moves at one speed (the inner loop of the writer, kept lean)."""
        f = f" F{speed * 60:.0f}"
        z0 = self.pos[2] if self.pos is not None else None
        lines = self.lines
        for (x, y, z), de in zip(pts[1:].tolist(), e.tolist()):
            if z0 is None or abs(z - z0) > 5e-4:
                lines.append(f"G1 X{x:.3f} Y{y:.3f} Z{z:.3f} E{de:.5f}{f}")
                z0 = z
            else:
                lines.append(f"G1 X{x:.3f} Y{y:.3f} E{de:.5f}{f}")
        seg = np.linalg.norm(pts[1:] - pts[:-1], axis=1)
        self.time += float(seg.sum()) / max(speed, 1e-3)
        self.e_total += float(e.sum())
        self.max_z = max(self.max_z, float(pts[1:, 2].max()))
        self.pos = np.array(pts[-1], float)

    def change_tool(self, new, template, filaments, layer_num, layer_z, next_xyz, log):
        """Swap filament with the (Bambu) filament-change macro."""
        old = self.tool
        if getattr(self, "by_object", False):
            # by object: finished objects stand higher than this object's layer, and change macros move the
            # head at about their layer height: give them the highest point printed so far instead
            layer_z = max(layer_z, self.max_z + 1.0)
            next_xyz = (next_xyz[0], next_xyz[1], max(float(next_xyz[2]), self.max_z + 1.0))
        self.retract()
        vol = 0.0
        if old is not None:
            vol = mmu.flush_volume(filaments[old].get("color"), filaments[new].get("color"), self.s.flush_multiplier)
        self.last_flush = vol
        env = mmu.change_env(self.ts, filaments, old, new, layer_num, layer_z, self.max_z, next_xyz, vol, self.changes + 1)
        text, unknown = mmu.render_change(template, env)
        unknown -= self._warned
        if unknown:
            self._warned |= unknown
            log("Filament change G-code: unknown placeholders set to 0: " + ", ".join(sorted(unknown)))
        self.emit(f"; filament change: {'-' if old is None else old + 1} -> {new + 1}")
        last_e = None
        for line in text.strip("\n").splitlines():
            self.emit(line)
            m = re.match(r"\s*G[01]\b[^;]*\bE(-?[\d.]+)", line)
            if m:
                last_e = float(m.group(1))
        self.tool = new
        self.s = self.ts[new]
        self.fil_area = np.pi * (self.s.filament_diameter / 2) ** 2
        # the macro moved the head away (and may have retracted): forget position, fan and acceleration
        self.pos, self.fan, self.accel = None, None, None
        self.safe_z = self.max_z + 3.0
        self.retracted = bool(last_e is not None and last_e < 0)
        self.reprime = -last_e if self.retracted else None   # undo the macro's own retraction, not ours
        if old is not None:
            self.changes += 1
            self.flush_mm3 += vol
            self.flush_g += vol * self.s.filament_density / 1000.0
            self.time += 40 + vol / 8.0       # cut, unload, load, flush


def fill_placeholders(text, s):
    """Replace {nozzle_temp}-style and common Orca/Bambu [name] placeholders."""
    vals = {
        "nozzle_temp": s.nozzle_temp, "bed_temp": s.bed_temp,
        "nozzle_temp_first": s.nozzle_temp_first, "bed_temp_first": s.bed_temp_first,
        "nozzle_temperature_initial_layer": s.nozzle_temp_first,
        "bed_temperature_initial_layer_single": s.bed_temp_first,
        "nozzle_temperature": s.nozzle_temp, "bed_temperature": s.bed_temp,
        "first_layer_temperature": s.nozzle_temp_first, "first_layer_bed_temperature": s.bed_temp_first,
    }
    for k, v in vals.items():
        text = text.replace("{" + k + "}", str(v)).replace("[" + k + "]", str(v))
        text = text.replace("{" + k + "[0]}", str(v)).replace("[" + k + "[0]]", str(v))
    return text


def _feature_speed(p, s, n):
    """Speed for a path on printed layer n (0-based), before cooling slow-down."""
    normal = {"outer_wall": s.outer_wall_speed, "inner_wall": s.inner_wall_speed,
              "overhang_wall": s.outer_wall_speed, "top_surface": s.top_surface_speed,
              "bottom_surface": s.solid_speed, "solid": s.solid_speed, "bridge": s.bridge_speed,
              "infill": s.infill_speed, "ironing": s.ironing_speed, "skirt": s.first_layer_speed,
              "brim": s.first_layer_speed, "support": s.support_speed,
              "support_interface": s.support_interface_speed}[p.kind]
    if p.speed:
        normal = p.speed
    if p.kind == "skirt" and s.skirt_speed > 0:
        return s.skirt_speed
    first = s.first_layer_speed if (p.kind in WALLS or p.kind in ("skirt", "brim")) else s.first_layer_infill_speed
    if n == 0:
        return min(first, normal)
    if s.slow_down_layers > 1 and n < s.slow_down_layers:
        return first + (normal - first) * n / s.slow_down_layers
    return normal


def _accel(kind, s, n):
    if n == 0:
        return s.initial_layer_acceleration
    return {"outer_wall": s.outer_wall_acceleration, "overhang_wall": s.outer_wall_acceleration,
            "inner_wall": s.inner_wall_acceleration, "top_surface": s.top_surface_acceleration,
            "ironing": s.top_surface_acceleration, "infill": s.sparse_infill_acceleration,
            "bridge": s.bridge_acceleration, "solid": s.internal_solid_infill_acceleration}.get(kind, 0)


def _jerk(kind, s, n):
    if n == 0:
        return s.initial_layer_jerk
    return {"outer_wall": s.outer_wall_jerk, "overhang_wall": s.outer_wall_jerk, "inner_wall": s.inner_wall_jerk,
            "top_surface": s.top_surface_jerk, "ironing": s.top_surface_jerk, "infill": s.infill_jerk}.get(kind, 0)


def _trim_end(pts, thick, gap):
    """A closed loop stopped `gap` mm short of where it started (Orca's seam gap)."""
    d = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    total = float(d.sum())
    if gap <= 0 or total <= 4 * gap or len(pts) < 3:
        return pts, thick
    cum = np.concatenate([[0.0], np.cumsum(d)])
    cut = total - gap
    k = int(np.searchsorted(cum, cut))
    k = max(1, min(k, len(pts) - 1))
    u = (cut - cum[k - 1]) / max(d[k - 1], 1e-12)
    end = pts[k - 1] + (pts[k] - pts[k - 1]) * u
    return np.vstack([pts[:k], end]), thick[:k]


def _layer_time(L, s, n):
    """Rough print time of a layer, for cooling decisions."""
    t, pos = 0.0, None
    for p in L.paths:
        length = float(np.linalg.norm(np.diff(p.pts, axis=0), axis=1).sum())
        sp = _feature_speed(p, s, n)
        if s.max_volumetric_speed > 0:
            sp = min(sp, s.max_volumetric_speed / (L.thickness * p.width))
        t += length / max(sp, 1e-3)
        if pos is not None:
            t += float(np.linalg.norm(p.pts[0] - pos)) / s.travel_speed
        pos = p.pts[-1]
    return t


def _layer_fan(t, s):
    """Orca: max fan for layers under slow_down_layer_time, min fan above fan_cooling_layer_time."""
    lo, hi = s.slow_down_layer_time, max(s.fan_cooling_layer_time, s.slow_down_layer_time + 1e-6)
    if t <= lo:
        return s.fan_max_speed
    if t >= hi:
        return s.fan_min_speed if s.reduce_fan_stop_start_freq else 0
    return s.fan_max_speed - (s.fan_max_speed - s.fan_min_speed) * (t - lo) / (hi - lo)


def initial_tool(start_text):
    """Filament slot the (template) start G-code loads, or None if it loads none."""
    found = None
    for line in (start_text or "").splitlines():
        m = re.match(r"\s*T(\d+)\s*(;.*)?$", line)
        if m and int(m.group(1)) < 16:
            found = int(m.group(1))
    return found


def write_gcode(layers, s, tf, start=None, end=None, log=print, progress=None,
                tool_settings=None, filaments=None, change_gcode=None, calib=None, objects=None):
    """Returns (gcode_text, preview, stats). tool_settings / filaments: one per filament slot
    (Settings, and {"type", "color"}); change_gcode: the filament-change macro.
    calib: a nonplanar.calibration.Calib whose hooks change settings by height or block.
    objects: print by object: [(layers, transform)] in print order, each finished before the next starts."""
    if objects:
        layers = [L for lay, _ in objects for L in lay]
        tf = objects[0][1]
    w = GcodeWriter(s, tf, tool_settings)
    w.by_object = bool(objects) and len(objects) > 1
    filaments = filaments or [{"type": "PLA", "color": "#808080"} for _ in w.ts]
    generic_change = not change_gcode
    change_gcode = change_gcode or mmu.DEFAULT_CHANGE_GCODE
    start = fill_placeholders(DEFAULT_START if not start else start, s)
    end = fill_placeholders(DEFAULT_END if not end else end, s)

    printed = [L for L in layers if L.paths]
    # which object each printed layer belongs to, and its own transform (one object when printing by layer)
    seq = [(otf, k) for k, (lay, otf) in enumerate(objects) for L in lay if L.paths] if objects else \
        [(tf, 0) for _ in printed]
    if not printed:
        raise ValueError("Nothing to print - the model produced no toolpaths")
    used = sorted({p.tool for L in printed for p in L.paths})
    if used and used[-1] >= len(w.ts):
        raise ValueError(f"Filament {used[-1] + 1} is used but only {len(w.ts)} filament(s) are set up")
    first_tool = printed[0].paths[0].tool
    w.s = w.ts[first_tool]
    w.fil_area = np.pi * (w.s.filament_diameter / 2) ** 2
    s = w.s
    loaded = initial_tool(start)
    w.tool = first_tool if loaded is None else loaded
    # the template's header block must stay at the very top of the file
    for line in start.strip("\n").splitlines():
        w.emit(line)
    w.emit("; generated by ArcLayer")
    w.emit(f"; mode: {s.mode}, angle {tf.angle:+.1f} deg, centre ({tf.cx:.2f}, {tf.cy:.2f}), ramp {tf.H:.2f} mm")
    w.emit(f"; layers: {len(printed)}")
    w.emit("G90")
    w.emit("M83")
    w.emit("G92 E0")
    w.set_fan(0)
    w.emit(f"M104 S{s.nozzle_temp_first}")
    w.emit(f"M140 S{s.bed_temp_first}")
    if s.chamber_temperature > 0:
        w.emit(f"M141 S{s.chamber_temperature}")
    if s.emit_machine_limits:
        w.emit(f"M201 X{s.machine_max_acceleration_x:.0f} Y{s.machine_max_acceleration_y:.0f} "
               f"Z{s.machine_max_acceleration_z:.0f} E{s.machine_max_acceleration_e:.0f}")
        w.emit(f"M203 X{s.machine_max_speed_x:g} Y{s.machine_max_speed_y:g} Z{s.machine_max_speed_z:g} E{s.machine_max_speed_e:g}")
        w.emit(f"M204 P{s.machine_max_acceleration_extruding:.0f} R{s.machine_max_acceleration_retracting:.0f} "
               f"T{s.machine_max_acceleration_travel:.0f}")
        w.emit(f"M205 X{s.machine_max_jerk_x:g} Y{s.machine_max_jerk_y:g}")
    if s.filament_start_gcode.strip():
        w.emit("; filament start G-code")
        for line in fill_placeholders(s.filament_start_gcode, s).strip("\n").splitlines():
            w.emit(line)
    for k, ts in enumerate(w.ts):
        if k in used and not (ts.nozzle_temperature_range_low <= ts.nozzle_temp <= ts.nozzle_temperature_range_high):
            log(f"WARNING: filament {k + 1} prints at {ts.nozzle_temp} °C, outside its recommended "
                f"{ts.nozzle_temperature_range_low}-{ts.nozzle_temperature_range_high} °C")
    max_lh = s.max_layer_height if s.max_layer_height > 0 else 0.75 * s.nozzle_diameter
    if s.layer_height > max_lh + 1e-6 or s.layer_height < s.min_layer_height - 1e-6:
        log(f"WARNING: layer height {s.layer_height:g} mm is outside this printer's "
            f"{s.min_layer_height:g}-{max_lh:g} mm")
    if len(used) > 1 or w.tool != first_tool:
        log(f"Filaments used: {', '.join(str(t + 1) for t in used)}"
            + (" - WARNING: generic filament change without flushing (set a Bambu template)" if generic_change else
               " - filament changes use the template's change G-code"))
    if w.tool != first_tool:
        p0 = printed[0].paths[0]
        w.change_tool(first_tool, change_gcode, filaments, 0, printed[0].zp,
                      tuple(w.lift_to(p0.pts[:1], printed[0].zp)[0]), log)
    w.tool = first_tool
    pa_tool = None
    # prime tower: auto = several filaments on a printer without a purge chute (Bambu printers flush into theirs)
    tower = None
    want = s.prime_tower == "on" or (s.prime_tower == "auto" and len(used) > 1 and s.gcode_flavor != "bambu")
    if objects and len(objects) > 1 and want:
        want = False
        log("Prime tower: not used when printing by object (each object changes filament at most at its start)")
    if want and len(used) > 1:
        from .tower import PrimeTower
        full_flush = generic_change               # no change macro at all: the whole flush goes into the tower
        tower = PrimeTower(s, printed, first_tool, filaments, full_flush, log)
        if not tower.enabled:
            tower = None
    elif len(used) > 1 and s.gcode_flavor != "bambu":
        log("Prime tower is off: colours can mix a little right after each change")

    slowed = 0
    m73 = []
    def custom(text, n, z):
        for k, v in (("layer_num", n), ("layer_z", f"{z:.3f}"), ("max_layer_z", f"{w.max_z:.3f}")):
            text = text.replace("{" + k + "}", str(v)).replace("[" + k + "]", str(v))
        return [ln for ln in text.strip("\n").splitlines()]

    aux_on = False
    cur_obj, nl = 0, -1
    for n, L in enumerate(printed):
        otf, k_obj = seq[n]
        if k_obj != cur_obj:
            # next object: up above everything printed so far, then over to it (see GcodeWriter.travel)
            w.retract()
            w.emit(f"; object {cur_obj + 1} done: next object")
            w.safe_z = w.max_z + max(2.0, s.travel_lift)
            w.emit(f"G1 Z{w.safe_z:.3f} F1200")          # up now: a filament change may come first
            w.pos = None
            cur_obj, nl = k_obj, -1
        w.tf = otf
        nl += 1                                       # layer number inside this object (its first layer = 0)
        w.layer_n = nl
        w.travel_sp = s.initial_layer_travel_speed if (nl == 0 and s.initial_layer_travel_speed > 0) else None
        if s.before_layer_change_gcode.strip():
            for line in custom(s.before_layer_change_gcode, n, L.zp):
                w.emit(line)
        w.emit("; CHANGE_LAYER")
        w.emit(f"; Z_HEIGHT: {L.zp:.3f}")
        w.emit(f"; LAYER_HEIGHT: {L.thickness:.3f}")
        w.emit(f"M73 L{n + 1}")
        m73.append((len(w.lines), w.time))          # filled in at the end: percent and minutes left
        w.emit("M73 P0 R0")
        w.emit(f"M991 S0 P{n} ;notify layer change")
        if s.layer_change_gcode.strip():
            for line in custom(s.layer_change_gcode, n, L.zp):
                w.emit(line)
        if not aux_on and s.additional_cooling_fan_speed > 0 and n >= s.close_fan_first_layers:
            w.emit(f"M106 P2 S{int(round(255 * min(100, s.additional_cooling_fan_speed) / 100))}")
            aux_on = True
        if calib is not None:
            for line in calib.layer_start(n, L.zp, L.thickness, w):
                w.emit(line)
        if n == 1:
            if w.s.nozzle_temp != w.s.nozzle_temp_first:
                w.emit(f"M104 S{w.s.nozzle_temp}")
            if s.bed_temp != s.bed_temp_first:
                w.emit(f"M140 S{s.bed_temp}")
        t_layer = _layer_time(L, s, nl)
        factor = 1.0
        if s.slow_down_for_layer_cooling and nl > 0 and 0 < t_layer < s.slow_down_layer_time:
            factor = t_layer / s.slow_down_layer_time
            slowed += 1
        fans = {}

        def base_fan_for(tool):
            if tool not in fans:
                ts = w.ts[tool]
                f = 0 if nl < ts.close_fan_first_layers else _layer_fan(t_layer, ts)
                full, c0 = ts.full_fan_speed_layer, ts.close_fan_first_layers
                if f and full > c0 + 1 and nl + 1 < full:     # ramp up to full speed at layer `full`
                    f *= (nl + 1 - c0) / (full - c0)
                fans[tool] = f
            return fans[tool]

        w.set_fan(base_fan_for(w.tool))
        w.preview.append([])
        w.preview_tools.append([])
        w.preview_speeds.append([])
        w.preview_attrs.append([])
        w.preview_thick.append([])
        t_start = w.time
        infill_area = prep(L.infill_area) if (s.reduce_infill_retraction and L.infill_area is not None) else None
        prev = None
        prepared = w.prepare_layer(L.paths, L.zp, L.thickness)
        changes_here = 0
        if tower is not None:
            tower.begin_layer(n, min(float(q[:, 2].min()) for q, _ in prepared) if prepared else L.zp,
                              L.thickness, s.nozzle_diameter)
            tower.layer_start(w, n, L.zp, L.thickness)
        for p, pre in zip(L.paths, prepared):
            if p.tool != w.tool:
                w.change_tool(p.tool, change_gcode, filaments, n + 1, L.zp, tuple(w.lift_to(p.pts[:1], L.zp)[0]), log)
                if tower is not None:
                    if w.tool != pa_tool and w.s.enable_pressure_advance:
                        w.emit(f"M900 K{w.s.pressure_advance:.3f} L1000 M10")
                        pa_tool = w.tool
                    w.set_fan(base_fan_for(w.tool))
                    tower.after_change(w, n, L.zp, L.thickness, changes_here)
                changes_here += 1
                prev = None
            if w.tool != pa_tool:
                if w.s.enable_pressure_advance:
                    w.emit(f"M900 K{w.s.pressure_advance:.3f} L1000 M10")
                pa_tool = w.tool
            base_fan = base_fan_for(w.tool)
            if prev is None or p.kind != prev.kind:
                w.emit(f"; FEATURE: {FEATURE.get(p.kind, p.kind)}")
            sp = _feature_speed(p, s, nl)
            w.cool_factor = 1.0
            if factor < 1 and not (s.dont_slow_down_outer_wall and p.kind == "outer_wall"):
                sp0 = sp
                sp = max(sp * factor, min(s.slow_down_min_speed, sp))
                w.cool_factor = sp / sp0 if sp0 > 0 else 1.0
            if calib is not None:
                sp = calib.speed(p, L.zp, L.thickness, sp, n)
                p.flow *= calib.flow(p)
            if p.gcode:
                for line in p.gcode.splitlines():
                    w.emit(line)
            w.set_accel(_accel(p.kind, s, nl))
            w.set_jerk(_jerk(p.kind, s, nl))
            w.set_fan(base_fan if (p.fan is None or nl < w.s.close_fan_first_layers) else max(base_fan, p.fan))
            allow = True
            if infill_area is not None and prev is not None and p.kind == "infill" and prev.kind == "infill":
                allow = not infill_area.contains(LineString([prev.pts[-1], p.pts[0]]))
            w.extrude_path(p, L.zp, L.thickness, sp, allow, pre=pre)
            prev = p
        if s.retract_when_changing_layer:
            w.retract()
        w.layer_times.append(w.time - t_start)
        if progress and n % 10 == 0:
            progress(0.62 + 0.3 * n / len(printed), f"G-code: layer {n}/{len(printed)}")
    w.emit("M73 P100")
    if aux_on:
        w.emit("M106 P2 S0")
    if w.s.filament_end_gcode.strip():
        w.emit("; filament end G-code")
        for line in fill_placeholders(w.s.filament_end_gcode, w.s).strip("\n").splitlines():
            w.emit(line)
    for line in end.strip("\n").splitlines():
        w.emit(line)

    zs = np.concatenate([np.concatenate([pt[:, 2] for _, pt in lay]) for lay in w.preview if lay])
    per_tool = []
    for k, ts in enumerate(w.ts):
        e = w.e_by_tool[k]
        g = e * np.pi * (ts.filament_diameter / 2) ** 2 * ts.filament_density / 1000.0
        per_tool.append({"m": e / 1000, "g": g, "cost": g / 1000.0 * ts.filament_cost})
    grams = sum(t["g"] for t in per_tool)
    # progress and time left for the printer's screen (and the Device tab): M73 P<percent> R<minutes>
    total = max(w.time, 1e-6)
    for i, t in m73:
        w.lines[i] = f"M73 P{min(99, int(100 * t / total))} R{int(math.ceil((total - t) * 1.15 / 60))}"
    if not any("HEADER_BLOCK_START" in ln for ln in w.lines[:5]):
        est = w.time * 1.15 + 120
        hm = lambda sec: f"{int(sec // 3600)}h {int(sec % 3600 // 60)}m {int(sec % 60)}s" if sec >= 3600 \
            else f"{int(sec // 60)}m {int(sec % 60)}s"
        vol = sum(w.e_by_tool[k] * np.pi * (ts.filament_diameter / 2) ** 2 for k, ts in enumerate(w.ts)) / 1000
        head = ["; HEADER_BLOCK_START", "; generated by ArcLayer",
                f"; model printing time: {hm(w.time * 1.15)}; total estimated time: {hm(est)}",
                f"; total layer number: {len(printed)}",
                f"; total filament length [mm] : {w.e_total:.2f}",
                f"; total filament volume [cm^3] : {vol:.2f}",
                f"; total filament weight [g] : {grams:.2f}",
                f"; max_z_height: {float(zs.max()):.2f}",
                "; HEADER_BLOCK_END", ""]
        w.lines[0:0] = head
    else:                                          # a template's header describes the template's model: fix it
        for k in range(min(60, len(w.lines))):
            if w.lines[k].startswith("; total layer number:"):
                w.lines[k] = f"; total layer number: {len(printed)}"
            if "HEADER_BLOCK_END" in w.lines[k]:
                break
    stats = {"lines": len(w.lines), "layers": len(printed), "filament_m": w.e_total / 1000,
             "cost": sum(t["cost"] for t in per_tool),
             "time_by_kind": {k: v * 1.15 for k, v in w.time_by_kind.items()},
             "time_mvs_extra": w.mvs_extra * 1.15, "time_cool_extra": w.cool_extra * 1.15, "slowed_layers": slowed,
             "filament_g": grams, "time_s": w.time * 1.15 + 120,  # +15% accel, +2 min start
             "tool_changes": w.changes, "flush_g": w.flush_g, "per_tool": per_tool, "used_tools": used,
             "preview_tools": w.preview_tools, "preview_speeds": w.preview_speeds,
             "preview_attrs": w.preview_attrs, "preview_thick": w.preview_thick, "layer_times": w.layer_times,
             "z_min": float(zs.min()), "z_max": float(zs.max()),
             "thick_min": float(w.min_t), "thick_max": float(w.max_t),
             "ramp": tf.H, "angle": tf.angle}
    log(f"G-code: {len(w.lines)} lines, {len(printed)} layers, filament {stats['filament_m']:.2f} m "
        f"({grams:.1f} g), est. {stats['time_s'] / 60:.0f} min, real Z {zs.min():.2f}-{zs.max():.2f} mm, "
        f"layer thickness {w.min_t:.3f}-{w.max_t:.3f} mm"
        + (f", {slowed} layers slowed for cooling" if slowed else "")
        + (f", {w.changes} filament changes (flush about {w.flush_g:.1f} g)" if w.changes else ""))
    if zs.max() > s.max_z:
        raise ValueError(f"Print reaches Z={zs.max():.1f} mm, above the printer's {s.max_z} mm")
    return "\n".join(w.lines) + "\n", w.preview, stats
