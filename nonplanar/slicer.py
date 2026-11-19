"""Slicing in the bent ("flat-layer") space: outlines, walls, surfaces, infill, ironing.

Output is a list of Layer objects. Each holds 2D paths (X/Y) plus the flat layer
height z' they belong to. gcode.py later bends them back to curved 3D paths.
"""
import math
from dataclasses import dataclass, field
from functools import reduce

import numpy as np
import shapely
import shapely.ops
import trimesh
from shapely.geometry import MultiLineString, LineString, Polygon, MultiPolygon
from shapely.ops import polygonize, unary_union

from .transforms import make_transform
from . import support as support_mod
from . import patterns

EMPTY = MultiPolygon()

# feature kinds, in the order the preview legend shows them
KINDS = ["outer_wall", "inner_wall", "overhang_wall", "top_surface", "bottom_surface", "solid", "bridge",
         "infill", "ironing", "skirt", "brim", "support", "support_interface", "prime_tower"]
WALLS = ("outer_wall", "inner_wall", "overhang_wall")


@dataclass
class Path:
    kind: str
    pts: np.ndarray           # (N, 2) X/Y points
    closed: bool
    width: float = 0.42       # line width, mm
    flow: float = 1.0         # extra flow multiplier (ironing, bridges)
    speed: float = None       # overrides the feature speed (overhang tiers, small perimeters)
    fan: int = None           # fan % while printing this path (overhangs, bridges)
    tool: int = 0             # filament slot (0-based) that prints this path
    gcode: str = None         # extra G-code emitted just before this path (calibration prints)


@dataclass
class Layer:
    index: int
    zp: float                 # flat height in bent space (top of the layer)
    paths: list = field(default_factory=list)
    thickness: float = 0.2
    infill_area: object = None   # sparse infill region, for "reduce infill retraction"


# ---------------------------------------------------------------- mesh prep

def _read_3mf(path):
    """Our own reader first (keeps Bambu/Orca parts apart); trimesh's as a fallback."""
    from .threemf import load_3mf_parts
    try:
        parts = load_3mf_parts(path)
    except Exception:
        return None
    meshes = []
    for name, m in parts:
        m.metadata["name"] = name
        meshes.append(m)
    return meshes


