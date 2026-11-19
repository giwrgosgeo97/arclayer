"""Each printer's own start / end G-code, as its maker's slicer profile writes it.

`printer_gcode.json` holds the start and end G-code of every built-in printer, taken from the
public printer profiles of OrcaSlicer (most brands, AGPL-3.0) and Bambu Studio (Bambu Lab,
AGPL-3.0), with parent profiles merged in. They're written in the slicers' macro language;
`render()` fills them in with this print's values, so a Prusa starts with Prusa's mesh levelling
and purge line, a Klipper printer calls its PRINT_START macro, and so on.

The Bambu A1 / A1 mini files in bambu_gcode/ are the same idea (they came first).
"""
import datetime
import json
import os

from . import macro
from .mmu import TEMP_RANGE

_FILE = os.path.join(os.path.dirname(__file__), "printer_gcode.json")
_DATA = None

# glass / soft temperature per material, for the "temperature_vitrification" checks some starts make
VITRIFICATION = {"PLA": 45, "PETG": 64, "ABS": 90, "ASA": 90, "TPU": 30, "PA": 70, "PC": 110, "PVA": 50, "HIPS": 90}


def table():
    global _DATA
    if _DATA is None:
        try:
            with open(_FILE, encoding="utf-8") as f:
                _DATA = json.load(f)
        except (OSError, ValueError):
            _DATA = {}
    return _DATA


def _text(v):
    return (v[0] if v else "") if isinstance(v, list) else (v or "")


def gcode_for(model):
    """{"start", "end", "change", "source", "printer_model"} for a printer model name, or None."""
    d = table().get(model or "")
    if not d:
        return None
    return {"start": _text(d.get("machine_start_gcode")), "end": _text(d.get("machine_end_gcode")),
            "change": _text(d.get("change_filament_gcode")) or None, "source": d.get("source", ""),
            "printer_model": d.get("printer_model") or model}


