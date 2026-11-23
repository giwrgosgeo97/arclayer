"""Printer / filament / process presets, Orca-style.

The schema below drives the whole settings UI: each category has tabs, each tab has
groups of fields. A field's `key` is a nonplanar.Settings attribute unless marked
`extra` (stored in the profile but used by the app, e.g. connection details).
Presets are saved as JSON under profiles/<category>/<name>.json.
"""
import json
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import re

from nonplanar.config import Settings
from nonplanar.mmu import DEFAULT_CHANGE_GCODE as DEFAULT_CHANGE

from . import filaments as filaments_mod, printers as printers_mod
from .paths import PROFILES as ROOT
CATEGORIES = ("printer", "filament", "process")


def F(key, label, type="number", unit="", min=None, max=None, step=None, options=None, tip="", extra=False, adv=False,
      suggest=None):
    """One setting. options: list of values or (value, label) pairs. adv: shown only in Advanced mode.
    suggest: common values offered in a drop-down list on a number field."""
    d = {"key": key, "label": label, "type": type, "unit": unit, "tip": tip, "extra": extra, "adv": adv}
    if suggest is not None: d["suggest"] = list(suggest)
    if min is not None: d["min"] = min
    if max is not None: d["max"] = max
    if step is not None: d["step"] = step
    if options is not None: d["options"] = [list(o) if isinstance(o, tuple) else o for o in options]
    return d


def MM(key, label, lo=0.0, hi=5.0, step=0.01, **kw):
    return F(key, label, unit="mm", min=lo, max=hi, step=step, **kw)


def SPD(key, label, lo=1, hi=500, **kw):
    return F(key, label, unit="mm/s", min=lo, max=hi, step=1, **kw)


def ACC(key, label, **kw):
    return F(key, label, unit="mm/s²", min=0, max=50000, step=100, **kw)


NOZZLES = [0.2, 0.4, 0.6, 0.8]
LAYER_HEIGHTS = [0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20, 0.24, 0.28, 0.30, 0.32, 0.36, 0.40, 0.42, 0.48, 0.56]

FIL = [(0, "Default"), (1, "Filament 1"), (2, "Filament 2"), (3, "Filament 3"), (4, "Filament 4")]

SURFACE = [("monotonic", "Monotonic"), ("monotonicline", "Monotonic line"), ("rectilinear", "Rectilinear"),
           ("alignedrectilinear", "Aligned Rectilinear"), ("concentric", "Concentric"),
           ("hilbertcurve", "Hilbert Curve"), ("archimedeanchords", "Archimedean Chords")]
SPARSE = [("rectilinear", "Rectilinear"), ("alignedrectilinear", "Aligned Rectilinear"), ("line", "Line"),
          ("grid", "Grid"), ("triangles", "Triangles"), ("tri-hexagon", "Tri-hexagon"), ("stars", "Stars"),
          ("cubic", "Cubic"), ("crosshatch", "Cross Hatch"), ("gyroid", "Gyroid"), ("honeycomb", "Honeycomb"),
          ("3dhoneycomb", "3D Honeycomb"), ("lightning", "Lightning"), ("hilbertcurve", "Hilbert Curve"),
          ("archimedeanchords", "Archimedean Chords"), ("concentric", "Concentric")]
SUPPORT_STYLES = [("default", "Default (snug)"), ("grid", "Grid"), ("tree_slim", "Tree Slim"),
                  ("tree_strong", "Tree Strong"), ("tree_hybrid", "Tree Hybrid"), ("organic", "Organic")]

