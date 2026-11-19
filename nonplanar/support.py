"""Supports (normal columns or trees), computed in the bent space.

In the bent space every layer is flat, so an overhang is simply the part of a layer
that the layer below doesn't hold up. Because the layers are bent back into cones
afterwards, this test is made against the real curved layer underneath: overhangs the
cone already takes care of get no support, and only what's left over does.

Support columns are vertical in the bent space. The transform never moves X/Y, so
they stay vertical on the real printer too and stand on the bed (the first layers
are flat thanks to the ramp).
"""
import math

import numpy as np

import shapely
import shapely.ops
from shapely.geometry import MultiPolygon
from shapely.ops import unary_union

EMPTY = MultiPolygon()
GRID = 1e-4   # snap results to a 0.1 micron grid: avoids GEOS "non-noded intersection" errors


def _diff(a, b):
    return shapely.difference(a, b, grid_size=GRID)


def _inter(a, b):
    return shapely.intersection(a, b, grid_size=GRID)


def _union(geoms):
    return shapely.union_all(geoms, grid_size=GRID)


def _clean(geom, r):
    """Drop slivers narrower than 2*r (morphological opening)."""
    if geom.is_empty:
        return EMPTY
    return geom.buffer(-r, join_style=2).buffer(r, join_style=2)


def overhangs(regions, thick, s):
    """Per layer: the area of the model the layer below doesn't support."""
    a = max(1.0, min(89.0, s.support_threshold_angle))
    out = [EMPTY]
    for i in range(1, len(regions)):
        reach = thick[i] / math.tan(math.radians(a))     # how far a layer may stick out unsupported
        below = regions[i - 1]
        if regions[i].is_empty:
            out.append(EMPTY)
            continue
        bad = _diff(regions[i], below.buffer(reach))
        if bad.is_empty:
            out.append(EMPTY)
            continue
        # grow it back over the tolerated strip, so the overhang strips of consecutive
        # layers join up into one area (a sloped underside gives a thin strip per layer)
        full = _inter(bad.buffer(reach + 0.01), _diff(regions[i], below))
        out.append(_clean(full, 0.05))
    return out


def generate(regions, thick, s, log=print, enforce=None, block=None):
    """Returns a list of (base_area, interface_area) shapely geometries, one per layer.
    enforce / block: painted areas per layer (or None). Enforced areas get support even where the
    slope is under the threshold or support is switched off; blocked areas never get any."""
    with np.errstate(all="ignore"):     # GEOS warns on degenerate slivers; harmless
        return _generate(regions, thick, s, log, enforce, block)


def _valid(g):
    """An area the line patterns can use: valid, on the grid, polygons only."""
    g = _poly(g)
    if g.is_empty or g.is_valid:
        return g
    return _poly(shapely.make_valid(shapely.set_precision(g, GRID)))


def _poly(g):
    """Only the area parts of a geometry (set operations can leave lines and points behind)."""
    if g.is_empty:
        return EMPTY
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    polys = [p for p in getattr(g, "geoms", []) if p.geom_type in ("Polygon", "MultiPolygon") and not p.is_empty]
    return _union(polys) if polys else EMPTY


def _painted(regions, over, enforce, block):
    out = [_poly(o) for o in over]
    for i in range(1, len(regions)):
        if enforce is not None and not enforce[i].is_empty and not regions[i].is_empty:
            hang = _poly(_diff(regions[i], regions[i - 1])) if not regions[i - 1].is_empty else _poly(regions[i])
            forced = _poly(_inter(hang, _poly(enforce[i])))
            if not forced.is_empty:
                out[i] = _poly(_union([out[i], forced]))
        if block is not None and not block[i].is_empty and not out[i].is_empty:
            out[i] = _poly(_diff(out[i], _poly(block[i])))
    return out


TREE_STYLES = ("tree_slim", "tree_strong", "tree_hybrid", "organic")