def load_mesh(path):
    if str(path).lower().endswith(".3mf"):
        parts = _read_3mf(path)
        if parts:
            mesh = trimesh.util.concatenate(parts) if len(parts) > 1 else parts[0]
            mesh.merge_vertices()
            return mesh
    mesh = trimesh.load(path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError(f"Could not read a mesh from {path}")
    mesh.merge_vertices()
    return mesh


def load_parts(path, max_parts=64):
    """Separate parts of a model (3MF objects, or the separate shells of an STL), for multi-colour.
    Inner shells (holes inside a part, which face inwards) stay with the part around them.
    Returns a list of meshes; more than max_parts pieces are kept as one part."""
    loaded = _read_3mf(path) if str(path).lower().endswith(".3mf") else None
    if loaded:
        meshes = loaded
    else:
        loaded = trimesh.load(path)
    if isinstance(loaded, list):
        pass
    elif isinstance(loaded, trimesh.Scene):
        meshes = [g for g in loaded.dump(concatenate=False) if isinstance(g, trimesh.Trimesh) and len(g.faces)]
    else:
        meshes = [loaded]
    if not meshes:
        raise ValueError(f"Could not read a mesh from {path}")
    if len(meshes) == 1:
        m = meshes[0]
        m.merge_vertices()
        comps = m.split(only_watertight=False)
        if 1 < len(comps) <= 4 * max_parts:
            solid = [c for c in comps if c.volume >= 0]
            voids = [c for c in comps if c.volume < 0]
            for v in voids:              # give each inward-facing shell to the part that contains it
                lo, hi = v.bounds
                host = next((k for k, c in enumerate(solid)
                             if np.all(c.bounds[0] <= lo + 1e-6) and np.all(c.bounds[1] >= hi - 1e-6)), None)
                if host is None:
                    solid.append(v)
                else:
                    solid[host] = trimesh.util.concatenate([solid[host], v])
            if 1 < len(solid) <= max_parts:
                meshes = solid
    if len(meshes) > max_parts:
        meshes = [trimesh.util.concatenate(meshes)]
    for m in meshes:
        m.merge_vertices()
    return meshes


def placement_matrix(mesh, settings, placement=None):
    """4x4 transform for placement {"scale": %, "rot_z": deg, "x": mm, "y": mm, "orient": 3x3 row-major}
    (x/y = part centre on the bed). orient is a free rotation (lay on face, X/Y rotation) applied first."""
    p = placement or {}
    scale = float(p.get("scale", 100)) / 100.0
    M = np.eye(4)
    o = p.get("orient")
    if o is not None and len(o) == 9:
        R = np.asarray(o, float).reshape(3, 3)
        u, _, vt = np.linalg.svd(R)              # keep it a clean rotation
        R = u @ vt
        if np.linalg.det(R) > 0:
            M[:3, :3] = R
    M[:3, :3] *= scale
    shrink = float(getattr(settings, "filament_shrink", 100) or 100)
    if abs(shrink - 100) > 1e-6:                 # the filament shrinks: print the part a little bigger in X/Y
        M[:2, :3] *= 100.0 / shrink
    rz = float(p.get("rot_z", 0))
    if rz:
        M = trimesh.transformations.rotation_matrix(np.radians(rz), [0, 0, 1]) @ M
    v = trimesh.transform_points(mesh.vertices, M)
    lo, hi = v.min(0), v.max(0)
    cx = p.get("x", settings.bed_x / 2 if settings.center_on_bed else None)
    cy = p.get("y", settings.bed_y / 2 if settings.center_on_bed else None)
    shift = np.array([0.0, 0.0, -lo[2]])
    if cx is not None:
        shift[0] = float(cx) - (lo[0] + hi[0]) / 2
    if cy is not None:
        shift[1] = float(cy) - (lo[1] + hi[1]) / 2
    T = np.eye(4)
    T[:3, 3] = shift
    return T @ M


def load_and_place(source, settings, placement=None):
    mesh = load_mesh(source) if isinstance(source, str) else source.copy()
    mesh.apply_transform(placement_matrix(mesh, settings, placement))
    lo, hi = mesh.bounds
    if lo[0] < 0 or lo[1] < 0 or hi[0] > settings.bed_x or hi[1] > settings.bed_y:
        raise ValueError(f"Model ({hi[0]-lo[0]:.0f} x {hi[1]-lo[1]:.0f} mm) does not fit on the bed")
    return mesh


def bend_mesh(mesh, transform, max_edge=1.0):
    """Subdivide (so straight edges can curve) then apply the forward transform."""
    v, f = trimesh.remesh.subdivide_to_size(mesh.vertices, mesh.faces, max_edge=max_edge, max_iter=12)
    v = v.copy()
    v[:, 2] = transform.forward(v[:, 0], v[:, 1], v[:, 2])
    return trimesh.Trimesh(v, f, process=False)


# ---------------------------------------------------------------- slicing

def _chain_loops(segs):
    """Join segments that share end points into closed loops (fast path for clean meshes).
    Returns a list of (N, 2) closed rings, or None when the section isn't a set of simple
    closed loops (then the caller falls back to GEOS noding)."""
    P = segs.reshape(-1, 2)
    # points are rounded to 1e-5 mm: an integer key per point makes this a fast 1-D unique
    q = np.rint(P * 1e5).astype(np.int64)
    q -= q.min(0)
    if q.max() >= 2 ** 31:
        return None
    keys = (q[:, 0] << 31) | q[:, 1]
    _, first_idx, inv, counts = np.unique(keys, return_index=True, return_inverse=True, return_counts=True)
    if not np.all(counts == 2):
        return None
    U = P[first_idx]
    inv = inv.reshape(-1, 2)
    order = np.argsort(inv.ravel(), kind="stable")
    owner = (order // 2).reshape(-1, 2)            # owner[p] = the two segments touching point p
    used = np.zeros(len(segs), bool)
    inv_l, owner_l = inv.tolist(), owner.tolist()
    loops = []
    for start in range(len(segs)):
        if used[start]:
            continue
        seg = start
        used[seg] = True
        first, p = inv_l[seg]
        seq = [first]
        while True:
            seq.append(p)
            a, b = owner_l[p]
            seg = b if a == seg else a
            if used[seg]:
                break
            used[seg] = True
            q0, q1 = inv_l[seg]
            p = q1 if q0 == p else q0
        if seq[0] != seq[-1]:
            return None
        if len(seq) >= 4:
            loops.append(U[seq])
    return loops


def _loops_to_polygons(segs, resolution):
    loops = _chain_loops(segs)
    if loops is None:
        return None
    if not loops:
        return EMPTY
    rings = shapely.polygons(shapely.linearrings(np.vstack(loops),
                                                  indices=np.repeat(np.arange(len(loops)), [len(l) for l in loops])))
    if not shapely.is_valid(rings).all():
        return None                    # a loop crosses itself: let GEOS sort it out
    if len(rings) > 1:
        # loops that touch or cross each other (separate shells meeting, overlapping parts)
        # need proper noding, or they leave slivers along the shared edges
        bounds = shapely.boundary(rings)
        tree = shapely.STRtree(bounds)
        i, j = tree.query(bounds, predicate="intersects")
        if np.any(i != j):
            return None
    rings = [r for r in rings if r.area > 0]
    if not rings:
        return EMPTY
    rings.sort(key=lambda p: -p.area)
    result = reduce(lambda a, b: a.symmetric_difference(b), rings)   # even-odd: nested loops become holes
    return result.buffer(0).simplify(max(resolution, 0.001), preserve_topology=True)


def _to_polygons(segments, resolution):
    """Turn a soup of 2D segments into even-odd filled polygons."""
    if len(segments) == 0:
        return EMPTY
    segs = np.round(np.asarray(segments, float), 5)
    segs = segs[np.any(segs[:, 0] != segs[:, 1], axis=1)]
    if len(segs) == 0:
        return EMPTY
    fast = _loops_to_polygons(segs, resolution)
    if fast is not None:
        return fast
    lines = shapely.union_all(shapely.linestrings(segs))      # vectorised: much faster than a Python list
    faces = list(polygonize(lines))
    if not faces:
        return EMPTY
    # even-odd rule on the outer boundary of every face: nested loops become holes
    faces = [Polygon(f.exterior) for f in faces]
    faces.sort(key=lambda p: -p.area)
    result = reduce(lambda a, b: a.symmetric_difference(b), faces)
    return result.buffer(0).simplify(max(resolution, 0.001), preserve_topology=True)


def plane_sections(mesh, heights):
    """Intersection segments of a mesh with horizontal planes z = h, for all planes at once.

    Each triangle is paired only with the planes inside its Z range, so the cost grows with
    the number of actual crossings, not triangles x layers. Returns a list (one per height)
    of (N, 2, 2) X/Y segments."""
    heights = np.asarray(heights, float)
    V = np.asarray(mesh.vertices, float)
    F = np.asarray(mesh.faces)
    tri_z = V[F, 2]
    zlo, zhi = tri_z.min(1), tri_z.max(1)
    # planes k with zlo <= h_k < zhi (a vertex exactly on a plane counts as above it)
    k0 = np.searchsorted(heights, zlo, side="left")
    k1 = np.searchsorted(heights, zhi, side="left")
    cnt = k1 - k0
    face = np.repeat(np.arange(len(F)), cnt)
    plane = np.repeat(k0, cnt) + (np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt))
    out = [np.zeros((0, 2, 2)) for _ in heights]
    if len(face) == 0:
        return out
    h = heights[plane]
    vi = F[face]                                  # (P, 3) vertex indices
    above = V[vi, 2] >= h[:, None]                # (P, 3)
    segs = []
    # each crossing triangle has exactly two crossing edges; collect them in a fixed order
    ends = []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        cross = above[:, a] != above[:, b]
        ia, ib = vi[:, a], vi[:, b]
        # interpolate from the lower vertex index to the higher one, so the two triangles
        # sharing an edge compute bit-identical points (needed to join the loops)
        lo = np.minimum(ia, ib)
        hi = np.maximum(ia, ib)
        za, zb = V[lo, 2], V[hi, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (h - za) / (zb - za)
        p = V[lo, :2] + (V[hi, :2] - V[lo, :2]) * t[:, None]
        ends.append((cross, p))
    c0, p0 = ends[0]
    c1, p1 = ends[1]
    c2, p2 = ends[2]
    first = np.where(c0[:, None], p0, p1)
    second = np.where((c0 & c1)[:, None], p1, p2)
    ok = (c0.astype(int) + c1 + c2) == 2
    seg = np.stack([first, second], axis=1)[ok]
    pl = plane[ok]
    order = np.argsort(pl, kind="stable")
    seg, pl = seg[order], pl[order]
    cuts = np.searchsorted(pl, np.arange(len(heights) + 1))
    for k in range(len(heights)):
        if cuts[k + 1] > cuts[k]:
            out[k] = seg[cuts[k]:cuts[k + 1]]
    return out


def slice_regions(bent, zs, resolution=0.012):
    """Cut the bent mesh at every layer height into filled outlines."""
    heights = np.asarray(zs, float)
    return [_to_polygons(sg, resolution) for sg in plane_sections(bent, heights)]


def compensate(region, s):
    """X-Y contour / hole compensation."""
    if region.is_empty or (s.xy_contour_compensation == 0 and s.xy_hole_compensation == 0):
        return region
    out = []
    for poly in _polys(region):
        outer = Polygon(poly.exterior).buffer(s.xy_contour_compensation, join_style=2, mitre_limit=2.0)
        holes = [Polygon(h).buffer(s.xy_hole_compensation, join_style=2, mitre_limit=2.0) for h in poly.interiors]
        out.append(outer.difference(unary_union(holes)) if holes else outer)
    return unary_union(out)


# ---------------------------------------------------------------- helpers

def _polys(geom):
    return patterns._polys(geom)


def _open(geom, r):
    """Drop slivers narrower than 2*r."""
    if geom.is_empty:
        return EMPTY
    return geom.buffer(-r, join_style=2).buffer(r, join_style=2)


def spacing(w, h):
    """Centre distance between neighbouring lines (Orca's rounded-rectangle extrusion model)."""
    return w - h * (1 - math.pi / 4)


def _length(pts):
    return float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())


class Seamer:
    """Chooses where each closed loop starts."""

    def __init__(self, mode, seed=1):
        self.mode = mode
        self.prev, self.cur = [], []
        self.rng = np.random.default_rng(seed)

    def next_layer(self):
        if self.cur:
            self.prev, self.cur = self.cur, []

    def pick(self, ring):
        """ring: (N+1, 2) closed. Returns the start vertex index, or None for 'nearest'."""
        pts = ring[:-1]
        if self.mode == "nearest" or len(pts) < 3:
            return None
        if self.mode == "random":
            k = int(self.rng.integers(len(pts)))
        elif self.mode == "back":
            k = int(np.argmax(pts[:, 1] - 1e-3 * np.abs(pts[:, 0] - pts[:, 0].mean())))
        else:  # aligned: stay near last layer's seam, prefer sharp corners
            a, b = np.roll(pts, 1, axis=0) - pts, np.roll(pts, -1, axis=0) - pts
            cos = np.einsum("ij,ij->i", a, b) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1) + 1e-9)
            sharp = np.clip((cos + 0.5) / 1.5, 0, 1)      # 0 = straight, 1 = very sharp corner
            if self.prev:
                prev = np.asarray(self.prev)
                d = np.min(np.linalg.norm(pts[:, None] - prev[None], axis=2), axis=1)
            else:
                d = -pts[:, 1]                             # first layer: towards the back
                d = d - d.min()
            k = int(np.argmin(d - 3.0 * sharp))
        self.cur.append(pts[k].copy())
        return k


