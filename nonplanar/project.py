"""Project files: a standard 3MF that also carries the app's settings.

The model parts are written as plain 3MF objects placed on the plate, so Bambu Studio, Orca and
other slicers open the file too. Everything the app needs to restore the session (presets and
their unsaved changes, filament slots and colours, part filaments, colour changes by layer,
placement) is in Metadata/nonplanar_project.json. The parts are stored in the app's own model
coordinates; the build transforms only matter to other programs.
"""
import io
import json
import os
import tempfile
import zipfile

import numpy as np

META = "Metadata/nonplanar_project.json"
FORMAT = 1

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="json" ContentType="application/json"/>
</Types>
"""
_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def _transform(M):
    """4x4 (column vectors) -> 3MF "m00 m01 m02 m10 ... m32" (row vectors)."""
    A = np.asarray(M, float).T
    return " ".join(f"{v:.6f}" for v in A[:, :3].ravel())


def _mesh_xml(out, mesh):
    v = np.asarray(mesh.vertices, float)
    f = np.asarray(mesh.faces, np.int64)
    out.write("   <mesh>\n    <vertices>\n")
    out.write("".join(f'     <vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>\n' for x, y, z in v.tolist()))
    out.write("    </vertices>\n    <triangles>\n")
    out.write("".join(f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>\n' for a, b, c in f.tolist()))
    out.write("    </triangles>\n   </mesh>\n")


def write_project(path, parts, names, placement, meta):
    """parts: meshes in the app's model coordinates; placement: 4x4 that puts them on the plate."""
    buf = io.StringIO()
    buf.write('<?xml version="1.0" encoding="UTF-8"?>\n'
              '<model unit="millimeter" xml:lang="en-US" '
              'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
              ' <metadata name="Application">ArcLayer</metadata>\n <resources>\n')
    for k, (m, name) in enumerate(zip(parts, names)):
        buf.write(f'  <object id="{k + 1}" type="model" name="{_esc(name)}">\n')
        _mesh_xml(buf, m)
        buf.write("  </object>\n")
    buf.write(" </resources>\n <build>\n")
    tf = _transform(placement)
    for k in range(len(parts)):
        buf.write(f'  <item objectid="{k + 1}" transform="{tf}"/>\n')
    buf.write(" </build>\n</model>\n")

    data = {"format": FORMAT, **meta}
    # write next to the target and swap in, so a failed save never destroys the old file
    folder = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(suffix=".3mf", dir=folder)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.writestr("[Content_Types].xml", _CONTENT_TYPES)
            z.writestr("_rels/.rels", _RELS)
            z.writestr("3D/3dmodel.model", buf.getvalue())
            z.writestr(META, json.dumps(data, indent=1))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def read_meta(path):
    """The app's project data in a 3MF, or None for any other 3MF (or a file that isn't one)."""
    try:
        with zipfile.ZipFile(path) as z:
            if META not in z.namelist():
                return None
            return json.loads(z.read(META).decode("utf-8"))
    except (OSError, ValueError, zipfile.BadZipFile):
        return None


def is_sliced_3mf(path):
    """A Bambu/Orca print file (.gcode.3mf): G-code inside, no model to edit."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except (OSError, zipfile.BadZipFile):
        return False
    has_gcode = any(n.lower().endswith(".gcode") for n in names)
    has_model = any(n.lower().endswith(".model") for n in names)
    return has_gcode and not has_model
