"""Space transforms that turn curved layers into flat ones.

The whole slicer works like this:
    1. forward():  bend the model so the curved layers we want become flat (z' = const)
    2. slice the bent model with ordinary flat layers
    3. inverse():  bend the toolpaths back, so flat layers become curved in real space

Every transform here keeps X and Y unchanged and only moves Z, which keeps the
slicing simple (polygons in X/Y are identical in both spaces).

A transform must provide:
    forward(x, y, z)        -> z'
    inverse(x, y, z')       -> z
    grad(x, y, z)           -> (dz'/dx, dz'/dy, dz'/dz)   used for true layer thickness
"""
import numpy as np


def _smoothstep(u):
    u = np.minimum(np.maximum(u, 0.0), 1.0)
    return u * u * (3.0 - 2.0 * u)


def _smoothstep_d(u):
    inside = (u > 0.0) & (u < 1.0)
    return np.where(inside, 6.0 * u * (1.0 - u), 0.0)


class ConicalTransform:
    """Layers shaped like cones around a vertical axis.

    angle > 0: tent-shaped layers (high in the middle). Each layer steps outward,
               so outward overhangs (mushrooms, flared parts) print without supports.
    angle < 0: bowl-shaped layers, for inward overhangs.

    Near the bed the layers are flat and bend into the cone over `ramp_height`,
    so the first layer is a normal flat layer with full bed contact.

    In real space a layer is the surface   z + r*tan(angle)*s(z) = z'
    where r is the distance from the cone axis and s() ramps 0 -> 1 over ramp_height.
    """

    def __init__(self, angle_deg, cx, cy, ramp_height):
        self.angle = float(angle_deg)
        self.t = float(np.tan(np.radians(angle_deg)))
        self.cx, self.cy = float(cx), float(cy)
        self.H = max(float(ramp_height), 1e-6)

    def _r(self, x, y):
        return np.hypot(np.asarray(x) - self.cx, np.asarray(y) - self.cy)

    def forward(self, x, y, z):
        r = self._r(x, y)
        return np.asarray(z) + r * self.t * _smoothstep(np.asarray(z) / self.H)

    def inverse(self, x, y, zp):
        x, y, zp = np.broadcast_arrays(np.asarray(x, float), np.asarray(y, float), np.asarray(zp, float))
        r = self._r(x, y)
        k = r * self.t
        z = np.empty_like(zp)
        # above the ramp: closed form
        above = zp - k >= self.H
        z[above] = (zp - k)[above]
        # below the bed: unchanged
        below = (~above) & (zp <= 0)
        z[below] = zp[below]
        # inside the ramp: f(z) = z + k*s(z/H) is monotonic in [0, H]. Safeguarded Newton:
        # a Newton step when it stays inside the bracket, otherwise bisection. Converges to
        # ~1e-9 mm in a handful of steps (plain bisection needed 48).
        mid = ~(above | below)
        if np.any(mid):
            H = self.H
            km, zm = k[mid], zp[mid]
            lo = np.zeros(len(zm))
            hi = np.full(len(zm), H)
            c = np.minimum(np.maximum(zm - km, 0.0), H)       # start: the above-ramp formula, clamped
            for _ in range(40):
                u = c / H
                f = c + km * (u * u * (3.0 - 2.0 * u)) - zm
                lo = np.where(f < 0, c, lo)
                hi = np.where(f < 0, hi, c)
                d = 1.0 + km * 6.0 * u * (1.0 - u) / H
                step = np.where(np.abs(d) > 1e-12, f / np.where(np.abs(d) > 1e-12, d, 1.0), 0.0)
                nxt = c - step
                bad = (nxt < lo) | (nxt > hi) | (np.abs(d) <= 1e-12)
                nxt = np.where(bad, 0.5 * (lo + hi), nxt)
                done = np.abs(nxt - c) < 1e-10
                c = nxt
                if done.all():
                    break
            z[mid] = c
        return z

    def grad(self, x, y, z):
        x, y, z = (np.asarray(a, float) for a in (x, y, z))
        dx, dy = x - self.cx, y - self.cy
        r = np.hypot(dx, dy)
        safe = np.where(r > 1e-9, r, 1.0)
        s = _smoothstep(z / self.H)
        ds = _smoothstep_d(z / self.H) / self.H
        gx = np.where(r > 1e-9, self.t * s * dx / safe, 0.0)
        gy = np.where(r > 1e-9, self.t * s * dy / safe, 0.0)
        gz = 1.0 + r * self.t * ds
        return gx, gy, gz

    @staticmethod
    def auto_ramp(angle_deg, cx, cy, verts, min_ratio):
        """Smallest ramp height that keeps every layer at least min_ratio thick
        (or at most 1/min_ratio thick for bowl layers) at all model points."""
        t = np.tan(np.radians(angle_deg))
        if abs(t) < 1e-9:
            return 1.0
        r = np.hypot(verts[:, 0] - cx, verts[:, 1] - cy)
        z = verts[:, 2]
        # vertical layer thickness ratio = 1 / (1 + r*t*s'(z)).  s'(z) <= 1.5/H
        if t > 0:
            limit = 1.0 / min_ratio - 1.0          # thinning limit
        else:
            limit = 1.0 - min_ratio                # thickening limit (also keeps it monotonic)
        for H in np.arange(0.5, 400.0, 0.25):
            worst = np.max(np.abs(r * t) * _smoothstep_d(z / H) / H)
            if worst <= limit:
                return float(H)
        return 400.0