def order_paths(paths, start, seamer=None, keep_order=False):
    """Greedy nearest-neighbour ordering. Closed loops start at the seam (or nearest point)."""
    pos = np.asarray(start, float)
    if keep_order:
        if paths and not paths[0].closed:
            d0 = np.sum((paths[0].pts[0] - pos) ** 2)
            d1 = np.sum((paths[-1].pts[-1] - pos) ** 2)
            if d1 < d0:
                paths = [_with(p, p.pts[::-1]) for p in reversed(paths)]
        return list(paths)
    if not paths:
        return []
    n = len(paths)
    closed = np.array([p.closed for p in paths])
    seams = [seamer.pick(p.pts) if (seamer is not None and p.closed) else None for p in paths]
    starts = np.array([p.pts[0] for p in paths], float)
    ends = np.array([p.pts[-1] for p in paths], float)
    for i in range(n):           # fixed seam: the loop starts and ends there
        if closed[i] and seams[i] is not None:
            starts[i] = ends[i] = paths[i].pts[seams[i]]
    free_loops = [i for i in range(n) if closed[i] and seams[i] is None]
    left = np.ones(n, bool)
    out = []
    for _ in range(n):
        d0 = np.sum((starts - pos) ** 2, axis=1)
        d1 = np.where(closed, np.inf, np.sum((ends - pos) ** 2, axis=1))
        best_k = np.zeros(n, int)
        for i in free_loops:      # loops without a fixed seam may start at any vertex
            if left[i]:
                d2 = np.sum((paths[i].pts[:-1] - pos) ** 2, axis=1)
                best_k[i] = int(np.argmin(d2))
                d0[i] = d2[best_k[i]]
        d = np.minimum(d0, d1)
        d[~left] = np.inf
        i = int(np.argmin(d))
        left[i] = False
        p = paths[i]
        if p.closed:
            k = seams[i] if seams[i] is not None else best_k[i]
            if k:
                ring = p.pts[:-1]
                ring = np.vstack([ring[k:], ring[:k]])
                p = _with(p, np.vstack([ring, ring[:1]]))
        elif d1[i] < d0[i]:
            p = _with(p, p.pts[::-1])
        out.append(p)
        pos = p.pts[-1]
    return out


def _with(p, pts, **kw):
    d = dict(kind=p.kind, pts=pts, closed=p.closed, width=p.width, flow=p.flow, speed=p.speed, fan=p.fan, tool=p.tool)
    d.update(kw)
    return Path(**d)


def _as_paths(raw, width, **kw):
    return [Path(r.kind, r.pts, r.closed, width, **kw) for r in raw]


# ---------------------------------------------------------------- walls

