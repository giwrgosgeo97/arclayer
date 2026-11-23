"""Fill patterns, all made in the flat (bent) layer space.

Every pattern lives on a fixed grid anchored at the bed origin, so sparse infill
lines land on top of the ones below them, layer after layer.
"""
import math

import numpy as np
from shapely.geometry import MultiLineString, LineString, Polygon
from shapely import affinity

from .geo import inter


class P:
    """Light toolpath record; slicer.Path is built from it."""
    __slots__ = ("kind", "pts", "closed")

    def __init__(self, kind, pts, closed):
        self.kind, self.pts, self.closed = kind, pts, closed


def _lines(geom, min_len=0.3):
    if geom.is_empty:
        return []
    gs = geom.geoms if hasattr(geom, "geoms") else [geom]
    out = []
    for g in gs:
        if isinstance(g, LineString):
            if g.length > min_len:
                out.append(g)
        elif hasattr(g, "geoms"):
            out += _lines(g, min_len)
    return out


def _polys(geom):
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon) and not g.is_empty]


def _rot(angle_deg):
    """Row-vector rotation matrix: pts @ _rot(a) turns points by +a degrees around the origin."""
    a = math.radians(angle_deg)
    return np.array([[math.cos(a), math.sin(a)], [-math.sin(a), math.cos(a)]])


def _clip(area, lines):
    """Lines inside an area. An area left slightly invalid by earlier boolean operations (it happens
    with support on curved layers) is repaired first instead of stopping the slice."""
    try:
        return inter(area, lines)
    except Exception:
        import shapely
        fixed = shapely.make_valid(shapely.set_precision(area, 1e-4))
        fixed = shapely.union_all([g for g in getattr(fixed, "geoms", [fixed]) if g.geom_type in ("Polygon", "MultiPolygon")])
        return fixed.intersection(lines)


# ---------------------------------------------------------------- straight lines

def hatch(area, spacing, angle_deg, kind, offset=0.0, unidirectional=False):
    """Parallel lines `spacing` apart at `angle_deg`, on a global grid, clipped to `area`.
    Returned in row order; rows alternate direction unless `unidirectional`."""
    if area.is_empty or spacing <= 0:
        return []
    rot = affinity.rotate(area, -angle_deg, origin=(0, 0))
    minx, miny, maxx, maxy = rot.bounds
    off = offset % spacing
    first = math.ceil((miny - off) / spacing) * spacing + off
    ys = np.arange(first, maxy, spacing)
    if len(ys) == 0:
        return []
    cut = _clip(rot, MultiLineString([[(minx - 1, y), (maxx + 1, y)] for y in ys]))
    rows = {}
    for g in _lines(cut):
        (x0, y0), (x1, y1) = g.coords[0], g.coords[-1]
        row = int(round((y0 - off) / spacing))
        rows.setdefault(row, []).append((min(x0, x1), g))
    a = math.radians(angle_deg)
    R = np.array([[math.cos(a), math.sin(a)], [-math.sin(a), math.cos(a)]])   # row vectors: p @ R rotates by +angle
    out = []
    for row in sorted(rows):
        flip = (row % 2 == 1) and not unidirectional
        for _, g in sorted(rows[row], key=lambda t: t[0], reverse=flip):
            fwd = np.asarray(g.coords)
            pts = fwd @ R
            if (fwd[-1, 0] < fwd[0, 0]) != flip:
                pts = pts[::-1]
            out.append(P(kind, pts, False))
    return out


def concentric(area, spacing, kind, first=None):
    """Inward offset loops, `spacing` apart, starting `first` mm in from the edge."""
    out = []
    d = spacing / 2 if first is None else first
    for _ in range(2000):
        g = area.buffer(-d, join_style=2, mitre_limit=2.0)
        if g.is_empty:
            break
        for poly in _polys(g):
            for ring in [poly.exterior, *poly.interiors]:
                pts = np.asarray(ring.coords)
                if len(pts) >= 4 and ring.length > 1.0:
                    out.append(P(kind, pts, True))
        d += spacing
    return out


