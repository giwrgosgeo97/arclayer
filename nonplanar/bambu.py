"""Bambu Lab packaging.

Bambu printers print `.gcode.3mf` files. Instead of guessing Bambu's metadata format,
we take a REAL one as a template: slice any small model for your Bambu printer in Bambu Studio
or OrcaSlicer (same filament and plate you'll use), "Export plate sliced file",
and pass it with --template. We then:
  * reuse its real start G-code (homing, bed levelling, nozzle wipe, purge line)
    and its end G-code,
  * swap our non-planar toolpaths in between,
  * write a new .gcode.3mf with the G-code replaced and its MD5 checksum updated.
"""
import hashlib
import re
import zipfile

START_MARKER = "MACHINE_START_GCODE_END"
END_MARKER = "MACHINE_END_GCODE_START"


def _gcode_name(names):
    cands = [n for n in names if re.fullmatch(r"Metadata/plate_\d+\.gcode", n)]
    if not cands:
        raise ValueError("Template has no Metadata/plate_N.gcode - export it with 'Export plate sliced file'")
    return sorted(cands)[0]


def read_template(path):
    """Return (start_gcode, end_gcode) cut from a Bambu .gcode.3mf or plain .gcode."""
    if str(path).lower().endswith(".3mf"):
        with zipfile.ZipFile(path) as z:
            text = z.read(_gcode_name(z.namelist())).decode("utf-8", errors="replace")
    else:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    lines = text.splitlines()
    si = next((i for i, l in enumerate(lines) if START_MARKER in l), None)
    ei = next((i for i, l in enumerate(lines) if END_MARKER in l), None)
    if si is None or ei is None:
        raise ValueError(f"Template G-code lacks '{START_MARKER}' / '{END_MARKER}' markers "
                         "(it must come from Bambu Studio or OrcaSlicer with a Bambu profile)")
    start = "\n".join(lines[: si + 1])
    end = "\n".join(lines[ei:])
    return start, end


def write_3mf(template_path, gcode_text, out_path, filaments=None):
    """Copy the template .gcode.3mf, replacing its G-code and MD5.
    filaments: [{"id": 1-based slot, "type", "color", "used_m", "used_g"}] for slice_info.config,
    which the printer uses to map the file's filaments to AMS slots."""
    data = gcode_text.encode("utf-8")
    with zipfile.ZipFile(template_path) as zin:
        gname = _gcode_name(zin.namelist())
        md5name = gname + ".md5"
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == gname:
                    zout.writestr(item, data)
                elif item.filename == md5name:
                    old = zin.read(item).decode().strip()
                    digest = hashlib.md5(data).hexdigest()
                    zout.writestr(item, digest.upper() if old.isupper() else digest)
                elif item.filename == "Metadata/slice_info.config" and filaments:
                    xml = zin.read(item).decode("utf-8", errors="replace")
                    zout.writestr(item, _slice_info(xml, filaments).encode("utf-8"))
                else:
                    zout.writestr(item, zin.read(item))


def read_change_gcode(path):
    """The template's filament-change macro (unrendered), or None.
    .gcode.3mf: Metadata/project_settings.config (JSON). Plain .gcode: the config block at the end."""
    import json
    try:
        if str(path).lower().endswith(".3mf"):
            with zipfile.ZipFile(path) as z:
                if "Metadata/project_settings.config" in z.namelist():
                    cfg = json.loads(z.read("Metadata/project_settings.config").decode("utf-8", errors="replace"))
                    v = cfg.get("change_filament_gcode")
                    if isinstance(v, list):
                        v = v[0] if v else None
                    return v or None
                text = z.read(_gcode_name(z.namelist())).decode("utf-8", errors="replace")
        else:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return None
    m = re.search(r"^; change_filament_gcode = (.*)$", text, re.M)
    if not m:
        return None
    return m.group(1).replace("\n", "\n").strip('"') or None


def template_nozzle(path):
    """Nozzle diameter (mm) a Bambu .gcode.3mf template was sliced for, or None."""
    import json
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "Metadata/project_settings.config" in names:
                v = json.loads(z.read("Metadata/project_settings.config").decode("utf-8", "replace")).get("nozzle_diameter")
                v = v[0] if isinstance(v, list) and v else v
                return float(v) if v else None
            if "Metadata/slice_info.config" in names:
                m = re.search(r'nozzle_diameters?"\s+value="([\d.]+)', z.read("Metadata/slice_info.config").decode("utf-8", "replace"))
                return float(m.group(1)) if m else None
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return None
    return None


