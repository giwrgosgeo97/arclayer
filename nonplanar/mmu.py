"""Multi-filament (AMS lite) helpers: flush volumes and the filament-change G-code.

Filament slots are 0-based inside the code (T0..T3), 1-based in the app.
"""
import colorsys
import math

from . import macro

# Used when there is no Bambu template, or the template has no change G-code.
# It switches filament but does NOT purge the old colour: use a template for real prints.
DEFAULT_CHANGE_GCODE = """; ---- generic filament change: no flushing. Set a Bambu template for the printer's real sequence ----
M620 S[next_extruder]A
M204 S9000
G1 Z{max_layer_z + 3.0} F1200
M400
M106 P1 S0
T[next_extruder]
M400
M621 S[next_extruder]A
M109 S[new_filament_temp]
"""

# nozzle temperature range per filament type (°C), for {nozzle_temperature_range_low/high}
TEMP_RANGE = {"PLA": (190, 240), "PETG": (230, 270), "ABS": (240, 280), "ASA": (240, 280), "TPU": (200, 250),
              "PLA-CF": (210, 250), "PETG-CF": (240, 270), "PA": (260, 300), "PC": (260, 300)}

MAX_FLUSH_COUNT = 4          # Bambu splits the flush into up to 4 parts ...
PURGE_ONE_TIME = 135.0       # ... of about this many mm³ each


def _rgb(hexcol):
    h = (hexcol or "#808080").lstrip("#")[:6]
    h = (h + "808080")[:6]
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def flush_volume(src_color, dst_color, multiplier=1.0):
    """mm³ of the new filament to push through before the old colour is gone.

    A colour-based estimate in the spirit of Bambu Studio's: going from dark to light
    needs much more than light to dark, and strongly different hues need extra."""
    r1, g1, b1 = _rgb(src_color)
    r2, g2, b2 = _rgb(dst_color)
    if (r1, g1, b1) == (r2, g2, b2):
        return 60.0 * multiplier
    lum = lambda r, g, b: 0.2126 * r + 0.7152 * g + 0.0722 * b
    l1, l2 = lum(r1, g1, b1), lum(r2, g2, b2)
    h1, _, s1 = colorsys.rgb_to_hls(r1, g1, b1)
    h2, _, s2 = colorsys.rgb_to_hls(r2, g2, b2)
    dh = min(abs(h1 - h2), 1 - abs(h1 - h2)) * 2           # 0..1
    hue = dh * min(s1, s2) * 180
    darker_to_lighter = max(0.0, l2 - l1)
    vol = 120 + 520 * darker_to_lighter ** 0.8 + hue + 60 * abs(l2 - l1)
    return float(min(800.0, vol) * multiplier)


def flush_parts(volume, fil_area):
    """Bambu's flush_length_1..4: the flush length in mm of filament, split in up to 4 parts."""
    length = volume / fil_area
    count = min(MAX_FLUSH_COUNT, max(1, int(round(volume / PURGE_ONE_TIME)))) if volume > 0 else 0
    parts = [length / count] * count + [0.0] * (MAX_FLUSH_COUNT - count)
    return length, parts


def change_env(tool_settings, filaments, prev, new, layer_num, layer_z, max_z, next_xyz, flush_vol, count):
    """Variables for the filament-change macro (names as Bambu Studio / Orca use them)."""
    n = len(tool_settings)
    ts = tool_settings
    types = [(f.get("type") or "PLA") for f in filaments]
    area = [math.pi * (s.filament_diameter / 2) ** 2 for s in ts]
    feed = [int(60 * s.max_volumetric_speed / a) for s, a in zip(ts, area)]
    hi = [TEMP_RANGE.get(t, (s.nozzle_temp - 20, s.nozzle_temp + 20))[1] for t, s in zip(types, ts)]
    lo = [TEMP_RANGE.get(t, (s.nozzle_temp - 20, s.nozzle_temp + 20))[0] for t, s in zip(types, ts)]
    flen, parts = flush_parts(flush_vol, area[new])
    prev_i = prev if prev is not None else new
    env = {
        "previous_extruder": prev if prev is not None else -1, "next_extruder": new,
        # current_extruder: the filament loaded now (the old one), as in Orca's change G-code
        "current_extruder": prev_i, "initial_extruder": new, "initial_tool": new, "_index": new,
        "layer_num": layer_num, "layer_z": layer_z, "max_layer_z": max_z, "toolchange_z": layer_z,
        "toolchange_count": count, "total_toolchanges": count,
        "x_after_toolchange": next_xyz[0], "y_after_toolchange": next_xyz[1], "z_after_toolchange": next_xyz[2],
        "old_filament_temp": ts[prev_i].nozzle_temp, "new_filament_temp": ts[new].nozzle_temp,
        "old_filament_e_feedrate": feed[prev_i], "new_filament_e_feedrate": feed[new],
        "old_retract_length": ts[prev_i].retract_length, "new_retract_length": ts[new].retract_length,
        "old_retract_length_toolchange": 2.0, "new_retract_length_toolchange": 2.0,
        "flush_length": flen, **{f"flush_length_{i + 1}": v for i, v in enumerate(parts)},
        "nozzle_temperature": [s.nozzle_temp for s in ts],
        "nozzle_temperature_initial_layer": [s.nozzle_temp_first for s in ts],
        "nozzle_temperature_range_high": hi, "nozzle_temperature_range_low": lo,
        "bed_temperature": [s.bed_temp for s in ts], "bed_temperature_initial_layer": [s.bed_temp_first for s in ts],
        "filament_type": types, "filament_colour": [f.get("color") or "#808080" for f in filaments],
        "filament_diameter": [s.filament_diameter for s in ts],
        "filament_max_volumetric_speed": [s.max_volumetric_speed for s in ts],
        "flush_volumetric_speeds": [s.max_volumetric_speed for s in ts],
        "flush_temperatures": hi,
        "retraction_length": [s.retract_length for s in ts], "retract_length_toolchange": [2.0] * n,
        "long_retractions_when_cut": [0] * n, "retraction_distances_when_cut": [18.0] * n,
        "filament_extruder_id": [0] * n, "nozzle_diameter": [ts[0].nozzle_diameter],
        "travel_point_1_x": 0, "travel_point_1_y": 0, "travel_point_2_x": 0, "travel_point_2_y": 0,
        "travel_point_3_x": 0, "travel_point_3_y": 0, "wipe_avoid_perimeter": 0, "wipe_avoid_pos_x": 0,
        "has_wipe_tower": False, "enable_prime_tower": False, "fan_speed": 0,
        "true": True, "false": False,
        # used by some printers' change G-code (A1: acceleration by layer; Elegoo: lift capped at the max height)
        "printable_height": float(ts[0].max_z), "max_print_height": float(ts[0].max_z),
        "default_acceleration": ts[new].default_acceleration or 5000,
        "initial_layer_acceleration": ts[new].initial_layer_acceleration or 500,
        "travel_acceleration": ts[new].travel_acceleration or 10000,
        "initial_layer_print_height": ts[0].first_layer_height, "layer_height": ts[0].layer_height,
        "outer_wall_volumetric_speed": min(ts[new].max_volumetric_speed or 12, ts[new].outer_wall_speed *
                                           ts[new].layer_height * ts[new].outer_wall_line_width),
        "travel_speed": ts[new].travel_speed, "z_hop": [s.travel_lift for s in ts],
    }
    return env


def render_change(template, env):
    return macro.render(template, env)