def _generate(regions, thick, s, log, enforce=None, block=None):
    n = len(regions)
    over = overhangs(regions, thick, s) if s.enable_support else [EMPTY] * n
    if enforce is not None or block is not None:
        over = _painted(regions, over, enforce, block)
    if all(o.is_empty for o in over):
        log("Support: no overhang needs support")
        return [(EMPTY, EMPTY)] * n

    h = s.layer_height
    gap = max(0, int(round(s.support_top_z_distance / h)))     # empty layers between support and part
    shadow = _shadow(regions) if s.support_on_build_plate_only else None
    tree = s.support_type == "tree"
    if tree:
        support, contact, info = _tree(regions, over, s, gap, shadow)
    else:
        support = _columns(regions, over, s, gap, shadow, grid=s.support_style == "grid")
        contact, info = over, ""
    result = _finish(regions, support, contact, s, gap)

    vol = sum(b.area + f.area for b, f in result)
    used = sum(1 for b, f in result if not (b.is_empty and f.is_empty))
    kind = {"tree_slim": "tree (slim)", "tree_strong": "tree (strong)", "tree_hybrid": "tree (hybrid)",
            "organic": "tree (organic)"}.get(s.support_style, "tree") if tree else (
        "normal (grid)" if s.support_style == "grid" else "normal")
    log(f"Support, {kind}: {used} layers, footprint {vol / max(used, 1):.0f} mm² per layer on average"
        + (" (build plate only)" if s.support_on_build_plate_only else "") + info)
    return result


def _shadow(regions):
    """The part's footprint from the bed up to each layer (for build-plate-only support)."""
    out, acc = [], EMPTY
    for r in regions:
        if not r.is_empty:
            # simplified as it grows: the union of many curved layers otherwise gets huge (and slow)
            acc = shapely.set_precision(shapely.union(acc, r.simplify(0.05)), 0.01).simplify(0.05)
        out.append(acc)
    return out


def _keep_out(regions, i, s, shadow, over=None):
    """Where support may not go on layer i: the part (plus the XY gap), the layers of part just
    below (bottom Z distance, where support stands on the part) and, for build-plate-only, the
    part's whole footprint.

    Curved layers: a flat underside cut by tilted layers shows up as a thin ring of overhang in each
    layer. The XY gap is for walls beside the support; next to such a ring the support is UNDER the part
    (the Z gap keeps it free), so the ring only keeps a small gap. Without this the support stopped one or
    two layers short of the underside and the part drooped there."""
    xy = s.support_xy_distance
    parts = []
    if not regions[i].is_empty:
        hang = over[i] if over is not None and i < len(over) else EMPTY
        if hang is None or hang.is_empty:
            parts.append(regions[i].buffer(xy))
        else:
            walls = _diff(regions[i], hang.buffer(0.02))
            if not walls.is_empty:
                parts.append(walls.buffer(xy))
            parts.append(hang.buffer(min(xy, 0.5 * s.support_line_width * 0.5)))
    gb = max(0, int(round(s.support_bottom_z_distance / s.layer_height)))
    for k in range(1, gb + 1):
        if i - k >= 0 and not regions[i - k].is_empty:
            parts.append(regions[i - k])
    if shadow is not None and not shadow[i].is_empty:
        parts.append(shadow[i].buffer(xy))
    return _union(parts) if parts else EMPTY


def _columns(regions, over, s, gap, shadow, grid=False):
    """Normal support: vertical columns under each overhang, down to the bed or the part."""
    n = len(regions)
    support = [EMPTY] * n
    carry = EMPTY
    for i in range(n - 1, -1, -1):
        j = i + 1 + gap                       # the layer whose overhang this layer starts holding up
        if j < n and not over[j].is_empty:
            o = over[j].buffer(s.support_expansion, join_style=2) if s.support_expansion > 0 else over[j]
            if grid:                          # grid style: whole rectangles, sturdier and easier to remove
                o = _union([p.envelope for p in getattr(o, "geoms", [o]) if not p.is_empty])
            carry = _union([carry, o])
        if carry.is_empty:
            continue
        keep_out = _keep_out(regions, i, s, shadow, over)
        area = _diff(carry, keep_out) if not keep_out.is_empty else carry
        area = _clean(area, 0.5 * s.support_line_width).simplify(0.05)
        if s.support_remove_small_overhang and not area.is_empty:
            # columns thinner than ~2 lines can't stand on their own: drop them
            keep = [p for p in getattr(area, "geoms", [area]) if p.area >= 4 * s.support_line_width ** 2]
            area = _union(keep) if keep else EMPTY
        support[i] = area
        # where the part is in the way, the column ends (it stands on the part)
        carry = area
    return support