def _slice_info(xml, filaments):
    """Replace the <filament .../> list in Metadata/slice_info.config with ours."""
    old = {m.group(1): m.group(0) for m in re.finditer(r'<filament\s+id="(\d+)"[^>]*/>', xml)}
    lines = []
    for f in filaments:
        attrs = {"id": str(f["id"]), "type": f.get("type", "PLA"), "color": f.get("color", "#FFFFFF").upper(),
                 "used_m": f"{f.get('used_m', 0):.2f}", "used_g": f"{f.get('used_g', 0):.2f}"}
        prev = old.get(attrs["id"])
        if prev:
            m = re.search(r'tray_info_idx="([^"]*)"', prev)
            if m:
                attrs = {"id": attrs["id"], "tray_info_idx": m.group(1), **{k: v for k, v in attrs.items() if k != "id"}}
        lines.append("<filament " + " ".join(f'{k}="{v}"' for k, v in attrs.items()) + " />")
    if not old:
        return xml.replace("</plate>", "    " + "\n    ".join(lines) + "\n  </plate>", 1)
    first = True

    def sub(m):
        nonlocal first
        if first:
            first = False
            return "\n    ".join(lines)
        return ""
    xml = re.sub(r'<filament\s+id="\d+"[^>]*/>', sub, xml)
    return re.sub(r"\n\s*\n", "\n", xml)


# ------------------------------------------------------------------ built in: no template needed
# Bambu Studio's own machine G-code for the printers it ships (resources/profiles/BBL/machine,
# AGPL-3.0, (c) Bambu Lab), rendered with our values. Used when no template is set.
import os as _os
import struct as _struct
import zlib as _zlib

_GDIR = _os.path.join(_os.path.dirname(__file__), "bambu_gcode")
BUILTIN = {"Bambu Lab A1": "A1", "Bambu Lab A1 mini": "A1_mini"}
# printer_model_id in slice_info.config: the printer checks that the file was made for it
MODEL_ID = {"Bambu Lab A1": "N2S", "Bambu Lab A1 mini": "N1", "Bambu Lab P1P": "C11", "Bambu Lab P1S": "C12",
            "Bambu Lab X1 Carbon": "BL-P001", "Bambu Lab X1": "BL-P002", "Bambu Lab X1E": "C13",
            "Bambu Lab P2S": "N7", "Bambu Lab H2D": "O1D"}
# Bambu's generic filament ids (tray_info_idx), used to match AMS trays
TRAY_IDX = {"PLA": "GFL99", "PETG": "GFG99", "ABS": "GFB99", "ASA": "GFB98", "TPU": "GFU99", "PA": "GFN99",
            "PC": "GFC99", "PVA": "GFS99", "HIPS": "GFS98", "PLA-CF": "GFL98", "PETG-CF": "GFG98", "PA-CF": "GFN98"}
END_MARK = "; NPS_BUILTIN_END"


def builtin(model):
    """{"start", "end", "change"} raw macro text for a Bambu printer model, or None."""
    key = BUILTIN.get(model or "")
    if not key:
        return None
    out = {}
    for part in ("start", "end", "change_filament"):
        with open(_os.path.join(_GDIR, f"{key}_{part}.gcode"), encoding="utf-8") as f:
            out["change" if part == "change_filament" else part] = f.read()
    return out


def machine_env(tool_settings, filaments, first_tool, bbox=None, max_z=0.0, bed_type="Textured PEI Plate"):
    """Variables for Bambu's start / end G-code."""
    from .mmu import TEMP_RANGE
    ts = tool_settings
    types = [(f.get("type") or "PLA") for f in filaments]
    hi = [TEMP_RANGE.get(t, (s.nozzle_temp - 20, s.nozzle_temp + 20))[1] for t, s in zip(types, ts)]
    s0 = ts[first_tool]
    (x0, y0), (x1, y1) = bbox if bbox else ((s0.bed_x / 2 - 10, s0.bed_y / 2 - 10), (s0.bed_x / 2 + 10, s0.bed_y / 2 + 10))
    w0 = float(s0.outer_wall_speed) * float(s0.layer_height) * float(s0.nozzle_diameter) * 1.05
    ow = min(float(s0.max_volumetric_speed or 12), w0 or 12)     # mm³/s of the outer wall
    return {
        "initial_extruder": first_tool, "initial_no_support_extruder": first_tool, "initial_tool": first_tool,
        "_index": first_tool, "nozzle_diameter": float(s0.nozzle_diameter),
        "nozzle_temperature": [s.nozzle_temp for s in ts],
        "nozzle_temperature_initial_layer": [s.nozzle_temp_first for s in ts],
        "bed_temperature_initial_layer_single": s0.bed_temp_first,
        "bed_temperature": [s.bed_temp for s in ts], "bed_temperature_initial_layer": [s.bed_temp_first for s in ts],
        "filament_type": types, "flush_temperatures": hi,
        "flush_volumetric_speeds": [s.max_volumetric_speed for s in ts],
        "outer_wall_volumetric_speed": ow, "curr_bed_type": bed_type,
        "first_layer_print_min": [x0, y0], "first_layer_print_size": [x1 - x0, y1 - y0],
        "first_layer_center_no_wipe_tower": [(x0 + x1) / 2, (y0 + y1) / 2],
        "max_layer_z": max_z, "spiral_mode": False, "print_sequence": "by layer",
        "true": True, "false": False,
    }