def island_walls(island, s, n_walls, widths, h, top_exposed):
    """Concentric walls of one island. Returns (loops_by_depth, fill_area).
    loops_by_depth[0] is the outer wall. With only_one_wall_top, inner walls skip the
    top-surface area and that area is handed to the top fill instead."""
    w_out, w_in = widths
    depth = []
    d = w_out / 2
    loops = []
    for k in range(n_walls):
        w = w_out if k == 0 else w_in
        if k > 0:
            d += (spacing(w_out if k == 1 else w_in, h) + spacing(w, h)) / 2
        g = island.buffer(-d, join_style=2, mitre_limit=2.0)
        if g.is_empty:
            break
        ring_paths = []
        for poly in _polys(g):
            for ring in [poly.exterior, *poly.interiors]:
                pts = np.asarray(ring.coords)
                if len(pts) >= 4:
                    ring_paths.append(Path("outer_wall" if k == 0 else "inner_wall", pts, True, w))
        loops.append(ring_paths)
        depth.append(d + spacing(w, h) / 2)
    if not loops:
        return [], EMPTY
    fill = island.buffer(-depth[-1], join_style=2, mitre_limit=2.0)
    if s.only_one_wall_top and len(loops) > 1 and not top_exposed.is_empty:
        top = top_exposed.intersection(island)
        if not top.is_empty:
            cut = top.buffer(spacing(w_in, h) / 2)
            for k in range(1, len(loops)):
                pieces = []
                for p in loops[k]:
                    full = LineString(p.pts)
                    rest = full.difference(cut)
                    if rest.is_empty:
                        continue
                    if rest.geom_type == "LineString" and abs(rest.length - full.length) < 1e-6:
                        pieces.append(p)          # untouched: keep it a closed loop
                        continue
                    for g in patterns._lines(rest, 0.5):
                        pieces.append(Path("inner_wall", np.asarray(g.coords), False, p.width))
                loops[k] = pieces
            fill = unary_union([fill, island.buffer(-depth[0], join_style=2, mitre_limit=2.0).intersection(top)])
    return loops, fill


def wall_sequence(loops, s, pos, seamer):
    """Order wall loops (outer = depth 0) by the chosen wall sequence."""
    if not loops:
        return []
    order = {
        "inner_outer": list(range(len(loops) - 1, 0, -1)) + [0],
        "outer_inner": list(range(len(loops))),
        "inner_outer_inner": list(range(len(loops) - 1, 1, -1)) + [0] + ([1] if len(loops) > 1 else []),
    }.get(s.wall_sequence)
    if order is None:
        raise ValueError(f"Unknown wall sequence {s.wall_sequence}")
    out = []
    for k in order:
        group = order_paths(loops[k], pos, seamer)
        out += group
        if group:
            pos = group[-1].pts[-1]
    return out


def overhang_split(p, prev, s, speeds):
    """Split a wall into runs by how far it hangs over the layer below (Orca's 4 overhang tiers)."""
    w = p.width
    if prev is None or prev.is_empty:
        return [p]
    line = LineString(p.pts)
    if line.within(prev.buffer(-0.45 * w)):
        return [p]
    pts = _densify(p.pts, 0.5)
    pts_geom = shapely.points(pts)
    outside = shapely.distance(prev, pts_geom)
    inside = shapely.distance(prev.boundary, pts_geom)
    signed = np.where(outside > 0, outside, -inside)
    frac = np.clip((signed + w / 2) / w, 0, 1)
    tier = np.where(frac < 0.05, 0, np.ceil(frac * 4)).astype(int)
    if not tier.any():
        return [p]
    # a segment takes the larger tier of its two ends
    seg_tier = np.maximum(tier[:-1], tier[1:])
    out, start = [], 0
    for i in range(1, len(seg_tier) + 1):
        if i == len(seg_tier) or seg_tier[i] != seg_tier[start]:
            t = int(seg_tier[start])
            run = pts[start:i + 1]
            run = np.asarray(LineString(run).simplify(s.resolution).coords) if len(run) > 2 else run
            if t == 0:
                out.append(_with(p, run, closed=False))
            else:
                sp = speeds[t - 1] if s.enable_overhang_speed and speeds[t - 1] > 0 else None
                fan = s.overhang_fan_speed if frac_threshold_tier(s) <= t else None
                out.append(_with(p, run, closed=False, kind="overhang_wall", speed=sp, fan=fan))
            start = i
    return out


def frac_threshold_tier(s):
    """Lowest overhang tier (1-4) that turns on the overhang fan. Tier t = overhang in ((t-1)/4, t/4]."""
    return min(5, int(s.overhang_fan_threshold * 4 + 1e-9) + 1)


def _densify(pts, max_seg):
    out = [pts[:1]]
    for a, b in zip(pts[:-1], pts[1:]):
        n = max(1, int(np.ceil(np.linalg.norm(b - a) / max_seg)))
        out.append(a + (b - a) * (np.arange(1, n + 1)[:, None] / n))
    return np.vstack(out)


# ---------------------------------------------------------------- main entry

def _noop(*a, **k):
    pass


def paint_areas(mesh, faces, tf, tops, thick, s):
    """Painted triangles (indices into the placed mesh) as one XY area per layer, in the bent space."""
    faces = np.asarray(faces, dtype=np.int64)
    faces = faces[(faces >= 0) & (faces < len(mesh.faces))]
    n = len(tops)
    if not len(faces):
        return None
    sub = trimesh.Trimesh(mesh.vertices, mesh.faces[faces], process=False)
    b = bend_mesh(sub, tf, max_edge=max(0.5, s.line_width * 2))
    tri = b.triangles
    zlo, zhi = tri[:, :, 2].min(1), tri[:, :, 2].max(1)
    bottoms = tops - thick
    i0 = np.searchsorted(tops, zlo - 0.5 * s.layer_height)
    i1 = np.searchsorted(bottoms, zhi + 0.5 * s.layer_height, side="right") - 1
    polys = shapely.polygons(tri[:, :, :2])
    per = [[] for _ in range(n)]
    for t in range(len(tri)):
        for i in range(max(0, i0[t]), min(n - 1, i1[t]) + 1):
            per[i].append(t)
    grow = 0.5 * s.support_line_width
    return [shapely.union_all(polys[idx]).buffer(grow, join_style=2) if idx else EMPTY for idx in per]