SCHEMA = {
    "printer": [
        {"tab": "Basic information", "groups": [
            {"name": "Printer", "fields": [
                F("model", "Printer model", "text", extra=True),
                F("bed_x", "Bed width (X)", unit="mm", min=50, max=1000),
                F("bed_y", "Bed depth (Y)", unit="mm", min=50, max=1000),
                F("max_z", "Max print height", unit="mm", min=50, max=1000),
                F("nozzle_diameter", "Nozzle diameter", "select", "mm", options=NOZZLES),
            ]},
            {"name": "Speed", "fields": [
                F("speed_factor", "Speed limit", unit="%", min=10, max=100, step=5,
                  tip="Caps the process speeds at this share of Bambu's (outer wall 200, inner wall 300, infill "
                      "270 mm/s). 100% for Bambu and fast Klipper printers, about 70% for a Prusa MK4, about 35% for "
                      "an Ender-3 class printer. Slower process speeds are never raised."),
            ]},
            {"name": "G-code", "fields": [
                F("gcode_flavor", "G-code flavor", "select",
                  options=[("marlin", "Marlin"), ("klipper", "Klipper"), ("reprapfirmware", "RepRapFirmware"),
                           ("bambu", "Bambu Lab")],
                  tip="Decides how jerk is sent (M205 or SET_VELOCITY_LIMIT)."),
            ]},
        ]},
        {"tab": "Extruder", "groups": [
            {"name": "Retraction", "fields": [
                MM("retract_length", "Length", 0, 10, 0.05),
                MM("travel_lift", "Z hop when retracting", 0, 5, 0.05,
                   tip="On non-planar layers the hop follows the curved layer surface."),
                SPD("retract_speed", "Retraction speed", 1, 150),
                SPD("deretraction_speed", "Deretraction speed", 0, 150, tip="0 = same as retraction speed."),
                MM("retract_min_travel", "Travel distance threshold", 0, 50, 0.1,
                   tip="Only retract when the travel is longer than this."),
                F("retract_when_changing_layer", "Retract when changing layer", "checkbox"),
                MM("retract_restart_extra", "Extra length on restart", -2, 5, 0.01, adv=True,
                   tip="Pushes a little more (or less) filament back after a retraction."),
                F("wipe", "Wipe while retracting", "checkbox",
                  tip="The nozzle moves back along the line it just printed while it retracts: fewer blobs and strings."),
                MM("wipe_distance", "Wipe distance", 0, 10, 0.1),
                MM("retract_lift_above", "Z hop only above", 0, 1000, 0.1, adv=True, tip="0 = everywhere."),
                MM("retract_lift_below", "Z hop only below", 0, 1000, 0.1, adv=True, tip="0 = everywhere."),
            ]},
            {"name": "Layer height limits", "fields": [
                MM("min_layer_height", "Min layer height", 0.01, 1, 0.01, tip="The slicer warns below this."),
                MM("max_layer_height", "Max layer height", 0, 2, 0.01, tip="0 = 75% of the nozzle diameter."),
            ]},
        ]},
        {"tab": "Toolhead clearance", "groups": [
            {"name": "Used by the non-planar collision check: measure your hotend", "fields": [
                F("nozzle_free_height", "Nozzle tip to heater block / sock", unit="mm", min=0.5, max=30, step=0.1,
                  tip="How far the nozzle tip sticks out below the heater block or silicone sock."),
                F("head_radius", "Heater block / sock reach", unit="mm", min=2, max=60, step=0.5,
                  tip="Horizontal distance from the nozzle centre to the edge of the block or sock."),
                F("nozzle_cone_slope", "Nozzle cone steepness", min=0.2, max=5, step=0.05,
                  tip="Rise of the brass nozzle cone per mm sideways (about 1.2 for most nozzles)."),
            ]},
            {"name": "Print by object: the whole toolhead", "fields": [
                F("extruder_clearance_radius", "Toolhead radius", unit="mm", min=10, max=200, step=1,
                  tip="How far the toolhead (fan duct, cutter, cables) reaches around the nozzle. Printing one object "
                      "after another, objects closer than this can be hit. From the printer maker's profile."),
                F("extruder_clearance_height_to_rod", "Height to the gantry", unit="mm", min=5, max=400, step=1,
                  tip="From the nozzle tip up to the X gantry / rod. Printing by object, every object except the "
                      "last must be lower than this, or the gantry hits it."),
            ]},
        ]},
        {"tab": "Motion ability", "groups": [
            {"name": "Machine limits", "fields": [
                F("emit_machine_limits", "Write the limits to the G-code", "checkbox",
                  tip="Sends M201 / M203 / M204 / M205 at the start. Leave off on Bambu printers: they use their own."),
                SPD("machine_max_speed_x", "Max speed X", 1, 2000), SPD("machine_max_speed_y", "Max speed Y", 1, 2000),
                SPD("machine_max_speed_z", "Max speed Z", 1, 200), SPD("machine_max_speed_e", "Max speed E", 1, 200),
                ACC("machine_max_acceleration_x", "Max acceleration X"), ACC("machine_max_acceleration_y", "Max acceleration Y"),
                ACC("machine_max_acceleration_z", "Max acceleration Z"), ACC("machine_max_acceleration_e", "Max acceleration E"),
                ACC("machine_max_acceleration_extruding", "Max acceleration when extruding"),
                ACC("machine_max_acceleration_retracting", "Max acceleration when retracting"),
                ACC("machine_max_acceleration_travel", "Max acceleration when travelling"),
                F("machine_max_jerk_x", "Max jerk X", unit="mm/s", min=0, max=100, step=0.5),
                F("machine_max_jerk_y", "Max jerk Y", unit="mm/s", min=0, max=100, step=0.5),
            ]},
        ]},
        {"tab": "Machine G-code", "groups": [
            {"name": "Layer change", "fields": [
                F("before_layer_change_gcode", "Before layer change G-code", "code",
                  tip="Placeholders: {layer_num} {layer_z} {max_layer_z}"),
                F("layer_change_gcode", "Layer change G-code", "code",
                  tip="Placeholders: {layer_num} {layer_z} {max_layer_z}"),
            ]},
            {"name": "Bambu template (Bambu printers: needed to print over the network)", "fields": [
                F("template_3mf", "Template .gcode.3mf", "file", extra=True,
                  tip="A plate sliced for this printer in Bambu Studio or Orca (File > Export > Export plate sliced file). "
                      "Its real start/end G-code is reused and the output is packed as a printable .gcode.3mf."),
            ]},
            {"name": "Custom G-code (used only without a template)", "fields": [
                F("start_gcode", "Machine start G-code", "code", extra=True,
                  tip="Placeholders: {nozzle_temp} {bed_temp} {nozzle_temp_first} {bed_temp_first}"),
                F("end_gcode", "Machine end G-code", "code", extra=True),
            ]},
            {"name": "Filament change (AMS lite)", "fields": [
                F("change_filament_gcode", "Change filament G-code", "code", extra=True,
                  tip="Used for colour changes only when the Bambu template has none: a template's own change "
                      "G-code (with the printer's own cutting and flushing) always wins. Placeholders as in Bambu Studio: "
                      "[next_extruder] [previous_extruder] {max_layer_z} [new_filament_temp] {flush_length_1} … "
                      "This default switches filament but doesn't flush the old colour."),
            ]},
        ]},
        {"tab": "Connection", "groups": [
            {"name": "How the app talks to this printer", "fields": [
                F("host_type", "Connection", "select", extra=True,
                  options=[("bambu", "Bambu Lab (LAN)"), ("moonraker", "Klipper (Moonraker: Fluidd / Mainsail, Creality K1 / K2 / Hi…)"),
                           ("octoprint", "OctoPrint"), ("prusalink", "PrusaLink (Prusa MK4 / CORE One / MINI / XL)")],
                  tip="Bambu: fill in the Bambu fields below. Others: the printer's address, and the API key or password."),
            ]},
            {"name": "Klipper / OctoPrint / PrusaLink", "fields": [
                F("host", "Printer address", "text", extra=True,
                  tip="The address you open in the browser, e.g. 192.168.1.40 (Klipper: Moonraker's port 7125 is added "
                      "if you leave it out; OctoPrint: e.g. octopi.local)."),
                F("api_key", "API key", "password", extra=True,
                  tip="OctoPrint: Settings > API. Klipper: usually none. PrusaLink on older firmware."),
                F("host_password", "PrusaLink password", "password", extra=True,
                  tip="Prusa printer screen: Settings > Network > PrusaLink (user name: maker)."),
            ]},
            {"name": "Bambu LAN connection", "fields": [
                F("ip", "Printer IP address", "text", extra=True, tip="Printer screen: Settings > Network."),
                F("serial", "Serial number", "text", extra=True, tip="Printer screen: Settings > Device."),
                F("access_code", "Access code", "password", extra=True,
                  tip="Printer screen: Settings > LAN Only / LAN access code. Stored only on this computer."),
            ]},
        ]},
    ],
    "filament": [
        {"tab": "Filament", "groups": [
            {"name": "Basic information", "fields": [
                F("type", "Type", "select", options=filaments_mod.TYPES, extra=True),
                F("vendor", "Vendor", "text", extra=True),
                F("color", "Colour", "color", extra=True),
                MM("filament_diameter", "Diameter", 1, 3),
                F("filament_density", "Density", unit="g/cm³", min=0.5, max=3, step=0.01),
                F("flow", "Flow ratio", min=0.5, max=1.5, step=0.01),
                F("enable_pressure_advance", "Enable pressure advance", "checkbox"),
                F("pressure_advance", "Pressure advance", min=0, max=2, step=0.001,
                  tip="Sent as M900 K. Leave off to use the printer's own calibration."),
                F("filament_shrink", "Shrinkage (XY)", unit="%", min=90, max=110, step=0.1,
                  tip="Measured size ÷ designed size. Below 100% the part is printed a bit bigger to make up for it."),
                F("filament_cost", "Price", unit="per kg", min=0, max=1000, step=0.5,
                  tip="Shown with the slicing summary."),
            ]},
            {"name": "Print temperature", "fields": [
                F("nozzle_temp_first", "Nozzle: initial layer", unit="°C", min=150, max=350),
                F("nozzle_temp", "Nozzle: other layers", unit="°C", min=150, max=350),
                F("bed_temp_first", "Bed: initial layer", unit="°C", min=0, max=120),
                F("bed_temp", "Bed: other layers", unit="°C", min=0, max=120),
            ]},
            {"name": "Recommended nozzle temperature", "fields": [
                F("nozzle_temperature_range_low", "Min", unit="°C", min=0, max=400, tip="The slicer warns outside this range."),
                F("nozzle_temperature_range_high", "Max", unit="°C", min=0, max=400),
            ]},
            {"name": "Bed temperature per plate (0 = the bed temperature above)", "fields": [
                F("textured_plate_temp_initial_layer", "Textured PEI: initial layer", unit="°C", min=0, max=120),
                F("textured_plate_temp", "Textured PEI: other layers", unit="°C", min=0, max=120),
                F("cool_plate_temp_initial_layer", "Cool Plate: initial layer", unit="°C", min=0, max=120),
                F("cool_plate_temp", "Cool Plate: other layers", unit="°C", min=0, max=120),
                F("supertack_plate_temp_initial_layer", "Cool Plate SuperTack: initial layer", unit="°C", min=0, max=120),
                F("supertack_plate_temp", "Cool Plate SuperTack: other layers", unit="°C", min=0, max=120),
                F("eng_plate_temp_initial_layer", "Engineering Plate: initial layer", unit="°C", min=0, max=120),
                F("eng_plate_temp", "Engineering Plate: other layers", unit="°C", min=0, max=120),
                F("hot_plate_temp_initial_layer", "High Temp Plate: initial layer", unit="°C", min=0, max=120),
                F("hot_plate_temp", "High Temp Plate: other layers", unit="°C", min=0, max=120),
            ]},
            {"name": "Chamber", "fields": [
                F("chamber_temperature", "Chamber temperature", unit="°C", min=0, max=80,
                  tip="Sent as M141 for printers with a heated chamber. 0 = don't set."),
            ]},
            {"name": "Volumetric speed limitation", "fields": [
                F("max_volumetric_speed", "Max volumetric speed", unit="mm³/s", min=1, max=60, step=0.5),
            ]},
        ]},
        {"tab": "Cooling", "groups": [
            {"name": "Cooling for specific layer", "fields": [
                F("close_fan_first_layers", "No cooling for the first", unit="layers", min=0, max=20, step=1),
                F("full_fan_speed_layer", "Full fan speed at layer", min=0, max=100, step=1, adv=True,
                  tip="The fan ramps up from the first cooled layer to this layer. 0 = full speed straight away."),
            ]},
            {"name": "Part cooling fan", "fields": [
                F("fan_min_speed", "Min fan speed threshold", unit="%", min=0, max=100, step=1),
                F("fan_cooling_layer_time", "… for layers longer than", unit="s", min=0, max=1000, step=1,
                  tip="Layers that take longer than this get the min fan speed; faster layers get more, up to max."),
                F("fan_max_speed", "Max fan speed threshold", unit="%", min=0, max=100, step=1),
                F("reduce_fan_stop_start_freq", "Keep fan always on", "checkbox",
                  tip="Long layers keep the min fan speed instead of turning the fan off."),
                F("slow_down_layer_time", "… for layers shorter than", unit="s", min=0, max=100, step=0.5,
                  tip="Layers faster than this run the fan at max (and slow down, if enabled below)."),
                F("overhang_fan_speed", "Fan speed for overhangs and bridges", unit="%", min=0, max=100, step=1),
                F("overhang_fan_threshold", "Overhang cooling activation threshold", "select",
                  options=[(0.0, "0%"), (0.25, "25%"), (0.5, "50%"), (0.75, "75%")],
                  tip="Overhang walls hanging out more than this share of the line width get the overhang fan."),
            ]},
            {"name": "Slow down for layer cooling", "fields": [
                F("slow_down_for_layer_cooling", "Slow printing down for better layer cooling", "checkbox"),
                SPD("slow_down_min_speed", "Min print speed", 1, 100),
                F("dont_slow_down_outer_wall", "Don't slow down outer walls", "checkbox",
                  tip="Cleaner outer surface: only the rest of the layer slows down for cooling."),
            ]},
            {"name": "Auxiliary part cooling fan", "fields": [
                F("additional_cooling_fan_speed", "Fan speed", unit="%", min=0, max=100, step=1,
                  tip="Side fan of the P1S / X1 / H2D (M106 P2). 0 = off."),
            ]},
        ]},
        {"tab": "Setting overrides", "groups": [
            {"name": "Retraction (-1 = the printer's value)", "fields": [
                MM("filament_retraction_length", "Length", -1, 10, 0.05),
                MM("filament_z_hop", "Z hop when retracting", -1, 5, 0.05),
                F("filament_retraction_speed", "Retraction speed", unit="mm/s", min=-1, max=150, step=1),
                F("filament_deretraction_speed", "Deretraction speed", unit="mm/s", min=-1, max=150, step=1),
            ]},
        ]},
        {"tab": "Advanced", "groups": [
            {"name": "Filament G-code", "fields": [
                F("filament_start_gcode", "Filament start G-code", "code",
                  tip="Runs after the machine start G-code. Placeholders: {nozzle_temp} {bed_temp}"),
                F("filament_end_gcode", "Filament end G-code", "code", tip="Runs before the machine end G-code."),
            ]},
        ]},
    ],
    "process": [
        {"tab": "Quality", "groups": [
            {"name": "Layer height", "fields": [
                MM("layer_height", "Layer height", 0.04, 0.6, 0.02, suggest=LAYER_HEIGHTS,
                   tip="Up to about 70% of the nozzle diameter (0.28 mm for a 0.4 mm nozzle)."),
                MM("first_layer_height", "Initial layer height", 0.1, 0.6, 0.02, suggest=LAYER_HEIGHTS),
                F("compatible_nozzle", "For nozzle", "select", "mm", options=NOZZLES, extra=True,
                  tip="The process list shows only presets for the printer's nozzle size."),
            ]},
            {"name": "Line width", "fields": [
                MM("line_width", "Default", 0.2, 1.2),
                MM("first_layer_line_width", "Initial layer", 0.2, 1.2),
                MM("outer_wall_line_width", "Outer wall", 0.2, 1.2),
                MM("inner_wall_line_width", "Inner wall", 0.2, 1.2),
                MM("top_surface_line_width", "Top surface", 0.2, 1.2),
                MM("sparse_infill_line_width", "Sparse infill", 0.2, 1.2),
                MM("solid_infill_line_width", "Internal solid infill", 0.2, 1.2),
                MM("support_line_width", "Support", 0.2, 1.2),
            ]},
            {"name": "Seam", "fields": [
                F("seam_position", "Seam position", "select",
                  options=[("aligned", "Aligned"), ("nearest", "Nearest"), ("back", "Back"), ("random", "Random")]),
                F("seam_gap", "Seam gap", unit="% of nozzle", min=0, max=100, step=1, adv=True,
                  tip="Loops stop this short of where they started, so the seam doesn't bulge."),
            ]},
            {"name": "Precision", "fields": [
                MM("resolution", "Slice resolution", 0.001, 0.2, 0.001, adv=True,
                   tip="Outlines are simplified to this tolerance. Bigger is faster but less detailed."),
                MM("xy_hole_compensation", "X-Y hole compensation", -2, 2,
                   tip="Positive makes holes bigger, negative smaller."),
                MM("xy_contour_compensation", "X-Y contour compensation", -2, 2,
                   tip="Positive makes the outside of the part bigger, negative smaller."),
                MM("elefant_foot_compensation", "Elephant foot compensation", 0, 1,
                   tip="Shrinks the first layer to offset the squish that makes it wider."),
                MM("curve_tolerance", "Curved path tolerance", 0.002, 0.1, 0.002, adv=True,
                   tip="How closely toolpaths follow the curved layers. Smaller is smoother but makes bigger files."),
            ]},
            {"name": "Ironing", "fields": [
                F("ironing_type", "Ironing type", "select",
                  options=[("no ironing", "No ironing"), ("top", "Top surfaces"), ("topmost", "Topmost surface"),
                           ("solid", "All solid layer")],
                  tip="On curved layers the ironing follows the curve, so tilted tops get smoothed too."),
                F("ironing_pattern", "Ironing pattern", "select",
                  options=[("rectilinear", "Rectilinear"), ("concentric", "Concentric")]),
                F("ironing_flow", "Ironing flow", "percent", "%", min=0, max=100,
                  tip="Calibration → Ironing finds it."),
                MM("ironing_spacing", "Ironing line spacing", 0.01, 1),
                MM("ironing_inset", "Ironing inset", 0, 5, adv=True),
                F("ironing_angle", "Ironing angle", unit="°", min=-1, max=359, step=1, adv=True,
                  tip="Direction of the ironing lines. -1 = across the top surface lines (the usual)."),
                SPD("ironing_speed", "Ironing speed", 1, 200),
            ]},
            {"name": "Walls and surfaces", "fields": [
                F("wall_sequence", "Walls printing order", "select",
                  options=[("inner_outer", "Inner/Outer"), ("outer_inner", "Outer/Inner"),
                           ("inner_outer_inner", "Inner/Outer/Inner")]),
                F("is_infill_first", "Print infill first", "checkbox", adv=True),
                F("only_one_wall_top", "Only one wall on top surfaces", "checkbox"),
                F("only_one_wall_first_layer", "Only one wall on first layer", "checkbox", adv=True),
            ]},
            {"name": "Bridging", "fields": [
                F("bridge_flow", "Bridge flow ratio", min=0.5, max=2, step=0.01, adv=True),
                F("thick_bridges", "Thick bridges", "checkbox",
                  tip="Round strands as wide as the nozzle: stronger, more reliable bridges. Off: flatter, neater bridges."),
                F("bridge_angle", "Bridge infill direction", unit="°", min=0, max=359, step=1, adv=True,
                  tip="0 = automatic (across the short span)."),
            ]},
            {"name": "Flow ratios", "fields": [
                F("print_flow_ratio", "Object flow ratio", min=0.5, max=1.5, step=0.01, adv=True,
                  tip="On top of the filament's flow ratio."),
                F("first_layer_flow_ratio", "First layer flow ratio", min=0.5, max=1.5, step=0.01, adv=True,
                  tip="More or less plastic on the first layer only: Calibration → First layer finds it."),
                F("top_surface_flow_ratio", "Top surface flow ratio", min=0.5, max=1.5, step=0.01, adv=True,
                  tip="Calibration → Top surface flow finds it."),
                F("bottom_surface_flow_ratio", "Bottom surface flow ratio", min=0.5, max=1.5, step=0.01, adv=True),
            ]},
            {"name": "Overhangs", "fields": [
                F("enable_overhang_speed", "Slow down for overhangs", "checkbox"),
            ]},
        ]},
        {"tab": "Strength", "groups": [
            {"name": "Walls", "fields": [
                F("wall_count", "Wall loops", min=1, max=20, step=1),
                F("alternate_extra_wall", "Alternate extra wall", "checkbox",
                  tip="One more wall on every other layer: the walls lock into each other, stronger parts."),
            ]},
            {"name": "Top/bottom shells", "fields": [
                F("top_surface_pattern", "Top surface pattern", "select", options=SURFACE),
                F("top_surface_density", "Top surface density", "percent", "%", min=10, max=100, adv=True),
                F("top_layers", "Top shell layers", min=0, max=30, step=1),
                MM("top_shell_thickness", "Top shell thickness", 0, 10, 0.1,
                   tip="Adds top layers when the layer count alone is thinner than this."),
                F("bottom_surface_pattern", "Bottom surface pattern", "select", options=SURFACE),
                F("bottom_surface_density", "Bottom surface density", "percent", "%", min=10, max=100, adv=True),
                F("bottom_layers", "Bottom shell layers", min=0, max=30, step=1),
                MM("bottom_shell_thickness", "Bottom shell thickness", 0, 10, 0.1),
                F("internal_solid_infill_pattern", "Internal solid infill pattern", "select", options=SURFACE, adv=True),
            ]},
            {"name": "Sparse infill", "fields": [
                F("infill_density", "Sparse infill density", "percent", "%", min=0, max=100),
                F("infill_pattern", "Sparse infill pattern", "select", options=SPARSE,
                  tip="Grid / Cubic / Gyroid: strong all round. Lightning: least plastic, only holds up the top "
                      "surfaces. Honeycomb / 3D Honeycomb / Tri-hexagon / Stars: stiff. Line / Rectilinear: fastest."),
                F("lightning_angle", "Lightning infill angle", unit="°", min=5, max=80, step=1, adv=True,
                  tip="Lightning only: how far the infill may lean while it narrows down under a top surface."),
                F("solid_infill_every_layers", "Solid infill every", unit="layers", min=0, max=100, step=1,
                  tip="Every Nth layer of sparse infill is printed solid: stiffer parts. 0 = off."),
            ]},
            {"name": "Advanced", "fields": [
                F("infill_direction", "Infill direction", unit="°", min=0, max=360, step=1, adv=True),
                F("solid_infill_direction", "Solid infill direction", unit="°", min=0, max=360, step=1, adv=True),
                F("infill_overlap", "Infill/wall overlap", "percent", "%", min=0, max=100, adv=True),
                F("minimum_sparse_infill_area", "Minimum sparse infill threshold", unit="mm²", min=0, max=500, step=1,
                  adv=True, tip="Sparse areas smaller than this are printed solid."),
            ]},
        ]},
        {"tab": "Speed", "groups": [
            {"name": "Initial layer speed", "fields": [
                SPD("first_layer_speed", "Initial layer"),
                SPD("first_layer_infill_speed", "Initial layer infill"),
                SPD("initial_layer_travel_speed", "Initial layer travel", 0, 700, tip="0 = same as travel."),
                F("slow_down_layers", "Number of slow layers", min=0, max=50, step=1, adv=True,
                  tip="Speeds ramp up from the initial-layer speed over this many layers."),
            ]},
            {"name": "Other layers speed", "fields": [
                SPD("outer_wall_speed", "Outer wall"),
                SPD("inner_wall_speed", "Inner wall"),
                F("small_perimeter_speed", "Small perimeters", "percent", "%", min=1, max=100, adv=True,
                  tip="Percent of the outer wall speed."),
                MM("small_perimeter_threshold", "Small perimeters threshold", 0, 50, 0.5, adv=True,
                   tip="Loops smaller than a circle of this radius use the small perimeter speed. 0 = off."),
                SPD("solid_speed", "Internal solid infill"),
                SPD("top_surface_speed", "Top surface"),
                SPD("infill_speed", "Sparse infill"),
                SPD("bridge_speed", "Bridge"),
                SPD("support_speed", "Support"),
                SPD("support_interface_speed", "Support interface"),
                SPD("skirt_speed", "Skirt", 0, adv=True, tip="0 = initial layer speed."),
            ]},
            {"name": "Overhang speed", "fields": [
                SPD("overhang_1_4_speed", "(10%, 25%)", 0, tip="0 = no slow-down for this amount of overhang."),
                SPD("overhang_2_4_speed", "[25%, 50%)", 0),
                SPD("overhang_3_4_speed", "[50%, 75%)", 0),
                SPD("overhang_4_4_speed", "[75%, 100%)", 0),
            ]},
            {"name": "Travel speed", "fields": [
                SPD("travel_speed", "Travel", 20, 700),
            ]},
            {"name": "Acceleration", "fields": [
                ACC("default_acceleration", "Normal printing", tip="0 = don't send acceleration commands at all."),
                ACC("outer_wall_acceleration", "Outer wall", tip="0 = normal printing acceleration."),
                ACC("inner_wall_acceleration", "Inner wall", adv=True),
                ACC("top_surface_acceleration", "Top surface", adv=True),
                ACC("sparse_infill_acceleration", "Sparse infill", adv=True),
                ACC("initial_layer_acceleration", "Initial layer"),
                ACC("travel_acceleration", "Travel"),
                ACC("bridge_acceleration", "Bridge", adv=True),
                ACC("internal_solid_infill_acceleration", "Internal solid infill", adv=True),
            ]},
            {"name": "Jerk (XY)", "fields": [
                F("default_jerk", "Default", unit="mm/s", min=0, max=100, step=0.5,
                  tip="0 = don't send jerk commands (Bambu printers ignore them)."),
                F("outer_wall_jerk", "Outer wall", unit="mm/s", min=0, max=100, step=0.5, adv=True),
                F("inner_wall_jerk", "Inner wall", unit="mm/s", min=0, max=100, step=0.5, adv=True),
                F("infill_jerk", "Infill", unit="mm/s", min=0, max=100, step=0.5, adv=True),
                F("top_surface_jerk", "Top surface", unit="mm/s", min=0, max=100, step=0.5, adv=True),
                F("initial_layer_jerk", "Initial layer", unit="mm/s", min=0, max=100, step=0.5, adv=True),
                F("travel_jerk", "Travel", unit="mm/s", min=0, max=100, step=0.5, adv=True),
            ]},
        ]},
        {"tab": "Non-planar", "groups": [
            {"name": "Curved layers", "fields": [
                F("mode", "Mode", "select",
                  options=[("conical", "Conical (overhangs)"), ("surface", "Follow the top surface (wavy / curved tops)")],
                  tip="Conical: every layer is a cone, so outward (or inward) overhangs print without support. "
                      "Follow the top surface: the lower layers are flat and the top layers bend to the shape of the "
                      "model's top, so wavy rims, domes and sculpted tops print as smooth curved lines, not steps."),
                MM("surface_depth", "Top surface: depth followed", 1, 50, 0.5,
                   tip="Top surface mode: how much height difference of the top the layers follow (the rest stays flat). "
                       "More needs a taller model: the layers bend over about 2x this height."),
                F("surface_max_angle", "Top surface: steepest slope", unit="°", min=0, max=60, step=1,
                  tip="Top surface mode: steeper parts of the top are smoothed to this slope so the heater block clears "
                      "the print. 0 = what your toolhead allows (Printer settings → Toolhead clearance)."),
                F("cone_angle", "Cone angle", unit="°", min=-45, max=45, step=0.5,
                  tip="+ prints outward overhangs (mushrooms, flares) without supports; − is for inward overhangs. Start with 10–15°."),
                F("auto_center", "Cone axis at part centre", "checkbox", extra=True),
                F("cone_cx", "Cone axis X", unit="mm", step=0.5, extra=True),
                F("cone_cy", "Cone axis Y", unit="mm", step=0.5, extra=True),
                F("auto_ramp", "Automatic flat-to-cone ramp", "checkbox", extra=True),
                F("ramp_value", "Ramp height", unit="mm", min=0.5, max=200, step=0.5, extra=True),
                F("min_layer_ratio", "Thinnest layer allowed (auto ramp)", min=0.3, max=0.95, step=0.05,
                  tip="While layers bend, they can't get thinner than this fraction of the layer height."),
                F("collision_avoidance", "If the toolhead could hit the print", "select",
                  options=[("lower_angle", "Lower the cone angle automatically"), ("warn", "Only warn")],
                  tip="Lowering: the slicer starts at the steepest angle your toolhead clearance allows and, if the "
                      "check still finds a collision, lowers it 1° at a time (up to 3 tries). Your preset's angle is not changed."),
            ]},
        ]},
        {"tab": "Support", "groups": [
            {"name": "Support", "fields": [
                F("enable_support", "Enable support", "checkbox",
                  tip="Checked against the curved layers: overhangs the cone angle already handles get none. Try a "
                      "bigger cone angle first; use support for flat undersides and bridges. Painted supports work "
                      "even with this off."),
                F("support_type", "Type", "select", options=[("normal", "Normal"), ("tree", "Tree")],
                  tip="Normal: columns straight down. Tree: branches that lean and merge, touching the part only at "
                      "small tips. Less plastic, easier to remove, leaves fewer marks."),
                F("support_style", "Style", "select", options=SUPPORT_STYLES,
                  tip="Normal: Default follows the overhang, Grid uses rectangles (sturdier). Tree: Slim (thin "
                      "branches), Strong (thicker, more branches), Hybrid (normal columns under big flat areas), "
                      "Organic (branches curve smoothly)."),
                F("support_threshold_angle", "Threshold angle", unit="°", min=1, max=89, step=1,
                  tip="Overhangs whose slope (from horizontal) is below this get support."),
                F("support_on_build_plate_only", "On build plate only", "checkbox"),
                F("support_remove_small_overhang", "Remove small overhangs", "checkbox", adv=True),
            ]},
            {"name": "Advanced", "fields": [
                MM("support_top_z_distance", "Top Z distance", 0, 1, 0.05),
                MM("support_bottom_z_distance", "Bottom Z distance", 0, 1, 0.05,
                   tip="Gap where support stands on the part."),
                F("support_base_pattern", "Base pattern", "select",
                  options=[("rectilinear", "Rectilinear"), ("rectilinear-grid", "Rectilinear grid"),
                           ("honeycomb", "Honeycomb"), ("hollow", "Hollow (walls only)")]),
                F("support_wall_count", "Support wall loops", min=0, max=5, step=1,
                  tip="Walls around the support areas. 0 = none (tree support always gets at least one)."),
                MM("support_spacing", "Base pattern spacing", 0.5, 10, 0.1),
                F("support_angle", "Pattern angle", unit="°", min=0, max=359, step=1, adv=True),
                F("support_interface_layers", "Top interface layers", min=0, max=10, step=1),
                F("support_bottom_interface_layers", "Bottom interface layers", min=0, max=10, step=1,
                  tip="Dense layers where support stands on the part (instead of the bed)."),
                F("support_interface_pattern", "Interface pattern", "select",
                  options=[("rectilinear", "Rectilinear"), ("grid", "Grid"), ("concentric", "Concentric")], adv=True),
                MM("support_interface_spacing", "Top interface spacing", 0.1, 5, 0.05),
                MM("support_expansion", "Normal support expansion", 0, 10, 0.1, adv=True),
                MM("support_xy_distance", "Support/object XY distance", 0, 3, 0.05),
            ]},
            {"name": "Tree supports", "fields": [
                F("tree_support_branch_angle", "Branch angle", unit="°", min=5, max=70, step=1,
                  tip="How far branches may lean. Bigger reaches further but needs good cooling."),
                MM("tree_support_branch_distance", "Branch distance", 1, 20, 0.5,
                   tip="Space between branch tips under an overhang. Smaller holds better, uses more plastic."),
                MM("tree_support_branch_diameter", "Branch diameter", 1, 20, 0.5,
                   tip="The thickest a branch gets."),
                MM("tree_support_tip_diameter", "Tip diameter", 0.4, 5, 0.1,
                   tip="Where a branch touches the part. Smaller leaves smaller marks."),
                F("tree_support_branch_diameter_angle", "Branch diameter angle", unit="°", min=0, max=15, step=0.5,
                  tip="How fast branches get thicker going down."),
            ]},
        ]},
        {"tab": "Multimaterial", "groups": [
            {"name": "Filament for features", "fields": [
                F("wall_filament", "Walls", "select", options=FIL,
                  tip="Default = the filament the object (or the layer) is assigned."),
                F("sparse_infill_filament", "Infill", "select", options=FIL),
                F("solid_infill_filament", "Solid infill", "select", options=FIL),
                F("support_filament", "Support/raft base", "select", options=FIL,
                  tip="Default = whichever filament is loaded at that layer, so supports cost no extra changes."),
                F("support_interface_filament", "Support/raft interface", "select", options=FIL),
            ]},
            {"name": "Prime tower", "fields": [
                F("prime_tower", "Prime tower", "select",
                  options=[("auto", "Auto (several filaments, printer without a purge chute)"), ("on", "On"), ("off", "Off")],
                  tip="A small block next to the model where the nozzle purges the old colour after each change, so it "
                      "doesn't mix into the model. Bambu printers flush into their chute, so Auto leaves it off there. "
                      "Without a filament-change G-code the whole flush volume goes into it."),
                MM("prime_tower_width", "Width", 15, 80, 1),
                F("prime_tower_x", "Position X", unit="mm", min=-1, max=1000, step=1,
                  tip="Front-left corner of the tower. -1 = placed automatically next to the model."),
                F("prime_tower_y", "Position Y", unit="mm", min=-1, max=1000, step=1),
                F("prime_volume", "Prime volume", unit="mm³", min=0, max=500, step=5,
                  tip="Purged into the tower at least, at each change (more if the change itself doesn't flush)."),
                F("prime_tower_speed", "Speed", unit="mm/s", min=10, max=500, step=5, adv=True),
            ]},
            {"name": "Flush options", "fields": [
                F("flush_multiplier", "Flushing volume multiplier", min=0.1, max=3, step=0.05,
                  tip="Scales the purge after each filament change. The base volume comes from the two colours "
                      "(dark to light needs the most)."),
            ]},
        ]},
        {"tab": "Others", "groups": [
            {"name": "Print sequence", "fields": [
                F("print_sequence", "Print sequence", "select",
                  options=[("by_layer", "By layer (all objects together)"), ("by_object", "By object (one after another)")],
                  tip="By object: each object is printed to the top before the next one starts, each with its own "
                      "curved layers. Fewer travels and filament changes, and a failed object doesn't spoil the others. "
                      "The objects must be far enough apart for the toolhead, and all but the last lower than the "
                      "gantry (Printer settings → Toolhead clearance)."),
            ]},
            {"name": "Skirt", "fields": [
                F("skirt_loops", "Skirt loops", min=0, max=10, step=1),
                F("skirt_height", "Skirt height", unit="layers", min=1, max=100, step=1, adv=True),
                MM("skirt_distance", "Skirt distance", 0, 30, 0.5),
            ]},
            {"name": "Brim", "fields": [
                F("brim_type", "Brim type", "select",
                  options=[("no_brim", "No-brim"), ("outer_only", "Outer brim only"), ("inner_only", "Inner brim only"),
                           ("outer_and_inner", "Outer and inner brim"), ("brim_ears", "Mouse ears")],
                  tip="Mouse ears: round pads only at sharp corners, where parts lift first."),
                MM("brim_width", "Brim width", 0, 50, 0.5),
                MM("brim_object_gap", "Brim-object gap", 0, 2, 0.05),
                F("brim_ears_max_angle", "Brim ears max angle", unit="°", min=30, max=180, step=5, adv=True,
                  tip="Mouse ears: corners sharper than this get an ear."),
            ]},
            {"name": "Fuzzy skin", "fields": [
                F("fuzzy_skin", "Fuzzy skin", "select",
                  options=[("none", "None"), ("external", "Outer walls"), ("all", "All walls")],
                  tip="Random jitter on the walls: a rough, textured surface that hides layer lines."),
                MM("fuzzy_skin_thickness", "Fuzzy skin thickness", 0.05, 2, 0.05),
                MM("fuzzy_skin_point_distance", "Fuzzy skin point distance", 0.1, 5, 0.05),
            ]},
            {"name": "G-code output", "fields": [
                F("reduce_infill_retraction", "Reduce infill retraction", "checkbox",
                  tip="Don't retract on travels that stay inside sparse infill, where stringing doesn't show."),
                F("filename_format", "Filename format", "text", extra=True,
                  tip="Placeholders: {input_filename_base} {layer_height} {filament_type} {print_time} {printer_model}"),
                F("post_process", "Post-processing scripts", "code", extra=True, adv=True,
                  tip="One command per line. Each runs with the exported G-code file's path added at the end."),
            ]},
            {"name": "Notes", "fields": [
                F("process_notes", "Notes", "code", extra=True),
            ]},
        ]},
    ],
}

