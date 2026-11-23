"""Auto arrange: spread the parts of the model over the plate with a gap between them.

Works on the outlines seen from above (convex hulls, in the orientation and scale the model has on the plate).
Parts that touch or overlap in 3D (a multi-colour assembly) stay together as one group.

Gap: printing by object, the printer's toolhead clearance radius (so nothing hits a finished object);
printing by layer, a few mm plus the brim on both sides.

Packing: on a 1 mm grid, biggest group first; each group goes to the free spot that keeps the whole
arrangement smallest and squarest, near the middle of the plate.
"""
import numpy as np
import shapely
import shapely.affinity
from shapely.geometry import MultiPoint, Polygon, box
from shapely.ops import unary_union

RES = 1.0          # mm per grid cell
EDGE = 3.0         # keep away from the plate edge (mm)


def gap_for(s):
    """The gap between outlines this print needs (mm), and why."""
    brim = float(s.brim_width) if getattr(s, "brim_type", "no_brim") not in ("no_brim", "", None) else 0.0
    if getattr(s, "print_sequence", "by_layer") == "by_object":
        r = float(s.extruder_clearance_radius)
        return max(r, 2 * brim + 2), f"the toolhead clearance ({r:.0f} mm), printing by object"
    g = 6.0 + 2 * brim
    return g, "6 mm" + (f" plus the brim ({brim:g} mm) on both sides" if brim else "")


def groups(parts, L, tol=0.3):
    """Indices of parts that touch or overlap (3D bounding boxes, then the outlines), as groups."""
    n = len(parts)
    pts = [np.asarray(m.vertices) @ L.T for m in parts]
    lo = [p.min(0) for p in pts]
    hi = [p.max(0) for p in pts]
    hull = [MultiPoint(p[:, :2]).convex_hull for p in pts]
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a in range(n):
        for b in range(a + 1, n):
            if np.all(lo[a] <= hi[b] + tol) and np.all(lo[b] <= hi[a] + tol) and \
                    shapely.distance(hull[a], hull[b]) <= tol:
                parent[find(a)] = find(b)
    out = {}
    for k in range(n):
        out.setdefault(find(k), []).append(k)
    return sorted(out.values()), pts


def _raster(poly, x0, y0, w, h):
    """Cells (h x w, origin x0/y0) whose centre lies inside poly."""
    xs = x0 + (np.arange(w) + 0.5) * RES
    ys = y0 + (np.arange(h) + 0.5) * RES
    gx, gy = np.meshgrid(xs, ys)
    return shapely.contains_xy(poly, gx, gy).astype(np.float32)


def pack(outlines, bed_x, bed_y, gap):
    """outlines: shapely polygons (any position). Returns the (dx, dy) to move each one, or None if they don't fit."""
    from scipy.signal import fftconvolve          # (slow to load, so only when it is needed)
    half = RES * 0.75                      # a cell's half diagonal: keeps the real gap at least `gap`
    W, H = int(np.floor((bed_x - 2 * EDGE) / RES)), int(np.floor((bed_y - 2 * EDGE) / RES))
    if W < 1 or H < 1:
        return None
    occ = np.zeros((H, W), np.float32)     # 1 = keep out (placed outlines grown by the gap)
    order = sorted(range(len(outlines)), key=lambda k: -outlines[k].area)
    moves = [None] * len(outlines)
    used = None                             # bounding box of everything placed so far (grid cells)
    cx, cy = W / 2, H / 2
    for k in order:
        p = outlines[k].buffer(half)
        bx0, by0, bx1, by1 = p.bounds
        w, h = int(np.ceil((bx1 - bx0) / RES)), int(np.ceil((by1 - by0) / RES))
        if w > W or h > H:
            return None
        mask = _raster(p, bx0, by0, w, h)
        if not mask.any():
            mask[h // 2, w // 2] = 1
        hit = fftconvolve(occ, mask[::-1, ::-1], mode="valid") if used is not None else np.zeros((H - h + 1, W - w + 1))
        free = hit < 0.5
        if not free.any():
            return None
        jy, jx = np.nonzero(free)          # candidate lower-left cell of the object's box
        if used is None:
            score = np.hypot(jx + w / 2 - cx, jy + h / 2 - cy)
        else:
            ux0, uy0, ux1, uy1 = used
            ex = np.maximum(ux1, jx + w) - np.minimum(ux0, jx)
            ey = np.maximum(uy1, jy + h) - np.minimum(uy0, jy)
            score = ex * ey + 4.0 * (ex + ey) + 0.05 * np.hypot(jx + w / 2 - cx, jy + h / 2 - cy)
        i = int(np.argmin(score))
        x, y = int(jx[i]), int(jy[i])
        used = (x, y, x + w, y + h) if used is None else (min(used[0], x), min(used[1], y),
                                                           max(used[2], x + w), max(used[3], y + h))
        # where its box goes on the plate (mm)
        ox, oy = EDGE + x * RES, EDGE + y * RES
        moves[k] = (ox - bx0, oy - by0)
        placed = shapely.affinity.translate(outlines[k], moves[k][0], moves[k][1]).buffer(gap + half)
        occ = np.maximum(occ, _raster(placed, EDGE, EDGE, W, H))
    # centre the whole arrangement on the plate
    allp = unary_union([shapely.affinity.translate(o, *m) for o, m in zip(outlines, moves)])
    x0, y0, x1, y1 = allp.bounds
    sx, sy = bed_x / 2 - (x0 + x1) / 2, bed_y / 2 - (y0 + y1) / 2
    return [(dx + sx, dy + sy) for dx, dy in moves]


def arrange(parts, L, bed_x, bed_y, gap, min_gap=None):
    """parts: meshes in model coordinates; L: 3x3 orientation/scale of the model on the plate.
    Returns {"moves": [3-vector per part, in model coordinates], "gap", "groups"} or raises ValueError.
    Each group also drops onto the plate (its lowest point at the bed)."""
    grp, pts = groups(parts, L)
    outlines = [unary_union([MultiPoint(pts[k][:, :2]).convex_hull for k in g]).convex_hull for g in grp]
    outlines = [o if isinstance(o, Polygon) else o.buffer(0.5) for o in outlines]
    tries = [gap] + ([g for g in (gap / 2, min_gap) if g is not None and g < gap] if min_gap is not None else [])
    res, used = None, None
    for g in tries:
        res = pack(outlines, bed_x, bed_y, g)
        if res is not None:
            used = g
            break
    if res is None:
        big = max(o.bounds[2] - o.bounds[0] for o in outlines), max(o.bounds[3] - o.bounds[1] for o in outlines)
        raise ValueError(f"The {len(grp)} objects don't fit on the {bed_x:.0f} × {bed_y:.0f} mm plate with "
                         f"{gap:.0f} mm between them (the biggest is {big[0]:.0f} × {big[1]:.0f} mm)")
    Linv = np.linalg.inv(L)
    zmin = min(p[:, 2].min() for p in pts)
    moves = [None] * len(parts)
    for g, (dx, dy) in zip(grp, res):
        gz = min(pts[k][:, 2].min() for k in g)
        d = Linv @ np.array([dx, dy, zmin - gz])
        for k in g:
            moves[k] = d
    return {"moves": moves, "gap": used, "groups": grp}