def _png(rgb):
    """Encode an (h, w, 3) RGB or (h, w, 4) RGBA uint8 array as PNG."""
    h, w, c = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))
    chunk = lambda t, d: _struct.pack(">I", len(d)) + t + d + _struct.pack(">I", _zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", _struct.pack(">IIBBBBB", w, h, 8, 6 if c == 4 else 2, 0, 0, 0))
            + chunk(b"IDAT", _zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def thumbnail(preview, preview_tools, colors, size=256):
    """PNG of the toolpaths seen from the front-top, coloured by filament, for the printer's screen.
    preview: per layer [(kind, (n, 3) points)], preview_tools: per layer [tool]."""
    import numpy as np
    segs, cols = [], []
    for layer, tools in zip(preview, preview_tools):
        for (kind, p), t in zip(layer, tools):
            p = np.asarray(p, float)
            if len(p) >= 2:
                segs.append(p)
                cols.append(t)
    img = np.zeros((size, size, 4), np.uint8)       # transparent background, like Bambu Studio's pictures
    if not segs:
        return _png(img)
    allp = np.vstack(segs)
    # oblique view: x across, y and z up the image
    u = lambda p: p[:, 0]
    v = lambda p: p[:, 1] * 0.5 + p[:, 2]
    lo = np.array([u(allp).min(), v(allp).min()])
    hi = np.array([u(allp).max(), v(allp).max()])
    # room around the object: the A1 shows the picture as it is
    scale = (size * 0.6) / max(hi - lo).max() if (hi - lo).max() > 0 else 1.0
    off = (size - (hi - lo) * scale) / 2
    zmin, zmax = allp[:, 2].min(), max(allp[:, 2].max(), allp[:, 2].min() + 1e-6)
    rgb = []
    for c in colors:
        h = (c or "#808080").lstrip("#")[:6].ljust(6, "8")
        rgb.append(np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float))
    order = np.argsort([s[:, 2].mean() + s[:, 1].mean() * -0.01 for s in segs])   # back and low first
    for i in order:
        p, t = segs[i], cols[i]
        d = np.linalg.norm(np.diff(p, axis=0), axis=1)
        n = np.maximum(1, (d * scale).astype(int) + 1)
        q = np.vstack([np.linspace(p[k], p[k + 1], n[k], endpoint=False) for k in range(len(p) - 1)] + [p[-1:]])
        x = ((u(q) - lo[0]) * scale + off[0]).astype(int).clip(0, size - 1)
        y = (size - 1 - ((v(q) - lo[1]) * scale + off[1])).astype(int).clip(0, size - 1)
        base = rgb[t] if t < len(rgb) else np.array([128.0, 128.0, 128.0])
        shade = 0.6 + 0.4 * (q[:, 2] - zmin) / (zmax - zmin)
        img[y, x, :3] = (base[None, :] * shade[:, None]).clip(0, 255).astype(np.uint8)
        img[y, x, 3] = 255
    return _png(img)


def quick_start(text):
    """Shorter start: no start-up tune and no motor resonance check (the printer keeps its last result)."""
    out, skip = [], False
    for line in text.split("\n"):
        if "mech mode fast check start" in line:
            skip = True
            out.append("; quick start: mech mode fast check skipped (uses the printer's saved values)")
            continue
        if skip:
            if "mech mode fast check end" in line:
                skip = False
            continue
        if line.startswith("M1006 ") and not line.startswith("M1006 W"):
            continue                              # the start-up tune
        out.append(line)
    return "\n".join(out)


def gcode_thumbnail(text, png, w=300, h=300):
    """Put a PNG preview into the G-code header, the way Bambu Studio does (the printer shows it)."""
    import base64
    b = base64.b64encode(png).decode()
    lines = ["; THUMBNAIL_BLOCK_START", f"; thumbnail begin {w}x{h} {len(b)}"]
    lines += ["; " + b[i:i + 78] for i in range(0, len(b), 78)]
    lines += ["; thumbnail end", "; THUMBNAIL_BLOCK_END", ""]
    block = "\n".join(lines) + "\n"
    i = text.find("; HEADER_BLOCK_END")
    if i >= 0:
        j = text.find("\n", i) + 1
        return text[:j] + "\n" + block + text[j:]
    return block + text


def build_3mf(gcode_text, out_path, model, nozzle, filaments, stats, thumbs=None):
    """A Bambu .gcode.3mf made from scratch (no template).
    thumbs: {"plate": 512 px PNG, "small": 128 px PNG}.
    No picture goes inside the G-code: Bambu Studio and Orca never put one there for Bambu printers,
    and the A1 draws one it finds at full size, over the time and progress on its screen."""
    thumbs = thumbs or {}
    data = gcode_text.encode("utf-8")
    md5 = hashlib.md5(data).hexdigest().upper()
    fil = []
    for f in filaments or [{"id": 1, "type": "PLA", "color": "#FFFFFF", "used_m": 0, "used_g": 0}]:
        t = f.get("type", "PLA")
        fil.append(f'    <filament id="{f["id"]}" tray_info_idx="{TRAY_IDX.get(t, "GFL99")}" type="{t}" '
                   f'color="{f.get("color", "#FFFFFF").upper()}" used_m="{f.get("used_m", 0):.2f}" used_g="{f.get("used_g", 0):.2f}" />')
    slice_info = f'''<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Type" value="slicer"/>
    <header_item key="X-BBL-Client-Version" value="02.00.00.00"/>
  </header>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="printer_model_id" value="{MODEL_ID.get(model, "")}"/>
    <metadata key="nozzle_diameters" value="{nozzle:g}"/>
    <metadata key="timelapse_type" value="0"/>
    <metadata key="prediction" value="{int(stats.get("time_s", 0))}"/>
    <metadata key="weight" value="{float(stats.get("filament_g", 0)):.2f}"/>
    <metadata key="outside" value="false"/>
    <metadata key="support_used" value="false"/>
    <metadata key="label_object_enabled" value="false"/>
{chr(10).join(fil)}
  </plate>
</config>
'''
    types = '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="png" ContentType="image/png"/>
 <Default Extension="gcode" ContentType="text/x.gcode"/>
</Types>
'''
    rels = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
 <Relationship Target="/Metadata/plate_1.png" Id="rel-2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>
 <Relationship Target="/Metadata/plate_1.png" Id="rel-4" Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-middle"/>
 <Relationship Target="/Metadata/plate_1_small.png" Id="rel-5" Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-small"/>
</Relationships>
'''
    model_settings = '''<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="plater_name" value=""/>
    <metadata key="locked" value="false"/>
    <metadata key="gcode_file" value="Metadata/plate_1.gcode"/>
    <metadata key="thumbnail_file" value="Metadata/plate_1.png"/>
    <metadata key="thumbnail_no_light_file" value="Metadata/plate_1.png"/>
    <metadata key="top_file" value="Metadata/top_1.png"/>
    <metadata key="pick_file" value="Metadata/pick_1.png"/>
  </plate>
</config>
'''
    model_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">
 <metadata name="Application">ArcLayer</metadata>
 <metadata name="BambuStudio:3mfVersion">1</metadata>
 <resources/>
 <build/>
</model>
'''
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model_xml)
        z.writestr("Metadata/plate_1.gcode", data)
        z.writestr("Metadata/plate_1.gcode.md5", md5)
        z.writestr("Metadata/slice_info.config", slice_info)
        z.writestr("Metadata/model_settings.config", model_settings)
        if thumbs.get("plate"):
            z.writestr("Metadata/plate_1.png", thumbs["plate"])
            z.writestr("Metadata/top_1.png", thumbs["plate"])
            z.writestr("Metadata/pick_1.png", thumbs["plate"])
            z.writestr("Metadata/plate_1_small.png", thumbs.get("small") or thumbs["plate"])