PERCENT_KEYS = {"infill_density", "infill_overlap", "ironing_flow", "small_perimeter_speed",
                "top_surface_density", "bottom_surface_density"}  # shown as %, stored 0-1

A1_START_NOTE = """; No Bambu template set - generic start. Set a template under Printer > Machine G-code
M140 S{bed_temp_first}
M104 S150
G28
M190 S{bed_temp_first}
M109 S{nozzle_temp_first}
G90
M83
G92 E0
G1 Z5 F3000
G1 X10 Y5 F12000
G1 Z0.3 F600
G1 X110 Y5 E12 F1200
G1 X110 Y5.5
G1 X10 Y5.5 E8 F1500
G92 E0
"""

A1_END = """G91
G1 Z10 F600
G90
M104 S0
M140 S0
M106 S0
G1 X128 Y250 F6000
M84
"""


def _keys(cat):
    return [f["key"] for tab in SCHEMA[cat] for g in tab["groups"] for f in g["fields"] if not f["extra"]]


def _base(cat):
    s = Settings()
    return {k: getattr(s, k) for k in _keys(cat)}


# Orca's layer heights for each nozzle size: (layer height, quality name)
QUALITIES = {
    0.2: [(0.06, "Fine"), (0.08, "Optimal"), (0.10, "Standard"), (0.12, "Draft"), (0.14, "Extra Draft")],
    0.4: [(0.08, "Extra Fine"), (0.12, "Fine"), (0.16, "Optimal"), (0.20, "Standard"), (0.24, "Draft"),
          (0.28, "Extra Draft")],
    0.6: [(0.18, "Fine"), (0.24, "Optimal"), (0.30, "Standard"), (0.36, "Draft"), (0.42, "Extra Draft")],
    0.8: [(0.24, "Fine"), (0.32, "Optimal"), (0.40, "Standard"), (0.48, "Draft"), (0.56, "Extra Draft")],
}
SPEED_BY_QUALITY = {"Extra Fine": 0.7, "Fine": 0.8, "Optimal": 0.9, "Standard": 1.0, "Draft": 1.05, "Extra Draft": 1.1}
RETRACT_BY_NOZZLE = {0.2: 0.8, 0.4: 0.8, 0.6: 1.2, 0.8: 1.4}    # starting values, tune for your nozzle


