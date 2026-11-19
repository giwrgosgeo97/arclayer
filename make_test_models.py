"""Creates test models in ./models:
  overhang_cup.stl - a flared cup whose wall overhangs 56 deg from vertical.
                     Normally needs supports; conical layers print it without.
  t_bridge.stl     - a T: 10 mm pillar with a 40 x 12 mm slab on top. The flat underside
                     can't be printed by any cone angle, so it needs supports.
"""
import os
import numpy as np
import trimesh

os.makedirs("models", exist_ok=True)

# flared solid: radius 8 mm at the bottom, 32 mm at the top, 16 mm tall (~56 deg overhang)
n = 96
h, r0, r1 = 16.0, 8.0, 32.0
ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
bottom = np.column_stack([r0 * np.cos(ang), r0 * np.sin(ang), np.zeros(n)])
top = np.column_stack([r1 * np.cos(ang), r1 * np.sin(ang), np.full(n, h)])
verts = np.vstack([bottom, top, [[0, 0, 0]], [[0, 0, h]]])
cb, ct = 2 * n, 2 * n + 1
faces = []
for i in range(n):
    j = (i + 1) % n
    faces += [[i, j, n + j], [i, n + j, n + i]]   # side
    faces += [[cb, j, i]]                          # bottom cap
    faces += [[ct, n + i, n + j]]                  # top cap
flare = trimesh.Trimesh(verts, faces, process=True)
flare.fix_normals()
flare.export("models/overhang_cup.stl")
print("models/overhang_cup.stl", flare.extents, "watertight:", flare.is_watertight)

# T shape: the pillar and slab share the z=10 face, so they stack into one solid when sliced
pillar = trimesh.creation.box([10, 10, 10]); pillar.apply_translation([0, 0, 5])
slab = trimesh.creation.box([40, 12, 3]); slab.apply_translation([0, 0, 11.5])
tee = trimesh.util.concatenate([pillar, slab])
tee.export("models/t_bridge.stl")
print("models/t_bridge.stl", tee.extents)