def _finish(regions, support, contact, s, gap):
    """Split each layer's support into base and the dense interface: under the part (top
    interface) and, if set, where the support stands on the part (bottom interface)."""
    n = len(regions)
    n_if = max(0, int(s.support_interface_layers))
    n_bot = max(0, int(s.support_bottom_interface_layers))
    gb = max(0, int(round(s.support_bottom_z_distance / s.layer_height)))
    result = []
    for i in range(n):
        area = support[i]
        if area.is_empty:
            result.append((EMPTY, EMPTY))
            continue
        tops = [contact[i + 1 + gap + k] for k in range(n_if) if i + 1 + gap + k < n]
        tops = [t for t in tops if not t.is_empty]
        interface = _inter(area, _union(tops)) if tops else EMPTY
        if n_bot:
            lows = [regions[i - 1 - gb - k] for k in range(n_bot) if i - 1 - gb - k >= 0]
            lows = [r for r in lows if not r.is_empty]
            if lows:
                interface = _union([interface, _inter(area, _union(lows))])
        interface = _clean(_poly(interface), 0.25 * s.support_line_width) if not interface.is_empty else EMPTY
        base = _diff(area, interface) if not interface.is_empty else area
        result.append((_valid(base), _valid(interface)))
    return result


def _grid_points(area, step):
    """Points on a global grid inside `area`; at least one per separate piece."""
    minx, miny, maxx, maxy = area.bounds
    xs = np.arange(math.floor(minx / step) * step + step / 2, maxx, step)
    ys = np.arange(math.floor(miny / step) * step + step / 2, maxy, step)
    pts = []
    if len(xs) and len(ys):
        X, Y = np.meshgrid(xs, ys)
        X, Y = X.ravel(), Y.ravel()
        inside = shapely.contains_xy(area, X, Y)
        pts = list(zip(X[inside], Y[inside]))
    for p in getattr(area, "geoms", [area]):
        if p.is_empty or p.area < 0.05:
            continue
        if not pts or not np.any(shapely.contains_xy(p, np.array([q[0] for q in pts]), np.array([q[1] for q in pts]))):
            c = p.representative_point()
            pts.append((c.x, c.y))
    return pts