def printer_name(model, nozzle):
    return f"Bambu Lab {model} {nozzle} nozzle"


def process_name(lh, quality, nozzle):
    return f"{lh:.2f}mm {quality}" + ("" if nozzle == 0.4 else f" @{nozzle:g} nozzle")


def old_process_name(lh, quality, nozzle):
    """Names up to 0.7.2, when the presets were made for the A1 only."""
    return f"{lh:.2f}mm {quality} @A1" + ("" if nozzle == 0.4 else f" {nozzle} nozzle")


def guess_flavor(data):
    """G-code flavor of a printer preset: Bambu, or what its maker's Orca profile says."""
    if (data.get("vendor") or "") == "Bambu Lab" or (data.get("model") or "").startswith("Bambu Lab"):
        return "bambu"
    from nonplanar import machine
    f = (machine.table().get(data.get("model") or "") or {}).get("gcode_flavor") or ""
    return "klipper" if f == "klipper" else "reprapfirmware" if f.startswith("reprap") else "marlin"


# How fast a printer prints well, as a share of Bambu's speeds (printers not listed: by firmware)
SPEED_FACTOR = {
    "AnkerMake M5": 100, "AnkerMake M5C": 100, "Creality Ender-3 V3 SE": 80, "Anycubic Kobra 2": 80,
    "Anycubic Kobra 2 Neo": 60, "Sovol SV07": 80, "Prusa MK4": 70, "Prusa MK4S": 70, "Prusa CORE One": 70,
    "Prusa XL": 70, "Prusa MK3.5": 60, "Prusa MINI+": 50, "Prusa MK3S+": 50, "Elegoo Neptune 3 Pro": 50,
    "Elegoo Neptune 3 Plus": 50, "Elegoo Neptune 3 Max": 45, "Creality Ender-3 S1": 45, "Creality Ender-3 S1 Pro": 45,
    "Creality Ender-3 S1 Plus": 40, "Creality Ender-5 S1": 60, "Snapmaker 2.0 A250": 35, "Snapmaker 2.0 A350": 35,
    "Snapmaker Artisan": 35,
}


