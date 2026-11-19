"""All slicer settings in one place. Defaults: a 256 mm bed, 0.4 mm nozzle and PLA (printer presets change them).
Names follow OrcaSlicer where there is an Orca equivalent."""
from dataclasses import dataclass, asdict, fields
import json


@dataclass
class Settings:
    # --- printer ---
    bed_x: float = 256.0
    bed_y: float = 256.0
    max_z: float = 256.0
    nozzle_diameter: float = 0.4

    # --- head geometry, used by the collision checker (MEASURE YOUR HOTEND) ---
    # allowed rise above the nozzle tip at horizontal distance d:
    #   min(d * nozzle_cone_slope, nozzle_free_height) inside head_radius
    nozzle_cone_slope: float = 1.2
    nozzle_free_height: float = 3.0
    head_radius: float = 12.0
    # print by object: the whole toolhead around the nozzle, and the gantry rod above the nozzle tip
    extruder_clearance_radius: float = 65.0
    extruder_clearance_height_to_rod: float = 34.0
    print_sequence: str = "by_layer"        # by_layer | by_object (each part finished before the next)

    # --- retraction / travel (printer) ---
    retract_length: float = 0.8
    retract_speed: float = 30
    deretraction_speed: float = 0          # 0 = same as retraction speed
    retract_min_travel: float = 2.0        # mm; shorter travels don't retract
    retract_when_changing_layer: bool = True
    travel_lift: float = 0.6               # Z hop above the current curved layer while travelling
    retract_restart_extra: float = 0.0     # mm extra pushed back after a retraction
    wipe: bool = False                     # move back along the path while retracting
    wipe_distance: float = 1.0             # mm
    retract_lift_above: float = 0.0        # only Z hop above this height (0 = everywhere)
    retract_lift_below: float = 0.0        # only Z hop below this height (0 = everywhere)
    gcode_flavor: str = "marlin"           # marlin | klipper | reprapfirmware | bambu (jerk commands)
    speed_factor: float = 100.0            # % of Bambu-class speeds this printer prints well at (caps the process)
    min_layer_height: float = 0.07
    max_layer_height: float = 0.0          # 0 = 75% of the nozzle
    # machine limits (written to the G-code only when emit_machine_limits is on)
    emit_machine_limits: bool = False
    machine_max_speed_x: float = 500
    machine_max_speed_y: float = 500
    machine_max_speed_z: float = 12
    machine_max_speed_e: float = 30
    machine_max_acceleration_x: float = 10000
    machine_max_acceleration_y: float = 10000
    machine_max_acceleration_z: float = 500
    machine_max_acceleration_e: float = 5000
    machine_max_acceleration_extruding: float = 10000
    machine_max_acceleration_retracting: float = 5000
    machine_max_acceleration_travel: float = 10000
    machine_max_jerk_x: float = 9
    machine_max_jerk_y: float = 9
    before_layer_change_gcode: str = ""
    layer_change_gcode: str = ""

    # --- filament ---
    filament_diameter: float = 1.75
    filament_density: float = 1.24         # g/cm^3
    flow: float = 0.98                     # flow ratio
    max_volumetric_speed: float = 18.0     # mm^3/s
    filament_cost: float = 20.0            # money per kg
    filament_shrink: float = 100.0         # % (XY): below 100 the part is printed bigger to make up for shrinking
    chamber_temperature: int = 0           # 0 = don't set
    nozzle_temperature_range_low: int = 190
    nozzle_temperature_range_high: int = 240
    # bed temperature per build plate, 0 = the filament's normal bed temperature (or the app's plate rule)
    textured_plate_temp: int = 0
    textured_plate_temp_initial_layer: int = 0
    cool_plate_temp: int = 0
    cool_plate_temp_initial_layer: int = 0
    supertack_plate_temp: int = 0
    supertack_plate_temp_initial_layer: int = 0
    eng_plate_temp: int = 0
    eng_plate_temp_initial_layer: int = 0
    hot_plate_temp: int = 0
    hot_plate_temp_initial_layer: int = 0
    # filament overrides of the printer's retraction, -1 = use the printer's
    filament_retraction_length: float = -1
    filament_z_hop: float = -1
    filament_retraction_speed: float = -1
    filament_deretraction_speed: float = -1
    filament_start_gcode: str = ""
    filament_end_gcode: str = ""
    enable_pressure_advance: bool = False
    pressure_advance: float = 0.02
    nozzle_temp: int = 220
    bed_temp: int = 65
    nozzle_temp_first: int = 220
    bed_temp_first: int = 65
    # cooling (Orca logic: fan between min and max by layer time, slow down very short layers)
    close_fan_first_layers: int = 1
    full_fan_speed_layer: int = 0          # fan ramps up until this layer (0 = straight to full)
    reduce_fan_stop_start_freq: bool = True   # keep the fan at min speed instead of stopping it
    dont_slow_down_outer_wall: bool = False
    additional_cooling_fan_speed: int = 0  # %, auxiliary part fan (M106 P2)
    fan_min_speed: int = 100               # %
    fan_max_speed: int = 100               # %
    fan_cooling_layer_time: float = 60     # s; layers faster than this get more than min fan
    slow_down_for_layer_cooling: bool = True
    slow_down_layer_time: float = 6        # s; layers faster than this are slowed down (fan at max)
    slow_down_min_speed: float = 20        # mm/s
    overhang_fan_speed: int = 100          # %, for overhang walls and bridges
    overhang_fan_threshold: float = 0.25   # overhang fraction of the line width that triggers it

    # --- non-planar transform ---
    mode: str = "conical"
    cone_angle: float = 15.0               # + outward overhangs, - inward overhangs
    cone_center: tuple = None              # (x, y) on the bed; None = centre of the part
    ramp_height: float = None              # mm from flat to conical; None = auto
    min_layer_ratio: float = 0.6
    curve_tolerance: float = 0.01          # mm the bent-back path may deviate from the curved layer
    collision_avoidance: str = "lower_angle"  # lower_angle | warn: what to do when the head would hit the print
    surface_depth: float = 8.0             # surface mode: most height difference of the top surface followed (mm)
    surface_max_angle: float = 0.0         # surface mode: steepest slope followed (deg); 0 = what the toolhead allows

    # --- quality: layers and line widths ---
    layer_height: float = 0.2
    first_layer_height: float = 0.2
    line_width: float = 0.42               # default
    first_layer_line_width: float = 0.5
    outer_wall_line_width: float = 0.42
    inner_wall_line_width: float = 0.45
    top_surface_line_width: float = 0.42
    solid_infill_line_width: float = 0.42
    sparse_infill_line_width: float = 0.45
    support_line_width: float = 0.42
    # seam
    seam_position: str = "aligned"         # aligned | nearest | back | random
    seam_gap: float = 10.0                 # % of the nozzle: loops stop this short of their start
    # precision
    resolution: float = 0.012              # mm, outline simplification
    xy_hole_compensation: float = 0.0      # mm, + makes holes bigger
    xy_contour_compensation: float = 0.0   # mm, + makes the outside bigger
    elefant_foot_compensation: float = 0.075  # mm the first layer is shrunk
    # ironing
    ironing_type: str = "no ironing"       # no ironing | top | topmost | solid
    ironing_pattern: str = "rectilinear"   # rectilinear | concentric
    ironing_flow: float = 0.10
    ironing_spacing: float = 0.15
    ironing_inset: float = 0.0
    ironing_speed: float = 30
    ironing_angle: float = -1.0            # degrees; -1 = across the top surface lines
    # walls and surfaces
    wall_sequence: str = "inner_outer"     # inner_outer | outer_inner | inner_outer_inner
    is_infill_first: bool = False
    only_one_wall_top: bool = False
    only_one_wall_first_layer: bool = False
    # bridging
    bridge_flow: float = 1.0
    thick_bridges: bool = True             # round, nozzle-wide bridge strands
    bridge_angle: float = 0.0              # 0 = automatic (across the short span)
    print_flow_ratio: float = 1.0          # whole-object flow, on top of the filament's flow ratio
    first_layer_flow_ratio: float = 1.0    # the first layer only
    top_surface_flow_ratio: float = 1.0
    bottom_surface_flow_ratio: float = 1.0
    # overhangs
    enable_overhang_speed: bool = True

    # --- strength ---
    wall_count: int = 3
    top_layers: int = 4
    top_shell_thickness: float = 0.8
    bottom_layers: int = 4
    bottom_shell_thickness: float = 0.0
    top_surface_pattern: str = "monotonicline"
    bottom_surface_pattern: str = "monotonic"
    internal_solid_infill_pattern: str = "rectilinear"
    infill_density: float = 0.15
    infill_pattern: str = "grid"
    infill_direction: float = 45.0
    solid_infill_direction: float = 45.0
    top_surface_density: float = 1.0
    bottom_surface_density: float = 1.0
    infill_overlap: float = 0.15           # fraction of line width infill overlaps the inner wall
    minimum_sparse_infill_area: float = 15.0  # mm^2; smaller sparse areas are printed solid
    solid_infill_every_layers: int = 0     # every Nth layer the sparse infill is solid (0 = never)
    alternate_extra_wall: bool = False     # one more wall on every other layer (stronger walls)
    lightning_angle: float = 45.0          # lightning infill: how far it may lean (degrees from vertical)

    # --- speed (mm/s), kept moderate for non-planar ---
    first_layer_speed: float = 30          # initial layer walls
    first_layer_infill_speed: float = 50   # initial layer infill
    slow_down_layers: int = 0              # ramp from first-layer speed over this many layers
    outer_wall_speed: float = 50
    inner_wall_speed: float = 70
    small_perimeter_speed: float = 0.5     # fraction of the outer wall speed
    small_perimeter_threshold: float = 0.0 # mm radius; 0 = off
    solid_speed: float = 70                # internal solid infill
    top_surface_speed: float = 50
    infill_speed: float = 100
    bridge_speed: float = 30
    overhang_1_4_speed: float = 0          # 0 = no slow-down for this overhang amount
    overhang_2_4_speed: float = 50
    overhang_3_4_speed: float = 30
    overhang_4_4_speed: float = 10
    support_speed: float = 80
    support_interface_speed: float = 50
    travel_speed: float = 200
    initial_layer_travel_speed: float = 0  # 0 = same as travel
    skirt_speed: float = 0                 # 0 = initial layer speed
    # acceleration (mm/s^2), 0 = use the default acceleration
    default_acceleration: float = 4000     # 0 = never send M204
    outer_wall_acceleration: float = 2000
    inner_wall_acceleration: float = 0
    top_surface_acceleration: float = 2000
    sparse_infill_acceleration: float = 0
    initial_layer_acceleration: float = 500
    travel_acceleration: float = 6000
    bridge_acceleration: float = 0
    internal_solid_infill_acceleration: float = 0
    # jerk (mm/s), 0 = don't send
    default_jerk: float = 0
    outer_wall_jerk: float = 0
    inner_wall_jerk: float = 0
    infill_jerk: float = 0
    top_surface_jerk: float = 0
    initial_layer_jerk: float = 0
    travel_jerk: float = 0

    # --- support (normal/grid, computed against the curved layers) ---
    enable_support: bool = False
    support_on_build_plate_only: bool = False
    support_threshold_angle: float = 30.0
    support_top_z_distance: float = 0.2
    support_xy_distance: float = 0.35
    support_spacing: float = 2.5
    support_angle: float = 0.0
    support_base_pattern: str = "rectilinear"       # rectilinear | rectilinear-grid | honeycomb | hollow
    support_interface_layers: int = 2
    support_interface_spacing: float = 0.5
    support_interface_pattern: str = "rectilinear"  # rectilinear | grid | concentric
    support_expansion: float = 0.0                  # mm the support area is grown
    support_remove_small_overhang: bool = True
    support_type: str = "normal"                    # normal | tree
    support_style: str = "default"                  # normal: default | grid;  tree: tree_slim | tree_strong | tree_hybrid | organic
    support_bottom_z_distance: float = 0.2          # gap where support stands on the part
    support_bottom_interface_layers: int = 0        # dense layers where support stands on the part
    support_wall_count: int = 0                     # walls around support areas (tree: around branches)
    tree_support_branch_angle: float = 40.0         # how far branches may lean (degrees from vertical)
    tree_support_branch_distance: float = 5.0       # mm between branch tips under an overhang
    tree_support_branch_diameter: float = 5.0       # mm, thickest a branch gets
    tree_support_tip_diameter: float = 0.8          # mm, where a branch touches the part
    tree_support_branch_diameter_angle: float = 5.0 # how fast branches get thicker going down (degrees)

    # --- others ---
    skirt_loops: int = 1
    skirt_distance: float = 3.0
    skirt_height: int = 1
    brim_type: str = "no_brim"             # no_brim | outer_only | inner_only | outer_and_inner
    brim_width: float = 5.0
    brim_object_gap: float = 0.1
    reduce_infill_retraction: bool = True
    brim_ears_max_angle: float = 125.0     # brim ears: corners sharper than this get an ear
    fuzzy_skin: str = "none"               # none | external | all
    fuzzy_skin_thickness: float = 0.3      # mm the wall moves in and out
    fuzzy_skin_point_distance: float = 0.8 # mm between fuzzy points

    # --- multimaterial (AMS). Filament numbers are 1-based; 0 = the object's own filament ---
    wall_filament: int = 0
    sparse_infill_filament: int = 0
    solid_infill_filament: int = 0
    support_filament: int = 0              # 0 = whichever filament is loaded at that layer
    support_interface_filament: int = 0
    flush_multiplier: float = 1.0          # scales the colour-based flush volumes
    prime_tower: str = "auto"              # auto (several filaments, printer without a purge chute) | on | off
    prime_tower_width: float = 35.0
    prime_tower_x: float = -1.0            # front-left corner; -1 = placed automatically
    prime_tower_y: float = -1.0
    prime_volume: float = 45.0             # mm3 purged in the tower at least, per change
    prime_tower_speed: float = 90.0

    # --- placement ---
    center_on_bed: bool = True

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown settings in {path}: {sorted(unknown)}")
        if data.get("cone_center") is not None:
            data["cone_center"] = tuple(data["cone_center"])
        return cls(**data)

    def to_json(self):
        return json.dumps(asdict(self), indent=2)