class SurfaceTransform:
    """Layers that follow the model's top surface (wavy rims, domes, sculpted tops).

    d(x, y) <= 0 is how far the top surface at (x, y) lies below the highest point Tmax (limited to `depth`
    and to a steepest slope the toolhead allows). The layers are flat up to z0, bend over the next R mm,
    and above that are copies of the top surface:

        z' = z - d(x, y) * s((z - z0) / R)        s: smoothstep 0 -> 1

    so the real surface z = Tmax + d(x, y) becomes the flat bent layer z' = Tmax, and the last layers
    print along the surface instead of in flat steps.
    """

    def __init__(self, grid_x0, grid_y0, cell, d, z0, R, cx, cy):
        from scipy import ndimage  # noqa: F401  (checked here so a missing scipy fails early)
        self.x0, self.y0, self.cell = float(grid_x0), float(grid_y0), float(cell)
        self.d = np.asarray(d, float)
        gy, gx = np.gradient(self.d, self.cell)            # axis 0 = x index, axis 1 = y index
        self.gx, self.gy = gy, gx
        self.z0, self.H = float(z0), max(float(R), 1e-6)
        self.cx, self.cy = float(cx), float(cy)
        self.angle = 0.0
        self.depth = float(-self.d.min()) if self.d.size else 0.0

    def _sample(self, arr, x, y):
        from scipy.ndimage import map_coordinates
        x, y = np.broadcast_arrays(np.asarray(x, float), np.asarray(y, float))
        i = (x.ravel() - self.x0) / self.cell
        j = (y.ravel() - self.y0) / self.cell
        return map_coordinates(arr, [i, j], order=1, mode="nearest").reshape(x.shape)

    def forward(self, x, y, z):
        z = np.asarray(z, float)
        return z - self._sample(self.d, x, y) * _smoothstep((z - self.z0) / self.H)

    def inverse(self, x, y, zp):
        x, y, zp = np.broadcast_arrays(np.asarray(x, float), np.asarray(y, float), np.asarray(zp, float))
        d = self._sample(self.d, x, y)
        z = np.array(zp, float)                              # below the bend: flat
        top = self.z0 + self.H
        above = zp >= top - d
        z[above] = (zp + d)[above]                           # above it: a copy of the top surface
        mid = (~above) & (zp > self.z0)
        if np.any(mid):
            dm, zm, H, z0 = d[mid], zp[mid], self.H, self.z0
            lo, hi = np.full(len(zm), z0), np.full(len(zm), top)
            c = np.clip(zm, z0, top)
            for _ in range(40):                              # monotonic (f' >= 1): safeguarded Newton
                u = (c - z0) / H
                f = c - dm * _smoothstep(u) - zm
                lo = np.where(f < 0, c, lo)
                hi = np.where(f < 0, hi, c)
                fp = 1.0 - dm * _smoothstep_d(u) / H
                nxt = c - f / fp
                nxt = np.where((nxt < lo) | (nxt > hi), 0.5 * (lo + hi), nxt)
                done = np.abs(nxt - c) < 1e-10
                c = nxt
                if done.all():
                    break
            z[mid] = c
        return z

    def grad(self, x, y, z):
        z = np.asarray(z, float)
        u = (z - self.z0) / self.H
        s = _smoothstep(u)
        d = self._sample(self.d, x, y)
        return (-self._sample(self.gx, x, y) * s, -self._sample(self.gy, x, y) * s,
                1.0 - d * _smoothstep_d(u) / self.H)

    @classmethod
    def from_mesh(cls, mesh, depth, max_angle, min_ratio, floor, cell=0.5, log=print):
        """Height map of the top surface, limited to `depth` mm and `max_angle` degrees of slope."""
        from scipy import ndimage
        v = _surface_points(mesh, 0.9 * cell)
        lo = mesh.bounds[0][:2] - 4 * cell
        hi = mesh.bounds[1][:2] + 4 * cell
        shape = np.ceil((hi - lo) / cell).astype(int) + 1
        T = np.full(shape, -np.inf)
        ij = np.clip(np.rint((v[:, :2] - lo) / cell).astype(int), 0, shape - 1)
        np.maximum.at(T, (ij[:, 0], ij[:, 1]), v[:, 2])
        empty = ~np.isfinite(T)
        if empty.all():
            raise ValueError("Empty model")
        idx = ndimage.distance_transform_edt(empty, return_distances=False, return_indices=True)
        T = T[tuple(idx)]                                   # cells off the model: the nearest top height
        T = ndimage.maximum_filter(T, footprint=_disk(2.0 / cell))   # thin walls keep their rim height
        Tmax = float(T.max())
        t = np.tan(np.radians(max(5.0, min(60.0, max_angle))))
        # the most the top layers may bend down, so the bend fits above the first layers
        k = 1.5 / (1.0 / min_ratio - 1.0)                  # ramp length needed per mm of bend
        room = max(0.0, Tmax - floor)
        D = min(depth, room / (1.0 + k))
        d = np.clip(T - Tmax, -D, 0.0)
        # steepest slope the toolhead allows: raise low spots next to high ones (an upper cone envelope)
        for _ in range(int(np.ceil(D / (t * cell))) + 2):
            p = np.pad(d, 1, mode="edge")
            nb = np.max([p[1 + a:p.shape[0] - 1 + a, 1 + b:p.shape[1] - 1 + b] - t * cell * np.hypot(a, b)
                         for a in (-1, 0, 1) for b in (-1, 0, 1)], axis=0)
            new = np.maximum(d, nb)
            if np.allclose(new, d):
                break
            d = new
        d = ndimage.gaussian_filter(d, 0.75 / cell, mode="nearest")
        d = np.minimum(d, 0.0)
        Dd = float(-d.min())
        R = max(1.0, k * Dd)
        z0 = Tmax - Dd - R
        c = (mesh.bounds[0][:2] + mesh.bounds[1][:2]) / 2
        if Dd < 0.05:
            log("Top surface: the top is flat, so the layers stay flat")
        else:
            log(f"Top surface: follows up to {Dd:.1f} mm of height difference, slopes up to {np.degrees(np.arctan(t)):.0f}°; "
                f"layers bend from {max(z0, 0):.1f} mm to {z0 + R:.1f} mm")
        return cls(lo[0], lo[1], cell, d, z0, R, c[0], c[1])