def guess_host(data):
    """How the app connects to a printer preset: Bambu, Klipper (Moonraker), PrusaLink or OctoPrint."""
    fl = data.get("gcode_flavor") or guess_flavor(data)
    if fl == "bambu":
        return "bambu"
    if fl == "klipper":
        return "moonraker"
    return "prusalink" if (data.get("vendor") or "") == "Prusa" else "octoprint"


_CLEARANCE = None


def guess_clearance(data):
    """(toolhead radius, height to the gantry rod) for a printer preset, from OrcaSlicer's profiles."""
    global _CLEARANCE
    if _CLEARANCE is None:
        try:
            import json as _json
            with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nonplanar",
                                   "clearance.json"), encoding="utf-8") as f:
                _CLEARANCE = {k: v for k, v in _json.load(f).items() if not k.startswith("_")}
        except (OSError, ValueError):
            _CLEARANCE = {}
    model = (data.get("model") or "").strip()
    if model in _CLEARANCE:
        return tuple(_CLEARANCE[model])
    for name, v in sorted(_CLEARANCE.items(), key=lambda kv: -len(kv[0])):
        if model.endswith(name) or name.endswith(model) and model:
            return tuple(v)
    return 65.0, 34.0


def guess_speed(data):
    """Speed limit (%) for a printer preset."""
    model = (data.get("model") or "").strip()
    for name, f in SPEED_FACTOR.items():
        if model.endswith(name) and (len(model) == len(name) or model[-len(name) - 1] == " "):
            return f
    fl = data.get("gcode_flavor") or guess_flavor(data)
    return 100 if fl in ("bambu", "klipper") else 70 if fl == "reprapfirmware" else 35


