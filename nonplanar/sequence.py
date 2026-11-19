"""Print by object: each part is sliced on its own (its own cone axis / top surface) and printed to the top
before the next one starts.

Order: lowest first, tallest last. The toolhead and gantry can hit objects that are already finished, so
before printing it checks (with the printer's toolhead clearance, from the maker's profile):
  - every two objects are at least the toolhead radius apart (their outlines on the bed), and
  - every object except the last is lower than the gantry rod above the nozzle.
Problems come back as warnings for the preview (with the object numbers); nothing is moved by itself.
"""
import numpy as np
import shapely
from shapely.geometry import MultiPoint


def footprint(mesh):
    """The object's outline on the bed (convex hull of the mesh seen from above)."""
    return MultiPoint(np.asarray(mesh.vertices)[:, :2]).convex_hull


def slice_objects(parts, s, M, slice_model, log=print, progress=lambda f, t: None, part_tools=None, tool_changes=None):
    """parts: meshes in model coordinates; M: the plate placement of the whole model.
    Returns [{"index", "tf", "layers", "outline", "height"}] in print order."""
    tools = list(part_tools or [])
    objs = []
    n = len(parts)
    for k, pm in enumerate(parts):
        def prog(f, t, k=k):
            progress(0.62 * (k + min(1.0, f / 0.62)) / n, f"Object {k + 1}/{n}: {t}")
        log(f"Object {k + 1}/{n}")
        m_k, tf_k, layers_k = slice_model(pm, s, log=log, progress=prog, matrix=M,
                                          part_tools=[tools[k] if k < len(tools) else 0], tool_changes=tool_changes)
        objs.append({"index": k, "tf": tf_k, "layers": layers_k, "outline": footprint(m_k),
                     "height": float(m_k.bounds[1][2]), "bounds": m_k.bounds})
    # lowest first, tallest last (only the last one may be taller than the gantry)
    objs.sort(key=lambda o: (o["height"], o["index"]))
    return objs


def check(objs, s):
    """Collision problems between objects printed one after another: list of {kind, text, objects}."""
    out = []
    r = float(s.extruder_clearance_radius)
    rod = float(s.extruder_clearance_height_to_rod)
    for a in range(len(objs)):
        for b in range(a + 1, len(objs)):
            oa, ob = objs[a], objs[b]
            d = float(shapely.distance(oa["outline"], ob["outline"]))
            if d < r:
                i, j = oa["index"] + 1, ob["index"] + 1
                what = "overlap or touch" if d < 0.05 else f"are only {d:.0f} mm apart"
                out.append({"kind": "too_close", "objects": [i, j], "distance": round(d, 1),
                            "text": f"Objects {i} and {j} {what}: the toolhead reaches {r:.0f} mm around the nozzle, "
                                    f"so it can hit object {i} while printing object {j}. Move them at least "
                                    f"{r - d:.0f} mm further apart, or print by layer."})
    for o in objs[:-1]:
        if o["height"] > rod:
            i = o["index"] + 1
            out.append({"kind": "too_tall", "objects": [i], "height": round(o["height"], 1),
                        "text": f"Object {i} is {o['height']:.0f} mm tall, but the gantry is only {rod:.0f} mm above "
                                f"the nozzle: printing the objects after it, the gantry would hit it. Only the last "
                                f"(tallest) object may be taller than {rod:.0f} mm: print by layer, or lower it."})
    return out