# ---------------------------------------------------------------- curves

def _clip_curves(area, curves, kind):
    if not curves:
        return []
    cut = _clip(area, MultiLineString(curves))
    return [P(kind, np.asarray(g.coords), False) for g in _lines(cut)]


def gyroid(area, period, z, angle_deg, kind):
    """Slice of the gyroid surface sin x cos y + sin y cos z + sin z cos x = 0 at height z.
    Solved analytically: y(x) when |cos z| >= |sin z|, otherwise x(y); both are gap-free."""
    rot = affinity.rotate(area, -angle_deg, origin=(0, 0))
    minx, miny, maxx, maxy = rot.bounds
    k = 2 * math.pi / period
    zz = z * k
    swap = abs(math.cos(zz)) < abs(math.sin(zz))
    lo_t, hi_t = (miny, maxy) if swap else (minx, maxx)      # the free variable
    lo_v, hi_v = (minx, maxx) if swap else (miny, maxy)      # the solved one
    t = np.arange(lo_t - period / 8, hi_t + period / 8, period / 40)
    T = t * k
    if not swap:     # y(x):  cos z * sin y + sin x * cos y = -sin z cos x
        A, B, C = np.full_like(T, math.cos(zz)), np.sin(T), -math.sin(zz) * np.cos(T)
    else:            # x(y):  cos y * sin x + sin z * cos x = -sin y cos z
        A, B, C = np.cos(T), np.full_like(T, math.sin(zz)), -np.sin(T) * math.cos(zz)
    R = np.sqrt(A ** 2 + B ** 2)
    psi = np.arctan2(B, A)
    s = np.arcsin(np.clip(C / R, -1, 1))
    curves = []
    for base in (s - psi, math.pi - s - psi):
        base = np.unwrap(base)
        m0 = math.floor((lo_v * k - base.max()) / (2 * math.pi)) - 1
        m1 = math.ceil((hi_v * k - base.min()) / (2 * math.pi)) + 1
        for m in range(m0, m1 + 1):
            v = (base + 2 * math.pi * m) / k
            pts = np.column_stack([v, t]) if swap else np.column_stack([t, v])
            curves.append(pts)
    curves = [np.asarray(c, float) @ _rot(angle_deg) for c in curves]
    return _clip_curves(area, curves, kind)


def honeycomb(area, w, density, angle_deg, kind):
    """2D hexagons: rows of flat-topped waves, mirrored in pairs one line width apart."""
    h = max(w, 4 * w / (3 * density) - w)     # wave height, from the line length per area
    a = 2 * h / math.sqrt(3)                  # hexagon side
    rot = affinity.rotate(area, -angle_deg, origin=(0, 0))
    minx, miny, maxx, maxy = rot.bounds
    x0 = math.floor(minx / (3 * a)) * 3 * a - 3 * a
    n = int((maxx - x0) / (3 * a)) + 3
    # one period: flat low, rise, flat high, fall
    px = np.array([0, a, 1.5 * a, 2.5 * a])
    py = np.array([0, 0, h, h])
    xs = (x0 + (np.arange(n)[:, None] * 3 * a + px[None])).ravel()
    prof = np.tile(py, n)
    pitch = 2 * h + 2 * w
    curves = []
    p0 = math.floor(miny / pitch) - 1
    p1 = math.ceil(maxy / pitch) + 1
    for p in range(p0, p1 + 1):
        Y = p * pitch
        curves.append(np.column_stack([xs, Y + prof]))
        curves.append(np.column_stack([xs, Y + h + w + (h - prof)]))
    curves = [np.asarray(c, float) @ _rot(angle_deg) for c in curves]
    return _clip_curves(area, curves, kind)