# Bambu's speeds and accelerations (A1 / X1, 0.20 mm Standard) that the speed limit is a share of
SPEED_REF = {"outer_wall_speed": 200, "inner_wall_speed": 300, "solid_speed": 250, "top_surface_speed": 200,
             "infill_speed": 270, "support_speed": 150, "support_interface_speed": 80, "travel_speed": 500}
ACCEL_REF = ("default_acceleration", "outer_wall_acceleration", "inner_wall_acceleration", "top_surface_acceleration",
             "sparse_infill_acceleration", "travel_acceleration", "bridge_acceleration",
             "internal_solid_infill_acceleration")


def apply_speed_limit(s):
    """Cap the speeds at the printer's speed limit (never raises a speed)."""
    f = max(0.1, min(1.0, float(getattr(s, "speed_factor", 100) or 100) / 100.0))
    if f >= 0.999:
        return
    for k, ref in SPEED_REF.items():
        cap = max(150.0, ref * f) if k == "travel_speed" else ref * f
        setattr(s, k, min(float(getattr(s, k)), round(cap)))
    acc = round(10000 * f * f / 100) * 100                         # 35% -> about 1200 mm/s²
    for k in ACCEL_REF:
        if getattr(s, k) > acc:
            setattr(s, k, float(acc))


def _defaults():
    printers = {}
    for model, bed in (("A1", 256), ("A1 mini", 180)):
        for nz in NOZZLES:
            printers[printer_name(model, nz)] = {
                **_base("printer"), "model": f"Bambu Lab {model}", "bed_x": bed, "bed_y": bed, "max_z": bed,
                "nozzle_diameter": nz, "retract_length": RETRACT_BY_NOZZLE[nz], "template_3mf": "",
                "start_gcode": A1_START_NOTE, "end_gcode": A1_END, "change_filament_gcode": DEFAULT_CHANGE,
                "ip": "", "serial": "", "access_code": "", "vendor": "Bambu Lab"}
    for vendor, model, bx, by, bz, ext in printers_mod.PRINTERS:
        for nz in printers_mod.NOZZLES:           # every printer comes in all four nozzle sizes
            length, speed = printers_mod.retract(ext, nz)
            printers[printers_mod.name(vendor, model, nz)] = {
                **_base("printer"), "model": f"{vendor} {model}" if vendor != "Custom" else f"Custom {model}",
                "vendor": vendor, "bed_x": bx, "bed_y": by, "max_z": bz, "nozzle_diameter": nz,
                "retract_length": length, "retract_speed": speed, "template_3mf": "",
                "start_gcode": printers_mod.start_gcode(vendor, model, bx, by), "end_gcode": printers_mod.end_gcode(bx, by, bz),
                "change_filament_gcode": DEFAULT_CHANGE, "ip": "", "serial": "", "access_code": ""}

    def fil(t, vendor, color, n1, n, b1, b, mvs, dens, flow=0.98, **kw):
        return {**_base("filament"), "type": t, "vendor": vendor, "color": color, "filament_density": dens,
                "flow": flow, "max_volumetric_speed": mvs, "nozzle_temp_first": n1, "nozzle_temp": n,
                "bed_temp_first": b1, "bed_temp": b, **kw}

    for d in printers.values():
        d["gcode_flavor"] = guess_flavor(d)
        d["speed_factor"] = guess_speed(d)
        d.update(host_type=guess_host(d), host="", api_key="", host_password="")
        d["extruder_clearance_radius"], d["extruder_clearance_height_to_rod"] = guess_clearance(d)
    filaments = {
        "Bambu PLA Basic": fil("PLA", "Bambu Lab", "#f2f2f2", 220, 220, 65, 65, 21, 1.26, slow_down_layer_time=8),
        "Generic PLA": fil("PLA", "Generic", "#3b82f6", 220, 215, 60, 60, 15, 1.24, slow_down_layer_time=8),
        "Generic PETG": fil("PETG", "Generic", "#22c55e", 245, 240, 75, 70, 10, 1.27, 0.95,
                            close_fan_first_layers=3, fan_min_speed=20, fan_max_speed=40, fan_cooling_layer_time=30,
                            slow_down_layer_time=12, overhang_fan_speed=90, pressure_advance=0.04),
        "Generic TPU": fil("TPU", "Generic", "#f97316", 230, 230, 40, 35, 3.5, 1.21, 1.0,
                           fan_min_speed=80, fan_max_speed=100, slow_down_layer_time=8, pressure_advance=0.1),
    }
    for brand, fname, material, colour, changes in filaments_mod.FILAMENTS:
        vendor = "Bambu Lab" if brand == "Bambu" else brand
        filaments[filaments_mod.name(brand, fname)] = {
            **filaments_mod.preset(material, colour, changes, _base("filament")), "vendor": vendor}

    processes = {}
    for nz in (0.4, 0.2, 0.6, 0.8):          # 0.4 first: it fills in keys missing from older presets
        for lh, q in QUALITIES[nz]:
            processes[process_name(lh, q, nz)] = _process(nz, lh, q)
    return {"printer": printers, "filament": filaments, "process": processes}