def slice_model(source, settings, log=print, progress=_noop, placement=None,
                parts=None, part_tools=None, tool_changes=None, paint=None, matrix=None):
    """source: file path or trimesh. progress(fraction 0..1, text).

    Multi-colour (AMS): parts = list of meshes in the same coordinates as source,
    part_tools = filament slot (0-based) per part, tool_changes = [(printed layer number, slot)]:
    from that layer on, the whole model prints with that slot."""
    s = settings
    mesh = load_mesh(source) if isinstance(source, str) else source.copy()
    M = placement_matrix(mesh, s, placement) if matrix is None else matrix    # matrix: one object of a plate
    mesh.apply_transform(M)
    lo, hi = mesh.bounds
    if lo[0] < 0 or lo[1] < 0 or hi[0] > s.bed_x or hi[1] > s.bed_y:
        raise ValueError(f"Model ({hi[0]-lo[0]:.0f} x {hi[1]-lo[1]:.0f} mm) does not fit on the bed")
    progress(0.02, "Preparing model")
    samples = np.vstack([mesh.vertices, trimesh.sample.sample_surface(mesh, 30000, seed=1)[0]])
    log(f"Model {mesh.extents.round(1)} mm, {len(mesh.faces)} faces")
    tf = make_transform(s, samples, mesh, log=log)
    if s.mode == "conical":
        log(f"Mode {s.mode}: {tf.angle:+.1f} deg around ({tf.cx:.1f}, {tf.cy:.1f}), ramp {tf.H:.1f} mm")

    part_tools = [int(t) for t in (part_tools or [])]
    multi_part = parts is not None and len(parts) > 1 and len(set(part_tools[:len(parts)])) > 1
    progress(0.05, "Bending model")
    bent = bend_mesh(mesh, tf, max_edge=max(0.5, s.line_width * 2))
    zmax = bent.bounds[1][2]
    h, h1 = s.layer_height, s.first_layer_height
    tops = np.concatenate([[h1], np.arange(h1 + h, zmax + 1e-6, h)]) if zmax > h1 else np.array([h1])
    thick = np.diff(np.concatenate([[0.0], tops]))
    mids = tops - thick / 2
    log(f"Slicing {len(tops)} bent layers ({len(bent.faces)} faces after subdivision)")
    progress(0.12, "Slicing layers")

    def prep(r):  # snap outlines to a 0.1 micron grid: keeps GEOS boolean operations robust
        return shapely.set_precision(compensate(r, s), 1e-4)

    if multi_part:
        # slice every part on its own; where parts overlap, the smaller part wins (inlays, letters)
        part_regions = []
        for k, pm in enumerate(parts):
            pm = pm.copy()
            pm.apply_transform(M)
            pb = bend_mesh(pm, tf, max_edge=max(0.5, s.line_width * 2))
            part_regions.append([prep(r) for r in slice_regions(pb, mids, s.resolution)])
            progress(0.12 + 0.06 * (k + 1) / len(parts), f"Slicing part {k + 1}/{len(parts)}")
        order = np.argsort([abs(p.volume) for p in parts])
        tools = sorted(set(part_tools[:len(parts)]))
        groups = {t: [] for t in tools}
        regions = []
        for i in range(len(tops)):
            claimed = EMPTY
            per_tool = {t: [] for t in tools}
            for k in order:
                r = part_regions[k][i]
                if r.is_empty:
                    continue
                r = r.difference(claimed) if not claimed.is_empty else r
                if r.is_empty:
                    continue
                claimed = claimed.union(r)
                per_tool[part_tools[k]].append(r)
            for t in tools:
                groups[t].append(shapely.set_precision(unary_union(per_tool[t]), 1e-4) if per_tool[t] else EMPTY)
            regions.append(shapely.set_precision(claimed, 1e-4))
        log(f"{len(parts)} parts in {len(tools)} filaments: " + ", ".join(f"filament {t + 1}" for t in tools))
    else:
        regions = [prep(r) for r in slice_regions(bent, mids, s.resolution)]
        groups = {(part_tools[0] if part_tools else 0): regions}
    n = len(regions)

    ctx = dict(n=n, last_layer=max((i for i, r in enumerate(regions) if not r.is_empty), default=0),
               n_top=0 if s.top_layers == 0 else max(s.top_layers, math.ceil(s.top_shell_thickness / h - 1e-6)),
               n_bot=0 if s.bottom_layers == 0 else max(s.bottom_layers, math.ceil(s.bottom_shell_thickness / h - 1e-6)),
               ov_speeds=(s.overhang_1_4_speed, s.overhang_2_4_speed, s.overhang_3_4_speed, s.overhang_4_4_speed))
    if s.infill_pattern == "lightning" and s.infill_density > 0:
        ctx["lightning"] = _lightning_areas(regions, s)

    enforce = block = None
    if paint and (paint.get("enforce") or paint.get("block")):
        if sum(len(p.faces) for p in (parts or [])) not in (0, len(mesh.faces)):
            log("WARNING: painted supports ignored: the model's triangles changed since painting")
        else:
            enforce = paint_areas(mesh, paint.get("enforce") or [], tf, tops, thick, s)
            block = paint_areas(mesh, paint.get("block") or [], tf, tops, thick, s)
            log(f"Painted supports: {len(paint.get('enforce') or [])} enforced, "
                f"{len(paint.get('block') or [])} blocked triangles")
    sup = None
    if s.enable_support or enforce is not None:
        progress(0.2, "Generating support")
        sup = support_mod.generate(regions, thick, s, log=log, enforce=enforce, block=block)

    # Multimaterial tab: features printed with a fixed filament (0 = the object's filament)
    feature_tool = {}
    for kinds, val in ((WALLS, s.wall_filament), (("infill",), s.sparse_infill_filament),
                       (("solid", "top_surface", "bottom_surface", "bridge"), s.solid_infill_filament),
                       (("support",), s.support_filament), (("support_interface",), s.support_interface_filament)):
        if val and int(val) > 0:
            for k in kinds:
                feature_tool[k] = int(val) - 1
    changes = sorted((int(l), int(t)) for l, t in (tool_changes or []))

    seamer = Seamer(s.seam_position)
    layers = []
    skirt_fp = regions[0]
    pos = (s.bed_x / 2, s.bed_y / 2)
    current = None            # filament loaded at the end of the previous layer
    printed_n = 0             # printed layers so far (what the preview slider counts)
    for i, region in enumerate(regions):
        hi_ = float(thick[i])
        layer = Layer(i, float(tops[i]), thickness=hi_)
        first = i == 0
        fw = s.first_layer_line_width
        W = dict(outer=fw if first else s.outer_wall_line_width, inner=fw if first else s.inner_wall_line_width,
                 top=fw if first else s.top_surface_line_width, solid=fw if first else s.solid_infill_line_width,
                 sparse=fw if first else s.sparse_infill_line_width)

        sup_paths = _support_paths(sup[i], i, s, pos) if sup else []
        if sup_paths:
            pos = sup_paths[-1].pts[-1]
        for p in sup_paths:
            p.tool = -1                                    # -1 = whatever filament is loaded
        rule = [t for l, t in changes if l <= printed_n + 1]
        model_paths, sparse_areas = [], []
        for t, g_regions in groups.items():
            g = g_regions[i]
            if g.is_empty:
                continue
            ps, sp, pos = _region_paths(i, g, regions, W, hi_, s, ctx, seamer, pos, layer.zp)
            tool = rule[-1] if rule else t
            for p in ps:
                p.tool = tool
            model_paths += ps
            sparse_areas += sp
        layer.paths = sup_paths + model_paths
        layer.infill_area = unary_union(sparse_areas) if sparse_areas else None
        if first:
            skirt_fp = region.union(unary_union([sup[0][0], sup[0][1]])) if sup else region
        if not region.is_empty and i < max(1, s.skirt_height):
            sk = _skirt_brim(skirt_fp, s, first)
            for p in sk:
                p.tool = -1
            layer.paths = sk + layer.paths
        for p in layer.paths:
            if p.kind in feature_tool:
                p.tool = feature_tool[p.kind]
        layer.paths, current = _order_by_tool(layer.paths, current)
        if layer.paths:
            pos = layer.paths[-1].pts[-1]
            printed_n += 1
        layers.append(layer)
        seamer.next_layer()
        if i % 10 == 0:
            progress(0.18 + 0.44 * i / n, f"Toolpaths: layer {i}/{n}")
    return mesh, tf, layers