def _surface_points(mesh, max_edge):
    """Points all over the mesh, closer than max_edge: every edge longer than that is halved until none is left.
    The same points trimesh's subdivide_to_size gives, without joining the triangles up again (its slow part)."""
    tri = np.asarray(mesh.vertices, float)[np.asarray(mesh.faces)]
    out = []
    for _ in range(40):
        d = tri[:, [1, 2, 0]] - tri                       # edges v0v1, v1v2, v2v0
        split = (d * d).sum(2) ** 0.5 > max_edge
        count = split.sum(1)
        out.append(tri[count == 0].reshape(-1, 3))
        if not count.any():
            break
        new = []
        q, sp = tri[count == 1], split[count == 1]        # one long edge: turned to be (a, b)
        if len(q):
            j, r = np.argmax(sp, axis=1), np.arange(len(q))
            a, b, c = q[r, j], q[r, (j + 1) % 3], q[r, (j + 2) % 3]
            p = (a + b) / 2
            new += [(a, p, c), (p, b, c)]
        q, sp = tri[count == 2], split[count == 2]        # two: the short edge turned to be (c, a)
        if len(q):
            j, r = (np.argmin(sp, axis=1) + 1) % 3, np.arange(len(q))
            a, b, c = q[r, j], q[r, (j + 1) % 3], q[r, (j + 2) % 3]
            p, o = (a + b) / 2, (b + c) / 2
            new.append((p, b, o))
            aq = ((a - o) ** 2).sum(1) <= ((p - c) ** 2).sum(1)      # the quad, cut along its shorter diagonal
            new += [(a[aq], p[aq], o[aq]), (a[aq], o[aq], c[aq]), (a[~aq], p[~aq], c[~aq]), (p[~aq], o[~aq], c[~aq])]
        q = tri[count == 3]
        if len(q):
            a, b, c = q[:, 0], q[:, 1], q[:, 2]
            ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
            new += [(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)]
        tri = np.concatenate([np.stack(x, axis=1) for x in new])
    else:
        out.append(tri.reshape(-1, 3))
    return np.vstack(out)


def _disk(r):
    r = max(1, int(round(r)))
    a = np.arange(-r, r + 1)
    return np.hypot(*np.meshgrid(a, a)) <= r + 0.01


def make_transform(settings, verts, mesh=None, log=print):
    """Build the transform for a mesh already placed on the bed."""
    if settings.mode == "surface":
        if mesh is None:
            raise ValueError("The top-surface mode needs the model")
        from .collision import safe_angle
        limit = settings.surface_max_angle if settings.surface_max_angle > 0 else safe_angle(settings)
        return SurfaceTransform.from_mesh(mesh, settings.surface_depth, limit, settings.min_layer_ratio,
                                          floor=max(1.0, 3 * settings.first_layer_height), log=log)
    if settings.mode != "conical":
        raise ValueError(f"Unknown mode {settings.mode!r} (available: conical, surface)")
    if settings.cone_center is not None:
        cx, cy = settings.cone_center
    else:
        lo, hi = verts[:, :2].min(0), verts[:, :2].max(0)
        cx, cy = (lo + hi) / 2
    H = settings.ramp_height
    if H is None:
        H = ConicalTransform.auto_ramp(settings.cone_angle, cx, cy, verts, settings.min_layer_ratio)
    return ConicalTransform(settings.cone_angle, cx, cy, H)