def _tree(regions, over, s, gap, shadow):
    """Tree support, worked out in the bent space (every layer flat).

    Each overhang gets branch tips on a grid; going down layer by layer the branches lean
    towards each other (at most the branch angle), merge, get thicker, and steer around the
    part. A branch that reaches the bed is kept; one that runs into the part stands on it
    (or, with build-plate-only, is dropped). Hybrid uses normal columns under big flat
    overhangs. Returns (support area per layer, contact areas for the interface, log text)."""
    n = len(regions)
    h = s.layer_height
    style = s.support_style if s.support_style in TREE_STYLES else "tree_slim"
    move = h * math.tan(math.radians(max(1.0, min(80.0, s.tree_support_branch_angle))))
    grow = h * math.tan(math.radians(max(0.0, min(30.0, s.tree_support_branch_diameter_angle))))
    r_tip = max(0.2, s.tree_support_tip_diameter / 2)
    r_max = max(r_tip, s.tree_support_branch_diameter / 2)
    dist = max(1.0, s.tree_support_branch_distance)
    if style == "tree_strong":
        r_max *= 1.4
        dist *= 0.75
    merge_reach = dist * (3.0 if style == "organic" else 2.0)
    momentum = 0.6 if style == "organic" else 0.0
    xy = s.support_xy_distance

    big = [EMPTY] * n                          # hybrid: overhangs that get normal columns
    tips = [EMPTY] * n
    for j in range(n):
        o = over[j]
        if o.is_empty:
            continue
        if style == "tree_hybrid":
            large = [p for p in getattr(o, "geoms", [o]) if p.area > max(4 * dist * dist, 25.0)
                     and not p.buffer(-dist / 2).is_empty]
            if large:
                big[j] = _union(large)
                o = _diff(o, big[j])
        tips[j] = o

    X = np.zeros((0, 2))                       # live nodes: position, radius, velocity, id
    R = np.zeros(0)
    V = np.zeros((0, 2))
    ids = np.zeros(0, dtype=int)
    hist = {}                                  # id -> [(layer, x, y, r)]
    merged_into = {}                           # id -> id it merged into
    ended = {}                                 # id -> "bed" | "part"
    next_id = 0
    for i in range(n - 1, -1, -1):
        j = i + 1 + gap
        if j < n and not tips[j].is_empty:
            new = _grid_points(tips[j], dist)
            if len(X) and new:
                P = np.asarray(new)
                d = np.sqrt(((P[:, None, :] - X[None, :, :]) ** 2).sum(-1)).min(1)
                new = [tuple(p) for p, dd in zip(P, d) if dd > dist * 0.5]
            if new:
                k = len(new)
                X = np.vstack([X, np.asarray(new)])
                R = np.concatenate([R, np.full(k, r_tip)])
                V = np.vstack([V, np.zeros((k, 2))])
                ids = np.concatenate([ids, np.arange(next_id, next_id + k)])
                for q in range(next_id, next_id + k):
                    hist[q] = []
                next_id += k
        if not len(X):
            continue
        # 1. lean towards the neighbours (so branches meet and merge)
        D = np.sqrt(((X[:, None, :] - X[None, :, :]) ** 2).sum(-1))
        near = D < merge_reach
        target = (near[:, :, None] * X[None, :, :]).sum(1) / near.sum(1)[:, None]
        step = target - X
        if momentum:
            step = momentum * V + (1 - momentum) * step
        ln = np.linalg.norm(step, axis=1)
        step *= (np.minimum(ln, move) / np.maximum(ln, 1e-9))[:, None]
        Xn = X + step
        # 2. keep clear of the part on this layer
        alive = np.ones(len(X), bool)
        if not regions[i].is_empty:
            avoid = regions[i].buffer(xy)
            d = shapely.distance(avoid, shapely.points(Xn))
            for q in np.flatnonzero(d < R):
                p = shapely.Point(Xn[q])
                edge = shapely.ops.nearest_points(avoid.exterior if avoid.geom_type == "Polygon" else avoid.boundary, p)[0]
                e = np.array([edge.x, edge.y])
                out = (e - Xn[q]) if avoid.contains(p) else (Xn[q] - e)
                nrm = np.linalg.norm(out)
                out = out / nrm if nrm > 1e-9 else np.array([1.0, 0.0])
                if regions[i].contains(shapely.Point(X[q])):
                    alive[q] = False           # the part is right under the branch: it stands on it
                    ended[int(ids[q])] = "part"
                else:                          # beside the part: steer around it
                    Xn[q] = e + out * (R[q] + 0.05)
        V = Xn - X
        X, R, V, ids = Xn[alive], R[alive], V[alive], ids[alive]
        # 3. merge branches that touch
        if len(X) > 1:
            keep = np.ones(len(X), bool)
            D = np.sqrt(((X[:, None, :] - X[None, :, :]) ** 2).sum(-1))
            order = np.argsort(-R)
            for a in order:
                if not keep[a]:
                    continue
                close = np.flatnonzero(keep & (D[a] < np.maximum(np.maximum(R, R[a]) * 0.9, move)))
                close = close[close != a]
                if len(close):
                    grp = np.concatenate([[a], close])
                    w = R[grp] ** 2
                    X[a] = (X[grp] * w[:, None]).sum(0) / w.sum()
                    R[a] = min(r_max, max(R[grp]) + 0.25 * (np.sqrt((R[grp] ** 2).sum()) - max(R[grp])))
                    for c in close:
                        merged_into[int(ids[c])] = int(ids[a])
                    keep[close] = False
            X, R, V, ids = X[keep], R[keep], V[keep], ids[keep]
        # 4. thicker going down
        R = np.minimum(r_max, R + grow)
        for q in range(len(X)):
            hist[int(ids[q])].append((i, X[q, 0], X[q, 1], R[q]))
    for q in ids:
        ended[int(q)] = "bed"

    def fate(q):
        seen = 0
        while q in merged_into and seen < 100000:
            q = merged_into[q]
            seen += 1
        return ended.get(q, "bed")

    ok = {q for q in hist if fate(q) == "bed" or (fate(q) == "part" and shadow is None)}
    per = [[] for _ in range(n)]
    for q in ok:
        for (i, x, y, r) in hist[q]:
            per[i].append((x, y, r))
    support = [EMPTY] * n
    normal = _columns(regions, big, s, gap, shadow) if style == "tree_hybrid" else None
    for i in range(n):
        parts = []
        if per[i]:
            c = np.asarray(per[i])
            parts.append(shapely.union_all(shapely.buffer(shapely.points(c[:, :2]), c[:, 2], quad_segs=6)))
        # the contact layers under each overhang: the whole overhang area, so the part sits on a flat top
        for k in range(max(0, int(s.support_interface_layers))):
            jj = i + 1 + gap + k
            if jj < n and not tips[jj].is_empty:
                parts.append(tips[jj])
        if normal is not None and not normal[i].is_empty:
            parts.append(normal[i])
        if not parts:
            continue
        area = _union(parts)
        keep_out = _keep_out(regions, i, s, shadow, over)
        area = _diff(area, keep_out) if not keep_out.is_empty else area
        support[i] = _clean(_poly(area), 0.25 * s.support_line_width)
    branches = len(ok)
    return support, over, f", {branches} branches"