def _order_by_tool(paths, current):
    """Each filament prints all its paths of the layer in one go (fewest changes),
    starting with the one already loaded. Paths marked -1 (support, skirt) use that one."""
    tools = sorted({p.tool for p in paths if p.tool >= 0})
    if not tools:
        cur = current if current is not None else 0
        for p in paths:
            p.tool = cur
        return paths, (cur if paths else current)
    first = current if current in tools else tools[0]
    order = [first] + [t for t in tools if t != first]
    for p in paths:
        if p.tool < 0:
            p.tool = first
    out = [p for t in order for p in paths if p.tool == t]
    return out, order[-1]


def _lightning_areas(regions, s):
    """Lightning infill: only what holds up the top surfaces. Points are put under every top
    surface; going down they lean towards each other (at most the lightning angle), merge and
    stay inside the part, like upside-down trees. Each layer's infill area is a small disk
    around every point, so the infill thins out into a few columns instead of filling the part."""
    from .support import _grid_points
    n = len(regions)
    sp = s.sparse_infill_line_width / max(0.05, min(1.0, s.infill_density))
    move = s.layer_height * math.tan(math.radians(max(5.0, min(80.0, s.lightning_angle))))
    inset = s.sparse_infill_line_width
    out = [EMPTY] * n
    X = np.zeros((0, 2))
    for i in range(n - 2, -1, -1):
        roof = regions[i + 1].difference(regions[i + 2]) if i + 2 < n and not regions[i + 2].is_empty else regions[i + 1]
        if not roof.is_empty and roof.area > 0.05:
            new = np.asarray(_grid_points(roof, sp)).reshape(-1, 2)
            if len(X) and len(new):
                d = np.sqrt(((new[:, None] - X[None]) ** 2).sum(-1)).min(1)
                new = new[d > 0.7 * sp]
            if len(new):
                X = np.vstack([X, new])
        if not len(X):
            continue
        # lean towards the neighbours
        D = np.sqrt(((X[:, None] - X[None]) ** 2).sum(-1))
        near = D < 2.5 * sp
        step = (near[:, :, None] * X[None]).sum(1) / near.sum(1)[:, None] - X
        ln = np.linalg.norm(step, axis=1)
        X = X + step * (np.minimum(ln, move) / np.maximum(ln, 1e-9))[:, None]
        # stay inside the part
        inside = regions[i].buffer(-inset) if not regions[i].is_empty else EMPTY
        if inside.is_empty:
            X = np.zeros((0, 2))
            continue
        ok = shapely.contains_xy(inside, X[:, 0], X[:, 1])
        for q in np.flatnonzero(~ok):
            e = shapely.ops.nearest_points(inside, shapely.Point(X[q]))[0]
            if math.hypot(e.x - X[q, 0], e.y - X[q, 1]) <= 2 * move:
                X[q] = (e.x, e.y)
                ok[q] = True
        X = X[ok]
        # merge points that met
        if len(X) > 1:
            keep = np.ones(len(X), bool)
            D = np.sqrt(((X[:, None] - X[None]) ** 2).sum(-1))
            for a_ in range(len(X)):
                if keep[a_]:
                    close = np.flatnonzero(keep & (D[a_] < 0.5 * sp))
                    close = close[close != a_]
                    keep[close] = False
            X = X[keep]
        if len(X):
            disks = shapely.union_all(shapely.buffer(shapely.points(X), 0.75 * sp, quad_segs=6))
            out[i] = shapely.set_precision(disks.intersection(regions[i]), 1e-4)
    return out


def fuzzy(pts, thickness, point_dist, rng):
    """Fuzzy skin: resample a closed loop every ~point_dist and push each point in or out at random."""
    pts = np.asarray(pts, float)
    if len(pts) < 4:
        return pts
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    L = float(seg.sum())
    if L < 3 * point_dist:
        return pts
    cum = np.concatenate([[0], np.cumsum(seg)])
    n = max(8, int(L / point_dist))
    t = (np.arange(n) + rng.uniform(-0.3, 0.3, n)) * (L / n)
    t = np.clip(np.sort(t), 0, L)
    x, y = np.interp(t, cum, pts[:, 0]), np.interp(t, cum, pts[:, 1])
    q = np.column_stack([x, y])
    tang = np.roll(q, -1, 0) - np.roll(q, 1, 0)
    nrm = np.column_stack([tang[:, 1], -tang[:, 0]])
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1), 1e-9)[:, None]
    q = q + nrm * rng.uniform(-thickness / 2, thickness / 2, n)[:, None]
    return np.vstack([q, q[:1]])


