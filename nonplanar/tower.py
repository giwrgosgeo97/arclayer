"""Prime tower: a small square block, printed next to the model in flat layers, where the nozzle purges the
old colour after each filament change (for printers that have no purge chute, or as a short prime).

The tower is planned before printing: which layers change filament, how much each change needs to purge, how
big the tower must be and where it fits on the bed. While printing, GcodeWriter calls print_band() right after
each change (and once at the start of layers without a change, to keep the tower growing).

Travel to and from the tower always goes up above everything printed so far first: the curved layers of the
part can be higher than the tower, and the tower can be higher than the curved surface near it.
"""
import math

import numpy as np

from . import mmu


class PrimeTower:
    def __init__(self, s, printed, first_tool, filaments, full_flush, log=print):
        self.s = s
        self.log = log
        self.enabled = False
        self.lw = lw = max(0.3, s.line_width)
        self.W = max(15.0, float(s.prime_tower_width))
        # which layers change filament, and how much each change purges here
        tool = first_tool
        self.plan = []                                 # per layer: [(old, new, mm3)]
        for L in printed:
            ch = []
            for p in L.paths:
                if p.tool != tool:
                    vol = mmu.flush_volume(filaments[tool].get("color"), filaments[p.tool].get("color"), s.flush_multiplier) \
                        if full_flush else 0.0
                    ch.append((tool, p.tool, max(float(s.prime_volume), vol)))
                    tool = p.tool
            self.plan.append(ch)
        changing = [n for n, ch in enumerate(self.plan) if ch]
        if not changing:
            return
        self.last = changing[-1]                       # the tower stops after the last change
        h = s.layer_height
        inner = self.W - 2 * lw
        rows_needed = max(sum(math.ceil(v / (inner * lw * h)) for _, _, v in ch) for ch in self.plan)
        self.rows = max(int(math.ceil(10.0 / lw)), rows_needed)
        max_rows = int(60.0 / lw)
        self.scale = 1.0
        if self.rows > max_rows:
            self.scale = max_rows / self.rows
            self.rows = max_rows
            log(f"Prime tower: limited to 60 mm deep; each change purges {self.scale:.0%} of what it needs "
                "(raise it in Process → Multimaterial if colours mix)")
        self.D = self.rows * lw + 2 * lw
        place = self._place(printed)
        if place is None:
            log("WARNING: no room on the bed for the prime tower next to the model: printed without it")
            return
        self.x0, self.y0 = place
        self.enabled = True
        self.cursor = 0                                # next free row in the current layer
        self.layer = -1
        self.z_prev = 0.0                              # top of the tower so far
        self.zt = self.ht = None                       # this layer's tower height and layer thickness
        self.done_layer = set()
        purge = sum(v for ch in self.plan for _, _, v in ch)
        log(f"Prime tower: {self.W:.0f} × {self.D:.0f} mm at X{self.x0:.0f} Y{self.y0:.0f}, layers 1-{self.last + 1}, "
            f"{len(changing)} layers with changes, purging about {purge * self.scale / 1000:.1f} cm³")

    # ------------------------------------------------------------------ where it goes
    def _place(self, printed):
        s = self.s
        xs, ys = [], []
        for L in printed[:3] + printed[3::max(1, len(printed) // 20)]:
            for p in L.paths:
                xs.append(p.pts[:, 0]); ys.append(p.pts[:, 1])
        xs, ys = np.concatenate(xs), np.concatenate(ys)
        bx0, bx1, by0, by1 = xs.min(), xs.max(), ys.min(), ys.max()
        gap = 8.0
        if s.prime_tower_x >= 0 and s.prime_tower_y >= 0:
            x0, y0 = float(s.prime_tower_x), float(s.prime_tower_y)
            if x0 + self.W > s.bed_x or y0 + self.D > s.bed_y:
                self.log("WARNING: the prime tower position is off the bed: placed automatically instead")
            else:
                return x0, y0
        m = 8.0                                        # away from the bed edges and clips
        cands = []
        for x0 in (m, s.bed_x - m - self.W, bx1 + gap, bx0 - gap - self.W):
            for y0 in (s.bed_y - m - self.D, m, by1 + gap, by0 - gap - self.D):
                if x0 < m - 1e-6 or y0 < m - 1e-6 or x0 + self.W > s.bed_x - m + 1e-6 or y0 + self.D > s.bed_y - m + 1e-6:
                    continue
                clear = x0 + self.W + gap <= bx0 or x0 >= bx1 + gap or y0 + self.D + gap <= by0 or y0 >= by1 + gap
                if not clear:
                    continue
                cx, cy = x0 + self.W / 2, y0 + self.D / 2
                # close to the model (short trips), back of the bed preferred
                d = math.hypot(cx - (bx0 + bx1) / 2, cy - (by0 + by1) / 2)
                cands.append((d - 0.05 * cy, x0, y0))
        if not cands:
            return None
        cands.sort()
        return cands[0][1], cands[0][2]

    # ------------------------------------------------------------------ printing
    def wants(self, n):
        return self.enabled and n <= self.last and self.zt is not None

    def begin_layer(self, n, zmin, h, nozzle):
        """The tower follows the LOWEST real point of the part's layer: on curved layers the part can be lower in
        places than its layer number says, and a tower taller than the part could hit the toolhead.
        A layer the part hasn't really risen by is skipped (unless it changes filament)."""
        self.zt = self.ht = None
        if not self.enabled or n > self.last:
            return
        lo, hi = 0.5 * h, min(0.75 * nozzle, 1.0 * h)
        if n == 0:
            z = max(zmin, 0.08)
        else:
            step = zmin - self.z_prev
            if step < lo:
                if not self.plan[n]:
                    return                             # wait: the part is not higher yet
                step = lo                              # a change here: purge just above (at most half a layer ahead)
            z = self.z_prev + min(step, hi)
        self.ht = z - self.z_prev if n else z
        self.zt = self.z_prev = z

    def layer_start(self, w, n, z, h):
        """At the start of a layer with no filament change: keep the tower growing (sparse, layer 1 solid)."""
        if not self.wants(n) or self.plan[n]:
            return
        self._print(w, n, self.zt, self.ht, purge=0.0, last=True)

    def after_change(self, w, n, z, h, k):
        """Right after the k-th change of layer n: purge into the tower."""
        if not self.wants(n) or k >= len(self.plan[n]):
            return
        self._print(w, n, self.zt, self.ht, purge=self.plan[n][k][2] * self.scale, last=(k == len(self.plan[n]) - 1))

    def _print(self, w, n, z, h, purge, last):
        s, lw = w.s, self.lw
        if self.layer != n:
            self.layer, self.cursor = n, 0
        inner = self.W - 2 * lw
        x0, y0 = self.x0 + lw / 2, self.y0 + lw / 2
        x1, y1 = self.x0 + self.W - lw / 2, self.y0 + self.D - lw / 2
        polys = []
        if self.cursor == 0:                                        # the outline, once per layer
            polys.append(np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0 + lw * 0.5]]))
        rows = []
        dense = min(self.rows - self.cursor, int(math.ceil(purge / (inner * lw * h)))) if purge > 0 else 0
        rows += list(range(self.cursor, self.cursor + dense))
        self.cursor += dense
        if last and self.cursor < self.rows:                        # fill the rest: solid on layer 1, sparse above
            step = 1 if n == 0 else (2 if self.plan[min(n + 1, self.last)] else 6)   # denser under a purge layer
            rows += list(range(self.cursor, self.rows, step))
            self.cursor = self.rows
        if rows:
            zz, pts = [], []
            for i, r in enumerate(rows):
                y = y0 + lw * (r + 1)
                if y > y1 - lw * 0.5:
                    break
                a, b = (x0 + lw, x1 - lw) if i % 2 == 0 else (x1 - lw, x0 + lw)
                pts += [[a, y], [b, y]]
            if len(pts) >= 2:
                polys.append(np.array(pts))
        if not polys:
            return
        # get there from above everything printed so far
        w.retract()
        safe = max(w.max_z, z) + max(1.0, s.travel_lift)
        start = polys[0][0]
        if w.pos is not None and w.pos[2] < safe:
            w.emit(f"G1 Z{safe:.3f} F1200")
            w.pos = np.array([w.pos[0], w.pos[1], safe])
        elif w.pos is None:
            w.emit(f"G1 Z{max(safe, w.safe_z or 0):.3f} F1200")
        w.set_accel(s.travel_acceleration)
        w.emit(f"G1 X{start[0]:.3f} Y{start[1]:.3f} F{s.travel_speed * 60:.0f}")
        w.emit(f"G1 Z{z:.3f} F600")
        if w.pos is not None:
            w.time += float(np.hypot(*(start - w.pos[:2]))) / max(s.travel_speed, 1)
        w.pos = np.array([start[0], start[1], z])
        w.safe_z = None
        w.unretract()
        w.emit("; FEATURE: Prime tower")
        w.set_accel(s.inner_wall_acceleration)
        speed = s.first_layer_speed if n == 0 else s.prime_tower_speed
        area = h * (lw - h * (1 - math.pi / 4))
        if s.max_volumetric_speed > 0:
            speed = min(speed, s.max_volumetric_speed / area)
        t0 = w.time
        for k, q in enumerate(polys):
            p3 = np.column_stack([q, np.full(len(q), z)])
            if w.pos is not None and np.hypot(*(p3[0, :2] - w.pos[:2])) > 1e-3:   # next piece: a short hop inside the tower
                w._move(tuple(p3[0]), s.travel_speed)
            seg = np.linalg.norm(np.diff(p3, axis=0), axis=1)
            e = s.flow * s.print_flow_ratio * area * seg / w.fil_area
            if n == 0:
                e = e * s.first_layer_flow_ratio
            w._extrude_moves(p3, speed, e)
            w.e_by_tool[w.tool or 0] += float(e.sum())
            w.preview[-1].append(("prime_tower", p3))
            w.preview_tools[-1].append(w.tool or 0)
            w.preview_speeds[-1].append(float(speed))
            w.preview_attrs[-1].append((float(lw), float(speed * area), 100.0 * (w.fan or 0) / 255,
                                        float(w.temp or s.nozzle_temp), float(w.accel or 0), float(w.pa)))
            w.preview_thick[-1].append(np.full(len(p3), h, np.float32))
        w.time_by_kind["prime_tower"] = w.time_by_kind.get("prime_tower", 0.0) + (w.time - t0)
        w.last_pts = None
        # leave upwards: the next travel starts above everything (see GcodeWriter.travel)
        w.retract()
        w.safe_z = max(w.max_z, z) + max(1.0, s.travel_lift)
        w.pos = None