def env(tool_settings, filaments, first_tool, bbox=None, max_z=0.0, plate="Textured PEI Plate",
        layers=0, printer_model="", preset_names=None):
    """Values for the start / end G-code, under the names Orca and Bambu Studio use."""
    ts = tool_settings
    n = len(ts)
    s0 = ts[first_tool]
    types = [(f.get("type") or "PLA") for f in filaments]
    hi = [TEMP_RANGE.get(t, (s.nozzle_temp - 20, s.nozzle_temp + 20))[1] for t, s in zip(types, ts)]
    bx, by = float(s0.bed_x), float(s0.bed_y)
    (x0, y0), (x1, y1) = bbox if bbox else ((bx / 2 - 10, by / 2 - 10), (bx / 2 + 10, by / 2 + 10))
    ow = min(float(s0.max_volumetric_speed or 12),
             float(s0.outer_wall_speed) * float(s0.layer_height) * float(s0.outer_wall_line_width))
    now = datetime.datetime.now()
    names = preset_names or {}
    noz_first = [s.nozzle_temp_first for s in ts]
    bed_first = [s.bed_temp_first for s in ts]
    vit = [VITRIFICATION.get(t.split("-")[0], 50) for t in types]
    e = {
        # extruders / filaments
        "initial_extruder": first_tool, "initial_tool": first_tool, "initial_no_support_extruder": first_tool,
        "current_extruder": first_tool, "_index": first_tool, "initial_filament_id": first_tool,
        "initial_no_support_filament_id": first_tool, "initial_nozzle_id": 0, "initial_no_support_hotend": 0,
        "filament_map": [1] * n, "first_filaments": [first_tool], "first_non_support_filaments": [first_tool],
        "is_extruder_used": [True] + [False] * 15, "has_wipe_tower": False, "total_toolchanges": 0,
        "parallel_printheads_count": 1, "is_all_bbl_filament": False, "has_tpu_in_first_layer": "TPU" in types[first_tool],
        # temperatures
        "nozzle_temperature_initial_layer": noz_first, "first_layer_temperature": noz_first,
        "nozzle_temperature": [s.nozzle_temp for s in ts], "temperature": [s.nozzle_temp for s in ts],
        "bed_temperature_initial_layer_single": bed_first[first_tool], "bed_temperature_initial_layer": bed_first,
        "first_layer_bed_temperature": bed_first, "bed_temperature": [s.bed_temp for s in ts],
        "hot_plate_temp_initial_layer": bed_first, "hot_plate_temp": [s.bed_temp for s in ts],
        "chamber_temperature": [0] * n, "overall_chamber_temperature": 0, "idle_temperature": [0] * n,
        "temperature_vitrification": vit, "min_vitrification_temperature": min(vit),
        "flush_temperatures": hi, "nozzle_temperature_range_high": hi,
        # filament
        "filament_type": types, "filament_diameter": [s.filament_diameter for s in ts],
        "filament_max_volumetric_speed": [s.max_volumetric_speed for s in ts],
        "flush_volumetric_speeds": [s.max_volumetric_speed for s in ts],
        "filament_notes": [""] * n, "filament_settings_id": [names.get("filament", "")] * n,
        "pressure_advance": [getattr(s, "pressure_advance", 0.02) for s in ts], "enable_pressure_advance": [False] * n,
        "retraction_length": [s.retract_length for s in ts], "retract_length_toolchange": [2.0] * n,
        "retraction_distances_when_cut": [18.0] * n, "long_retraction_when_ec": [False] * n,
        "retraction_distance_when_ec": [0.0] * n, "fan_max_speed": [s.fan_max_speed for s in ts],
        # machine and plate
        "nozzle_diameter": [float(s0.nozzle_diameter)], "nozzle_diameter_at_nozzle_id": [float(s0.nozzle_diameter)],
        "print_bed_max": [bx, by], "print_bed_min": [0.0, 0.0], "printable_height": float(s0.max_z),
        "max_print_height": float(s0.max_z), "max_print_z": float(s0.max_z), "z_offset": 0.0,
        "curr_bed_type": plate, "plate_name": "", "printer_model": printer_model,
        "printer_preset": names.get("printer", ""), "printer_settings_id": names.get("printer", ""),
        "print_preset": names.get("process", ""), "print_settings_id": names.get("process", ""), "printer_notes": "",
        "first_layer_print_min": [x0, y0], "first_layer_print_max": [x1, y1],
        "first_layer_print_size": [x1 - x0, y1 - y0], "first_layer_center_no_wipe_tower": [(x0 + x1) / 2, (y0 + y1) / 2],
        # print
        "layer_height": float(s0.layer_height), "initial_layer_print_height": float(s0.first_layer_height),
        "first_layer_height": float(s0.first_layer_height), "line_width": float(s0.line_width),
        "layer_z": float(s0.first_layer_height), "layer_num": 0, "max_layer_z": float(max_z),
        "total_layer_count": int(layers), "travel_speed": float(s0.travel_speed),
        "outer_wall_volumetric_speed": ow, "outer_wall_acceleration": float(s0.outer_wall_acceleration),
        "default_acceleration": float(s0.default_acceleration),
        "machine_max_acceleration_travel": [float(s0.travel_acceleration)] * 2,
        "machine_max_acceleration_extruding": [float(s0.default_acceleration)] * 2,
        "machine_max_acceleration_retracting": [5000.0] * 2, "machine_max_jerk_x": [9.0] * 2,
        "spiral_mode": False, "print_sequence": "by layer", "scan_first_layer": False,
        "cooling_filter_enabled": False, "hold_chamber_temp_for_flat_print": False,
        "year": now.year, "month": now.month, "day": now.day, "hour": now.hour, "minute": now.minute,
        "second": now.second, "timestamp": now.strftime("%Y%m%d%H%M%S"),
        "true": True, "false": False,
    }
    return e


def render(text, values):
    """(rendered_text, unknown_names). Never raises on a macro it can't read: it reports it instead."""
    try:
        return macro.render(text, values)
    except macro.MacroError as ex:
        raise ValueError(f"Can't read the printer's start G-code ({ex})") from ex