SPEED_REV = 2


def _speeds(sc):
    """Built-in process speeds (0.7.20): about 80% of Bambu's own, scaled by quality. Printers cap them (speed limit)."""
    return {"outer_wall_speed": round(160 * sc), "inner_wall_speed": round(240 * sc), "solid_speed": round(200 * sc),
            "top_surface_speed": round(160 * sc), "infill_speed": round(215 * sc),
            "first_layer_speed": 50, "first_layer_infill_speed": 90, "travel_speed": 400, "bridge_speed": 50,
            "support_speed": 120, "support_interface_speed": 70,
            "default_acceleration": 6000, "outer_wall_acceleration": 4000, "top_surface_acceleration": 2000,
            "initial_layer_acceleration": 500, "travel_acceleration": 8000, "speed_rev": SPEED_REV}


def _speeds_rev1(sc):
    """The built-in process speeds of 0.7.16 - 0.7.19 (Bambu's own), to recognise presets nobody changed."""
    return {"outer_wall_speed": round(200 * sc), "inner_wall_speed": round(300 * sc), "solid_speed": round(250 * sc),
            "top_surface_speed": round(200 * sc), "infill_speed": round(270 * sc),
            "first_layer_speed": 50, "first_layer_infill_speed": 105, "travel_speed": 500, "bridge_speed": 50,
            "support_speed": 150, "support_interface_speed": 80,
            "default_acceleration": 8000, "outer_wall_acceleration": 5000, "top_surface_acceleration": 2000,
            "initial_layer_acceleration": 500, "travel_acceleration": 10000}


def _old_speeds(sc):
    """The built-in process speeds up to 0.7.15 (to recognise presets nobody changed)."""
    return {"outer_wall_speed": round(50 * sc), "inner_wall_speed": round(70 * sc), "solid_speed": round(70 * sc),
            "top_surface_speed": round(50 * sc), "infill_speed": round(100 * sc), "first_layer_speed": 30,
            "first_layer_infill_speed": 50, "travel_speed": 200}


def _process(nz, lh, quality):
    k = nz / 0.4
    sc = SPEED_BY_QUALITY[quality]

    def w(v):
        return round(v * k, 2)

    walls = 2 if (nz >= 0.6 or quality == "Extra Draft") else 3
    shells = max(3, math.ceil(0.8 / lh - 1e-9))
    return {**_base("process"), "compatible_nozzle": nz, "layer_height": lh,
            "first_layer_height": round(nz / 2, 2),
            "line_width": w(0.42), "first_layer_line_width": w(0.5), "outer_wall_line_width": w(0.42),
            "inner_wall_line_width": w(0.45), "top_surface_line_width": w(0.42),
            "sparse_infill_line_width": w(0.45), "solid_infill_line_width": w(0.42), "support_line_width": w(0.42),
            "support_top_z_distance": round(min(max(lh, 0.12), 0.4), 2),
            "wall_count": walls, "top_layers": shells, "bottom_layers": shells,
            **_speeds(sc),
            "auto_center": True, "cone_cx": 128.0, "cone_cy": 128.0, "auto_ramp": True, "ramp_value": 10.0,
            "filename_format": "{input_filename_base}", "post_process": "", "process_notes": ""}


# Built-in presets renamed in 0.5 (Orca's names). An untouched copy of the old file is retired.
RENAMED = {"process": {"0.16mm Fine @A1": "0.16mm Optimal @A1", "0.28mm Draft @A1": "0.28mm Extra Draft @A1"}}


def _legacy_process(lh, walls, top, bottom, speed_scale):
    """The 0.4 built-in process presets as they were before 0.5 (to recognise untouched copies)."""
    def sc(v):
        return round(v * speed_scale)
    return {"layer_height": lh, "wall_count": walls, "top_layers": top, "bottom_layers": bottom,
            "outer_wall_speed": sc(50), "inner_wall_speed": sc(70), "solid_speed": sc(70),
            "top_surface_speed": sc(50), "infill_speed": sc(100)}


LEGACY = {"0.16mm Fine @A1": _legacy_process(0.16, 3, 5, 5, 0.8),
          "0.28mm Draft @A1": _legacy_process(0.28, 2, 3, 3, 1.1)}


def resolve(path):
    """Preset file paths are stored relative to the profiles folder so the app folder can move."""
    if not path:
        return ""
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def _safe(name):
    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip()
    if not name:
        raise ValueError("Preset name is empty")
    return name


