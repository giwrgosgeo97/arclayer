"""Nozzle / hotend collision check.

Non-planar layers mean already-printed material can stick up ABOVE the nozzle tip
next to where it is printing. We keep a height map of everything printed so far and,
for every new layer, check that nothing rises higher than the head allows:

    allowed rise at distance d = min(d * nozzle_cone_slope, nozzle_free_height)   (d <= head_radius)

The check is against previous layers (the dominant risk for curved layers).
"""
import numpy as np

CELL = 1.0  # mm
MAX_MARKS = 5000  # collision points sent to the preview


def check(preview, s, log=print):
    allp = np.vstack([pts for lay in preview for _, pts in lay])
    lo = allp[:, :2].min(0) - s.head_radius - 2
    hi = allp[:, :2].max(0) + s.head_radius + 2
    shape = np.ceil((hi - lo) / CELL).astype(int) + 1
    hmap = np.full(shape, -1e3)

    rad = int(np.ceil(s.head_radius / CELL))
    gx, gy = np.mgrid[-rad:rad + 1, -rad:rad + 1] * CELL
    # grid cells are CELL wide, so a cell's true distance can be up to ~0.7*CELL more
    d = np.hypot(gx, gy) + 0.7 * CELL
    allowed = np.minimum(d * s.nozzle_cone_slope, s.nozzle_free_height)
    # cells outside the head footprint can't collide
    under = d <= s.head_radius

    padded = np.full((shape[0] + 2 * rad, shape[1] + 2 * rad), -1e3)   # hmap with a border of "nothing"
    oi, oj = np.nonzero(under)                                         # cells under the head, as flat offsets
    offsets = oi * padded.shape[1] + oj
    drop = allowed[under]
    worst, worst_layer, hits = 0.0, None, 0
    marks = []
    for n, lay in enumerate(preview):
        if not lay:
            continue
        pts = np.vstack([p for _, p in lay])
        ij = np.floor((pts[:, :2] - lo) / CELL).astype(int)
        if n > 0:
            # max over the head's neighbourhood of (printed height - allowed rise), evaluated only
            # at the cells this layer passes through (the whole-map dilation was the slow part)
            cells, inv = np.unique(ij[:, 0] * padded.shape[1] + ij[:, 1], return_inverse=True)
            inv = inv.ravel()
            flat = padded.ravel()                 # (a view)
            reach_c = np.full(len(cells), -1e3)
            for c0 in range(0, len(cells), 1024):
                win = flat.take(cells[c0:c0 + 1024, None] + offsets[None])
                win -= drop
                reach_c[c0:c0 + 1024] = win.max(axis=1)
            over = reach_c[inv] - pts[:, 2]
            bad = over > 0.05
            if bad.any():
                hits += int(bad.sum())
                if len(marks) < MAX_MARKS:      # where the nozzle is when something printed is too close
                    for (x, y, z), o in zip(pts[bad][:MAX_MARKS - len(marks)].tolist(), over[bad].tolist()):
                        marks.append([round(x, 3), round(y, 3), round(z, 3), n, round(o, 3)])
                m = float(over.max())
                if m > worst:
                    worst, worst_layer = m, n
        np.maximum.at(hmap, (ij[:, 0], ij[:, 1]), pts[:, 2])
        padded[rad:-rad, rad:-rad] = hmap

    if hits:
        log(f"WARNING: possible head collision at {hits} points; worst {worst:.2f} mm "
            f"too high at layer {worst_layer}. Lower --angle or check the head settings.")
    else:
        log("Collision check: OK (with the configured head geometry)")
    return {"hits": hits, "worst_mm": worst, "worst_layer": worst_layer, "points": marks}


def safe_angle(s):
    """Steepest cone angle (degrees) the head geometry allows: the cone may rise at most
    nozzle_free_height over the heater block's reach (head_radius)."""
    return float(np.degrees(np.arctan2(s.nozzle_free_height, s.head_radius)))