def _hilbert_xy(n):
    """Cell coordinates of a Hilbert curve over an n x n grid (n a power of 2), in curve order."""
    t = np.arange(n * n)
    x = np.zeros_like(t)
    y = np.zeros_like(t)
    s_ = 1
    while s_ < n:
        rx = 1 & (t // 2)
        ry = 1 & (t ^ rx)
        flip = (ry == 0) & (rx == 1)
        x = np.where(flip, s_ - 1 - x, x)
        y = np.where(flip, s_ - 1 - y, y)
        swap = ry == 0
        x, y = np.where(swap, y, x), np.where(swap, x, y)
        x = x + s_ * rx
        y = y + s_ * ry
        t = t // 4
        s_ *= 2
    return x, y


def hilbert(area, spacing, kind):
    """Hilbert curve on a global grid of `spacing` cells (up to 1024 x 1024 cells)."""
    if area.is_empty:
        return []
    minx, miny, maxx, maxy = area.bounds
    i0, j0 = math.floor(minx / spacing), math.floor(miny / spacing)
    need = max(math.ceil(maxx / spacing) - i0, math.ceil(maxy / spacing) - j0, 2)
    n = 1 << max(1, math.ceil(math.log2(need)))
    if n > 1024:                                     # too fine for the area: coarser cells
        spacing *= n / 1024
        n = 1024
        i0, j0 = math.floor(minx / spacing), math.floor(miny / spacing)
    x, y = _hilbert_xy(n)
    pts = np.column_stack([(i0 + x + 0.5) * spacing, (j0 + y + 0.5) * spacing]).astype(float)
    return _clip_curves(area, [pts], kind)


def archimedean(area, spacing, kind):
    """Archimedean spiral (Orca's Archimedean chords) around the middle of the area."""
    if area.is_empty:
        return []
    minx, miny, maxx, maxy = area.bounds
    cx, cy = round((minx + maxx) / 2), round((miny + maxy) / 2)    # whole mm: same centre layer to layer
    rmax = math.hypot(maxx - minx, maxy - miny) / 2 + 2 * spacing
    turns = rmax / spacing
    # fine enough that the outer turn has points 0.5 mm apart; then thinned to ~0.5 mm everywhere
    th = np.linspace(0, 2 * math.pi * turns, int(min(2e6, max(64, 2 * math.pi * turns * rmax / 0.5))))
    r = spacing * th / (2 * math.pi)
    seg = np.maximum(r * np.gradient(th), 1e-6)
    keep = np.concatenate([[0], np.flatnonzero(np.diff(np.floor(np.cumsum(seg) / 0.5)) > 0) + 1, [len(th) - 1]])
    th, r = th[keep], r[keep]
    pts = np.column_stack([cx + r * np.cos(th), cy + r * np.sin(th)])
    return _clip_curves(area, [pts], kind)


def zigzag3d(area, w, density, angle_deg, z, kind):
    """3D honeycomb: 45-degree zigzag lines that shift with height and turn 90 degrees every period,
    so the layers stack into closed 3D cells."""
    s_ = math.sqrt(2) * w / density                 # spacing between zigzag lines
    period = s_
    phase = (z % (2 * period)) / (2 * period)
    turn = int(z // (2 * period)) % 2
    ang = angle_deg + 90 * turn
    rot = affinity.rotate(area, -ang, origin=(0, 0))
    minx, miny, maxx, maxy = rot.bounds
    # corner points of the zigzag: where (x / period + phase) is a multiple of 1/2
    k0 = math.floor(minx / period - 1) * 2
    xs = (np.arange(k0, k0 + 2 * ((maxx - minx) / period + 4)) / 2 - phase) * period
    # triangle wave of height period/2, moving along x with height
    tri = np.abs(((xs / period + phase) % 1.0) - 0.5) * period - period / 4
    curves = []
    for k in range(math.floor(miny / s_) - 1, math.ceil(maxy / s_) + 2):
        curves.append(np.column_stack([xs, k * s_ + tri]))
    curves = [np.asarray(c, float) @ _rot(ang) for c in curves]
    return _clip_curves(area, curves, kind)


# ---------------------------------------------------------------- dispatch

SPARSE_PATTERNS = ["rectilinear", "alignedrectilinear", "line", "grid", "triangles", "tri-hexagon", "stars",
                   "cubic", "crosshatch", "gyroid", "honeycomb", "3dhoneycomb", "lightning", "hilbertcurve",
                   "archimedeanchords", "concentric"]
SURFACE_PATTERNS = ["monotonic", "monotonicline", "rectilinear", "alignedrectilinear", "concentric",
                    "hilbertcurve", "archimedeanchords"]
GYROID_K = 2.4   # period = GYROID_K * line_width / density (calibrated: see tests)


def sparse(area, pattern, w, density, angle, layer_index, z, kind="infill"):
    if area.is_empty or density <= 0:
        return []
    d = min(density, 1.0)
    alt = angle if layer_index % 2 == 0 else angle + 90
    if pattern == "rectilinear":
        return hatch(area, w / d, alt, kind)
    if pattern == "alignedrectilinear":
        return hatch(area, w / d, angle, kind)
    if pattern == "line":            # like rectilinear, but every layer crosses the one below at 90 degrees +-45
        return hatch(area, w / d, angle + (45 if layer_index % 2 == 0 else -45), kind)
    if pattern in ("lightning",):    # the slicer limits the area to what holds up top surfaces
        return hatch(area, w / d, alt, kind)
    if pattern == "tri-hexagon":     # three directions, offset so they make hexagons and triangles
        sp = 3 * w / d
        return sum((hatch(area, sp, angle + k * 60, kind, offset=k * sp / 3) for k in range(3)), [])
    if pattern == "stars":           # three directions, one shifted half a cell: six-pointed stars
        sp = 3 * w / d
        return sum((hatch(area, sp, angle + k * 60, kind, offset=sp / 2 if k == 1 else 0) for k in range(3)), [])
    if pattern == "crosshatch":      # blocks of layers that alternate between two directions
        sp = w / d
        block = int(z // (2 * sp)) % 2
        return hatch(area, sp, angle + 90 * block, kind, unidirectional=False)
    if pattern == "3dhoneycomb":
        return zigzag3d(area, w, d, angle, z, kind)
    if pattern == "hilbertcurve":
        return hilbert(area, w / d, kind)
    if pattern == "archimedeanchords":
        return archimedean(area, w / d, kind)
    if pattern == "grid":
        return hatch(area, 2 * w / d, angle, kind) + hatch(area, 2 * w / d, angle + 90, kind)
    if pattern == "triangles":
        return sum((hatch(area, 3 * w / d, angle + k * 60, kind) for k in range(3)), [])
    if pattern == "cubic":
        sp = 3 * w / d
        shift = z / math.sqrt(2)
        return sum((hatch(area, sp, angle + k * 60, kind, offset=shift * (1 if k != 1 else -1))
                    for k in range(3)), [])
    if pattern == "gyroid":
        return gyroid(area, GYROID_K * w / d, z, angle, kind)
    if pattern == "honeycomb":
        return honeycomb(area, w, d, angle, kind)
    if pattern == "concentric":
        return concentric(area, w / d, kind)
    raise ValueError(f"Unknown infill pattern: {pattern}")


def surface(area, pattern, spacing, angle, layer_index, kind):
    """Solid fill. Returns (paths, keep_order): monotonic patterns must print in row order."""
    if area.is_empty:
        return [], False
    alt = angle if layer_index % 2 == 0 else angle + 90
    if pattern == "monotonic":
        return hatch(area, spacing, alt, kind), True
    if pattern == "monotonicline":
        # rows in order, back and forth (not all one way: that travels back across the part before every line)
        return hatch(area, spacing, alt, kind), True
    if pattern == "rectilinear":
        return hatch(area, spacing, alt, kind), False
    if pattern == "alignedrectilinear":
        return hatch(area, spacing, angle, kind), False
    if pattern == "concentric":
        return concentric(area, spacing, kind), False
    if pattern == "hilbertcurve":
        return hilbert(area, spacing, kind), True
    if pattern == "archimedeanchords":
        return archimedean(area, spacing, kind), True
    raise ValueError(f"Unknown surface pattern: {pattern}")