PRESET_VERSION = 2   # bump when the built-in presets change; older built-in files get refreshed


def _same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) < 1e-6
    return a == b


def _retire_a1_names():
    """0.7.3: process presets lost their "@A1". A built-in copy that was never edited is retired
    (the new name replaces it); an edited one stays, under its old name, as your own preset."""
    for nz in NOZZLES:
        for lh, q in QUALITIES[nz]:
            p = os.path.join(ROOT, "process", _safe(old_process_name(lh, q, nz)) + ".json")
            if not os.path.exists(p):
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    old = json.load(f)
            except (OSError, ValueError):
                continue
            ref = {**_process(nz, lh, q), **_old_speeds(SPEED_BY_QUALITY[q])}   # those presets had the old speeds
            if all(_same(v, ref[k]) for k, v in old.items() if k in ref):
                os.replace(p, p + ".old.bak")


def _retire_renamed():
    _retire_a1_names()
    for cat, names in RENAMED.items():
        for old_name in names:
            p = os.path.join(ROOT, cat, _safe(old_name) + ".json")
            if not os.path.exists(p):
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    old = json.load(f)
            except (OSError, ValueError):
                continue
            ref = LEGACY.get(old_name, {})
            if all(old.get(k) == v for k, v in ref.items()):     # never edited: replaced by the new name
                os.replace(p, p + ".old.bak")


def _sibling_connection(name, data):
    """A new nozzle variant of a printer gets the IP, serial and access code of an existing variant."""
    d = os.path.join(ROOT, "printer")
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json") or fn[:-5] == name:
            continue
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                other = json.load(f)
        except (OSError, ValueError):
            continue
        if other.get("model") == data.get("model") and other.get("serial"):
            return {k: other[k] for k in ("ip", "serial", "access_code") if other.get(k)}
    return {}


def _speed_up_builtins():
    """Built-in process presets still on an older set of factory speeds get the current ones
    (0.7.16: Bambu's speeds; 0.7.20: 20% calmer). A preset whose speeds you changed is left exactly as it is."""
    for nz in NOZZLES:
        for lh, q in QUALITIES[nz]:
            p = os.path.join(ROOT, "process", _safe(process_name(lh, q, nz)) + ".json")
            if not os.path.exists(p):
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            rev = data.get("speed_rev", 0)
            if rev >= SPEED_REV:
                continue
            sc = SPEED_BY_QUALITY[q]
            olds = [_speeds_rev1(sc)] + ([_old_speeds(sc)] if rev == 0 else [])   # rev 0: made before or after 0.7.16
            if any(all(_same(data.get(k), v) for k, v in old.items()) for old in olds):
                data.update(_speeds(sc))
            else:
                data["speed_rev"] = SPEED_REV          # your own speeds: left alone, never looked at again
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)


_DEFAULTS_LOCK = threading.Lock()


def ensure_defaults():
    with _DEFAULTS_LOCK:
        _ensure_defaults()


def _ensure_defaults():
    todo = []
    os.makedirs(os.path.join(ROOT, "process"), exist_ok=True)
    _retire_renamed()
    _speed_up_builtins()
    for cat, presets in _defaults().items():
        d = os.path.join(ROOT, cat)
        os.makedirs(d, exist_ok=True)
        for name, data in presets.items():
            p = os.path.join(d, _safe(name) + ".json")
            if os.path.exists(p):
                try:
                    with open(p, encoding="utf-8") as f:
                        old = json.load(f)
                except (OSError, ValueError):
                    old = {}
                if old.get("preset_version", 1) >= PRESET_VERSION:
                    continue
                # built-in preset from an older version: keep a copy, then replace it.
                # Keep the user's connection details and template in printer presets.
                os.replace(p, p + f".v{old.get('preset_version', 1)}.bak")
                if cat == "printer":
                    data = {**data, **{k: old[k] for k in ("ip", "serial", "access_code", "template_3mf") if old.get(k)}}
            elif cat == "printer":
                data = {**data, **_sibling_connection(name, data)}
            todo.append((p, {**data, "preset_version": PRESET_VERSION}))

    def write(job):
        p, data = job
        text = json.dumps(data, indent=2)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    if len(todo) > 20:        # the first start writes about a thousand files, each one scanned by the antivirus
        with ThreadPoolExecutor(16) as ex:
            list(ex.map(write, todo))
    else:
        for job in todo:
            write(job)


def list_all():
    ensure_defaults()
    defaults = _defaults()
    out = {}
    for cat in CATEGORIES:
        out[cat] = {}
        d = os.path.join(ROOT, cat)
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json"):
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    data = json.load(f)
                name = fn[:-5]
                # fill keys added in newer versions from the closest default
                base = next(iter(defaults[cat].values()))
                out[cat][name] = {**base, **data}
                if cat == "process" and "compatible_nozzle" not in data:
                    out[cat][name]["compatible_nozzle"] = 0.4      # presets from before 0.5 were all for 0.4
                out[cat][name]["_builtin"] = name in defaults[cat]
                if cat == "printer":
                    out[cat][name]["vendor"] = data.get("vendor") or printers_mod.vendor_of(data.get("model", name))
                    out[cat][name]["_form"] = printers_mod.form_of(data.get("model", ""))
                    if "gcode_flavor" not in data:          # presets from before 0.7.14
                        out[cat][name]["gcode_flavor"] = guess_flavor(out[cat][name])
                    if "host_type" not in data:             # presets from before 0.7.18
                        out[cat][name]["host_type"] = guess_host(out[cat][name])
                    if "extruder_clearance_radius" not in data:   # presets from before 0.8.2
                        r, rod = guess_clearance(out[cat][name])
                        out[cat][name].update(extruder_clearance_radius=r, extruder_clearance_height_to_rod=rod)
                    if "speed_factor" not in data:          # presets from before 0.7.16
                        out[cat][name]["speed_factor"] = guess_speed(out[cat][name])
    return out


def save(cat, name, data):
    if cat not in CATEGORIES:
        raise ValueError(cat)
    data = {k: v for k, v in data.items() if not k.startswith("_")}
    data["preset_version"] = PRESET_VERSION      # saved by the user: never auto-refreshed
    p = os.path.join(ROOT, cat, _safe(name) + ".json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return _safe(name)


KEEP_ON_RESET = ("ip", "serial", "access_code", "template_3mf", "host_type", "host", "api_key", "host_password")   # the user's own printer details


def reset(cat, name):
    """Restore a built-in preset to its factory values (keeps the printer connection and template)."""
    defaults = _defaults()[cat]
    if name not in defaults:
        raise ValueError("Only built-in presets have defaults to restore")
    p = os.path.join(ROOT, cat, _safe(name) + ".json")
    data = dict(defaults[name])
    if cat == "printer" and os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                old = json.load(f)
            data.update({k: old[k] for k in KEEP_ON_RESET if old.get(k)})
        except (OSError, ValueError):
            pass
    with open(p, "w", encoding="utf-8") as f:
        json.dump({**data, "preset_version": PRESET_VERSION}, f, indent=2)


def delete(cat, name):
    if name in _defaults()[cat]:
        raise ValueError("Built-in presets can't be deleted (you can edit them)")
    os.remove(os.path.join(ROOT, cat, _safe(name) + ".json"))


def build_settings(printer, filament, process):
    """Merge three presets into a nonplanar.Settings."""
    s = Settings()
    names = {f.name for f in s.__dataclass_fields__.values()}
    for preset in (printer, filament, process):
        for k, v in preset.items():
            if k in names:
                setattr(s, k, type(getattr(s, k))(v) if getattr(s, k) is not None else v)
    for k, attr in (("filament_retraction_length", "retract_length"), ("filament_z_hop", "travel_lift"),
                    ("filament_retraction_speed", "retract_speed"), ("filament_deretraction_speed", "deretraction_speed")):
        v = filament.get(k, -1)
        if v is not None and float(v) >= 0:
            setattr(s, attr, float(v))
    apply_speed_limit(s)
    s.cone_center = None if process.get("auto_center", True) else (float(process["cone_cx"]), float(process["cone_cy"]))
    s.ramp_height = None if process.get("auto_ramp", True) else float(process["ramp_value"])
    s.center_on_bed = True
    return s
