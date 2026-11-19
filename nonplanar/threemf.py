"""3MF reader that keeps every part separate, including Bambu Studio / Orca project files.

Bambu and Orca store each object in its own file under 3D/Objects/ (the 3MF production
extension). A multi-part object, such as a logo on a base, keeps all its parts in one of those
files and points at each one with <component p:path=... objectid=...>. We follow those
references exactly and return one mesh per part, placed by the build transforms.
"""
import posixpath
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import trimesh

PROD = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path"


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _matrix(text):
    """3MF transform "m00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31 m32" (row vectors) -> 4x4."""
    M = np.eye(4)
    if text:
        v = [float(x) for x in text.split()]
        if len(v) == 12:
            M[:3, :3] = np.array(v[:9]).reshape(3, 3).T
            M[:3, 3] = v[9:12]
    return M


def _unit_scale(root):
    return {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "meter": 1000.0,
            "inch": 25.4, "foot": 304.8}.get(root.get("unit", "millimeter"), 1.0)


def load_3mf_parts(path, use_build=True):
    """Returns [(name, trimesh.Trimesh)], one per part, in millimetres.
    use_build=False ignores the build-item placement (component transforms still apply)."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        cache = {}

        def model(part_path):
            key = part_path.lstrip("/")
            if key not in cache:
                if key not in names:
                    raise ValueError(f"3MF refers to a missing file: {part_path}")
                root = ET.fromstring(z.read(key))
                objs = {}
                for res in root:
                    if _local(res.tag) != "resources":
                        continue
                    for o in res:
                        if _local(o.tag) == "object":
                            objs[o.get("id")] = o
                cache[key] = (root, objs, _unit_scale(root))
            return cache[key]

        main = next((n for n in ("3D/3dmodel.model", "3D/3DModel.model") if n in names), None)
        if main is None:
            main = next((n for n in names if n.lower().endswith(".model") and n.lower().startswith("3d/")), None)
        if main is None:
            raise ValueError("Not a 3MF file (no 3D model inside)")

        out = []

        def walk(file_key, oid, M, name, depth=0):
            if depth > 16:
                raise ValueError("3MF components nest too deep")
            root, objs, scale = model(file_key)
            o = objs.get(oid)
            if o is None:
                raise ValueError(f"3MF object {oid} not found in {file_key}")
            label = o.get("name") or name
            for child in o:
                tag = _local(child.tag)
                if tag == "mesh":
                    verts, faces = [], []
                    for sec in child:
                        st = _local(sec.tag)
                        if st == "vertices":
                            verts = [(float(v.get("x")), float(v.get("y")), float(v.get("z"))) for v in sec]
                        elif st == "triangles":
                            faces = [(int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))) for t in sec]
                    if verts and faces:
                        m = trimesh.Trimesh(np.asarray(verts) * scale, np.asarray(faces), process=False)
                        m.apply_transform(M)
                        out.append((label, m))
                elif tag == "components":
                    for comp in child:
                        ref = comp.get(PROD)
                        target = posixpath.normpath(ref).lstrip("/") if ref else file_key
                        walk(target, comp.get("objectid"), M @ _matrix(comp.get("transform")), label, depth + 1)

        root, objs, _ = model(main)
        build = next((b for b in root if _local(b.tag) == "build"), None)
        items = list(build) if build is not None else []
        for k, item in enumerate(items):
            if _local(item.tag) != "item" or item.get("printable", "1") == "0":
                continue
            ref = item.get(PROD)
            target = posixpath.normpath(ref).lstrip("/") if ref else main
            o = model(target)[1].get(item.get("objectid"))
            walk(target, item.get("objectid"), _matrix(item.get("transform")) if use_build else np.eye(4),
                 (o.get("name") if o is not None else None) or f"Object {k + 1}")
    if not out:
        raise ValueError("The 3MF has no printable objects")
    return out