def _region_paths(i, region, ref, W, hi_, s, ctx, seamer, pos, zp):
    """Walls, surfaces, infill and ironing for one filament's area on layer i.
    `ref` is the whole model's layers, so top/bottom surfaces appear only where the
    model ends, not where one colour meets another."""
    n, n_top, n_bot = ctx["n"], ctx["n_top"], ctx["n_bot"]
    first = i == 0
    printed = region.buffer(-s.elefant_foot_compensation, join_style=2) if first and s.elefant_foot_compensation > 0 else region
    above = ref[i + 1] if i + 1 < n else EMPTY
    below = ref[i - 1] if i > 0 else EMPTY
    top_exposed = _open(region.difference(above), 0.5 * W["top"]) if not above.is_empty else region
    # areas that need solid fill: not covered by n_top layers above and n_bot below
    ups = [ref[j] for j in range(i + 1, min(n, i + 1 + n_top))]
    downs = [ref[j] for j in range(max(0, i - n_bot), i)]
    if len(ups) < n_top or len(downs) < n_bot:
        covered = EMPTY
    elif not ups and not downs:
        covered = region.buffer(2 * W["solid"])     # no shells at all: nothing solid
    else:
        covered = reduce(lambda a, b: a.intersection(b), ups + downs)
    unsupported = _open(region.difference(below), 0.5 * W["solid"]) if i > 0 else EMPTY

    n_walls = 1 if (first and s.only_one_wall_first_layer) else s.wall_count
    if s.alternate_extra_wall and not first and i % 2 == 1:
        n_walls += 1
    rng = np.random.default_rng(1000 + i) if s.fuzzy_skin != "none" else None
    angle = s.infill_direction
    islands = _polys(printed)
    layer_paths, ironing, sparse_areas = [], [], []
    for isl in _island_order(islands, pos):
        loops, fill = island_walls(isl, s, n_walls, (W["outer"], W["inner"]), hi_, top_exposed)
        walls = wall_sequence(loops, s, pos, seamer)
        if rng is not None and not first:
            fz = ("outer_wall",) if s.fuzzy_skin == "external" else ("outer_wall", "inner_wall")
            walls = [_with(p, fuzzy(p.pts, s.fuzzy_skin_thickness, s.fuzzy_skin_point_distance, rng))
                     if p.closed and p.kind in fz else p for p in walls]
        # small perimeters + overhang tiers
        wall_paths = []
        for p in walls:
            if p.closed and s.small_perimeter_threshold > 0 and _length(p.pts) < 2 * math.pi * s.small_perimeter_threshold:
                p = _with(p, p.pts, speed=s.small_perimeter_speed * s.outer_wall_speed)
            wall_paths += overhang_split(p, below if i > 0 else None, s, ctx["ov_speeds"])

        fills = []
        if not fill.is_empty:
            fill_area = fill.buffer(W["sparse"] * s.infill_overlap)
            solid = fill_area.difference(covered.buffer(-W["solid"])) if not covered.is_empty else fill_area
            solid = _open(solid, 0.01)
            sparse = fill_area.difference(solid)
            # small sparse pockets become solid
            small = [p for p in _polys(sparse) if p.area < s.minimum_sparse_infill_area]
            if small:
                solid = unary_union([solid] + small)
                sparse = sparse.difference(unary_union(small))
            every = int(s.solid_infill_every_layers or 0)
            if every > 0 and i > 0 and (i + 1) % every == 0 and not sparse.is_empty:
                solid = unary_union([solid, sparse])
                sparse = EMPTY
            if "lightning" in ctx and not sparse.is_empty:
                sparse = sparse.intersection(ctx["lightning"][i]) if not ctx["lightning"][i].is_empty else EMPTY
                sparse = _open(sparse, 0.01) if not sparse.is_empty else EMPTY
            bridge = solid.intersection(unsupported) if not unsupported.is_empty else EMPTY
            bridge = _open(bridge, 0.5 * W["solid"])
            rest = solid.difference(bridge) if not bridge.is_empty else solid
            top = rest.intersection(top_exposed) if not first else EMPTY
            top = _open(top, 0.25 * W["top"])
            internal = rest.difference(top) if not top.is_empty else rest
            cur = walls[-1].pts[-1] if walls else pos

            def add(area, pattern, w, kind, ang=s.solid_infill_direction, **kw):
                nonlocal cur
                if area.is_empty:
                    return
                sp = spacing(w, hi_)
                if kind == "top_surface":
                    sp /= max(0.1, s.top_surface_density)
                elif kind == "bottom_surface":
                    sp /= max(0.1, s.bottom_surface_density)
                raw, keep = patterns.surface(area, pattern, sp, ang, i, kind)
                ps = order_paths(_as_paths(raw, w, **kw), cur, keep_order=keep)
                fills.extend(ps)
                if ps:
                    cur = ps[-1].pts[-1]

            if first:
                add(internal, s.bottom_surface_pattern, W["solid"], "bottom_surface")
            else:
                for b in _polys(bridge):
                    raw = patterns.hatch(b, W["solid"], s.bridge_angle or _bridge_angle(b), "bridge")
                    ps = order_paths(_as_paths(raw, W["solid"], flow=s.bridge_flow, fan=s.overhang_fan_speed), cur)
                    fills.extend(ps)
                    if ps:
                        cur = ps[-1].pts[-1]
                add(internal, s.internal_solid_infill_pattern, W["solid"], "solid")
                add(top, s.top_surface_pattern, W["top"], "top_surface")
            if not sparse.is_empty and s.infill_density > 0:
                raw = patterns.sparse(sparse, s.infill_pattern, W["sparse"], s.infill_density, angle, i, zp)
                fills += order_paths(_as_paths(raw, W["sparse"]), cur)
                sparse_areas.append(sparse)
            # ironing
            iron_area = {"top": top, "topmost": top if i == ctx["last_layer"] else EMPTY,
                         "solid": unary_union([top, internal, bridge])}.get(s.ironing_type, EMPTY)
            if not iron_area.is_empty:
                iron_area = iron_area.buffer(-s.ironing_inset) if s.ironing_inset > 0 else iron_area
                if s.ironing_pattern == "concentric":
                    raw = patterns.concentric(iron_area, s.ironing_spacing, "ironing")
                else:
                    ia = s.ironing_angle if s.ironing_angle >= 0 else s.solid_infill_direction + 90 + (90 if i % 2 else 0)
                    raw = patterns.hatch(iron_area, s.ironing_spacing, ia, "ironing")
                # Orca: each ironing line extrudes ironing_flow x a normal top-surface line
                ironing += _as_paths(raw, W["top"], flow=s.ironing_flow, speed=s.ironing_speed)
        layer_paths += (fills + wall_paths) if s.is_infill_first else (wall_paths + fills)
        if layer_paths:
            pos = layer_paths[-1].pts[-1]
    if ironing:
        layer_paths += order_paths(ironing, pos)
        pos = layer_paths[-1].pts[-1]
    return layer_paths, sparse_areas, pos


def _island_order(islands, pos):
    """Visit islands nearest-first."""
    out, p = [], np.asarray(pos, float)
    rest = list(islands)
    while rest:
        d = [np.min(np.sum((np.asarray(isl.exterior.coords) - p) ** 2, axis=1)) for isl in rest]
        isl = rest.pop(int(np.argmin(d)))
        out.append(isl)
        c = isl.exterior.coords[0]
        p = np.asarray(c)
    return out


def _bridge_angle(area):
    """Bridge lines run across the short span of the area (anchored on both sides)."""
    rect = np.asarray(area.minimum_rotated_rectangle.exterior.coords)
    e1, e2 = rect[1] - rect[0], rect[2] - rect[1]
    short = e1 if np.linalg.norm(e1) < np.linalg.norm(e2) else e2
    return math.degrees(math.atan2(short[1], short[0]))


def _support_paths(areas, i, s, pos):
    """Support toolpaths for one layer: sparse lines plus dense interface just under the part."""
    base, interface = areas
    w = s.first_layer_line_width if i == 0 else s.support_line_width
    paths = []
    walls = int(s.support_wall_count or 0)
    if s.support_type == "tree" and walls == 0:
        walls = 2 if s.support_style == "tree_strong" else 1      # branches need a wall to hold together
    if not base.is_empty:
        inner = base
        for k in range(walls):                                    # walls around the support areas
            ring = base.buffer(-(k + 0.5) * w, join_style=2)
            for poly in patterns._polys(ring):
                for r_ in [poly.exterior, *poly.interiors]:
                    if r_.length > 1.0:
                        paths.append(Path("support", np.asarray(r_.coords), True, w))
            inner = base.buffer(-(k + 1) * w, join_style=2)
        sp = s.support_spacing if i > 0 else max(w * 1.5, s.support_spacing / 2)
        pat = s.support_base_pattern
        if not inner.is_empty and (i == 0 or not walls or pat != "hollow"):
            if pat == "honeycomb" and i > 0:
                raw = patterns.honeycomb(inner, w, min(1.0, w / sp), s.support_angle, "support")
            else:
                raw = patterns.hatch(inner, sp, s.support_angle, "support")
                if pat == "rectilinear-grid":
                    raw += patterns.hatch(inner, sp, s.support_angle + 90, "support")
            paths += order_paths(_as_paths(raw, w), paths[-1].pts[-1] if paths else pos)
        paths = order_paths(paths, pos) if walls else paths
    if not interface.is_empty:
        start = paths[-1].pts[-1] if paths else pos
        if s.support_interface_pattern == "concentric":
            raw = patterns.concentric(interface, s.support_interface_spacing, "support_interface")
        elif s.support_interface_pattern == "grid":
            raw = (patterns.hatch(interface, 2 * s.support_interface_spacing, s.support_angle, "support_interface")
                   + patterns.hatch(interface, 2 * s.support_interface_spacing, s.support_angle + 90, "support_interface"))
        else:
            raw = patterns.hatch(interface, s.support_interface_spacing, s.support_angle + (90 if i % 2 else 0),
                                 "support_interface")
        paths += order_paths(_as_paths(raw, w), start)
    return paths


def _skirt_brim(region, s, first):
    """Skirt (on the first skirt_height layers) and brim (first layer only)."""
    w = s.first_layer_line_width if first else s.outer_wall_line_width
    sp = spacing(w, s.first_layer_height)
    paths = []
    brim_loops = 0 if s.brim_type in ("no_brim", "brim_ears") else max(0, int(round(s.brim_width / sp)))
    outer_brim = brim_loops if s.brim_type in ("outer_only", "outer_and_inner") else 0
    for k in range(s.skirt_loops):
        ring = region.buffer(s.brim_object_gap * (outer_brim > 0) + outer_brim * sp + s.skirt_distance + k * sp).convex_hull
        for poly in _polys(ring):
            paths.append(Path("skirt", np.asarray(poly.exterior.coords), True, w))
    if first and brim_loops:
        gap = s.brim_object_gap
        for k in reversed(range(brim_loops)):
            d = gap + w / 2 + k * sp
            for poly in _polys(region):
                if s.brim_type in ("outer_only", "outer_and_inner"):
                    for g in _polys(Polygon(poly.exterior).buffer(d)):
                        paths.append(Path("brim", np.asarray(g.exterior.coords), True, w))
                if s.brim_type in ("inner_only", "outer_and_inner"):
                    for hole in poly.interiors:
                        g = Polygon(hole).buffer(-d)
                        for gp in _polys(g):
                            paths.append(Path("brim", np.asarray(gp.exterior.coords), True, w))
    if first and s.brim_type == "brim_ears" and s.brim_width > 0:
        paths += _brim_ears(region, s, w, sp)
    return paths


def _brim_ears(region, s, w, sp):
    """Brim ears: round pads only at sharp outside corners (they lift first), not all around."""
    from shapely.geometry.polygon import orient
    pads = []
    for poly in _polys(region):
        ring = np.asarray(orient(Polygon(poly.exterior).simplify(0.3), 1.0).exterior.coords)[:-1]
        if len(ring) < 3:
            continue
        prev, nxt = np.roll(ring, 1, 0), np.roll(ring, -1, 0)
        a, b = ring - prev, nxt - ring
        turn = np.degrees(np.arctan2(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0], (a * b).sum(1)))   # + = left (convex)
        inner = 180 - turn
        for k in np.flatnonzero((turn > 0) & (inner < s.brim_ears_max_angle)):
            pads.append(shapely.Point(ring[k]).buffer(s.brim_width, quad_segs=16))
    if not pads:
        return []
    ear = unary_union(pads).difference(region.buffer(s.brim_object_gap))
    return [Path("brim", r.pts, True, w) for r in patterns.concentric(ear, sp, "brim")]
