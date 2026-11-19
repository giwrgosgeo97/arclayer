"""Built-in printer models: bed size, height and extruder type, with a generic start/end G-code.

Sizes are the makers' printable volumes in mm. Delta printers (round beds, origin in the
middle) and 2.85 mm filament printers are left out: the slicer assumes a rectangular bed
with its origin in the front-left corner and 1.75 mm filament.

The Bambu Lab A1 and A1 mini are defined in profiles.py (with every nozzle size).
The start/end G-code here is only a fallback: printers listed in nonplanar/printer_gcode.json use
their maker's real sequence (see nonplanar/machine.py).
"""

# (vendor, model, bed x, bed y, max z, extruder: "direct" | "bowden")
PRINTERS = [
    # Bambu Lab (print them with a Bambu template, like the A1)
    ("Bambu Lab", "P1P", 256, 256, 256, "direct"),
    ("Bambu Lab", "P1S", 256, 256, 256, "direct"),
    ("Bambu Lab", "P2S", 256, 256, 256, "direct"),
    ("Bambu Lab", "X1 Carbon", 256, 256, 256, "direct"),
    ("Bambu Lab", "X1E", 256, 256, 256, "direct"),
    ("Bambu Lab", "H2D", 325, 320, 325, "direct"),
    # Prusa Research
    ("Prusa", "MK4S", 250, 210, 220, "direct"),
    ("Prusa", "MK4", 250, 210, 220, "direct"),
    ("Prusa", "MK3.5", 250, 210, 220, "direct"),
    ("Prusa", "MK3S+", 250, 210, 210, "direct"),
    ("Prusa", "MINI+", 180, 180, 180, "bowden"),
    ("Prusa", "CORE One", 250, 220, 270, "direct"),
    ("Prusa", "XL", 360, 360, 360, "direct"),
    # Creality
    ("Creality", "Ender-3", 220, 220, 250, "bowden"),
    ("Creality", "Ender-3 Pro", 220, 220, 250, "bowden"),
    ("Creality", "Ender-3 V2", 220, 220, 250, "bowden"),
    ("Creality", "Ender-3 V2 Neo", 220, 220, 250, "bowden"),
    ("Creality", "Ender-3 Max Neo", 300, 300, 320, "bowden"),
    ("Creality", "Ender-3 S1", 220, 220, 270, "direct"),
    ("Creality", "Ender-3 S1 Pro", 220, 220, 270, "direct"),
    ("Creality", "Ender-3 S1 Plus", 300, 300, 300, "direct"),
    ("Creality", "Ender-3 V3", 220, 220, 250, "direct"),
    ("Creality", "Ender-3 V3 SE", 220, 220, 250, "direct"),
    ("Creality", "Ender-3 V3 KE", 220, 220, 240, "direct"),
    ("Creality", "Ender-3 V3 Plus", 300, 300, 330, "direct"),
    ("Creality", "Ender-5", 220, 220, 300, "bowden"),
    ("Creality", "Ender-5 Pro", 220, 220, 300, "bowden"),
    ("Creality", "Ender-5 Plus", 350, 350, 400, "bowden"),
    ("Creality", "Ender-5 S1", 220, 220, 280, "direct"),
    ("Creality", "Ender-6", 250, 250, 400, "bowden"),
    ("Creality", "CR-10", 300, 300, 400, "bowden"),
    ("Creality", "CR-10 V3", 300, 300, 400, "direct"),
    ("Creality", "CR-10 S5", 500, 500, 500, "bowden"),
    ("Creality", "CR-10 SE", 220, 220, 265, "direct"),
    ("Creality", "CR-10 Smart Pro", 300, 300, 400, "direct"),
    ("Creality", "CR-M4", 450, 450, 470, "direct"),
    ("Creality", "K1", 220, 220, 250, "direct"),
    ("Creality", "K1C", 220, 220, 250, "direct"),
    ("Creality", "K1 SE", 220, 220, 250, "direct"),
    ("Creality", "K1 Max", 300, 300, 300, "direct"),
    ("Creality", "K2 Plus", 350, 350, 350, "direct"),
    ("Creality", "Hi", 260, 260, 300, "direct"),
    # Anycubic
    ("Anycubic", "Kobra 2 Neo", 220, 220, 250, "direct"),
    ("Anycubic", "Kobra 2", 220, 220, 250, "direct"),
    ("Anycubic", "Kobra 2 Pro", 220, 220, 250, "direct"),
    ("Anycubic", "Kobra 2 Plus", 320, 320, 400, "direct"),
    ("Anycubic", "Kobra 2 Max", 420, 420, 500, "direct"),
    ("Anycubic", "Kobra 3", 250, 250, 260, "direct"),
    ("Anycubic", "Kobra S1", 250, 250, 250, "direct"),
    ("Anycubic", "Vyper", 245, 245, 260, "direct"),
    ("Anycubic", "i3 Mega S", 210, 210, 205, "bowden"),
    # Elegoo
    ("Elegoo", "Neptune 3 Pro", 225, 225, 280, "direct"),
    ("Elegoo", "Neptune 3 Plus", 320, 320, 400, "direct"),
    ("Elegoo", "Neptune 3 Max", 420, 420, 500, "direct"),
    ("Elegoo", "Neptune 4", 225, 225, 265, "direct"),
    ("Elegoo", "Neptune 4 Pro", 225, 225, 265, "direct"),
    ("Elegoo", "Neptune 4 Plus", 320, 320, 385, "direct"),
    ("Elegoo", "Neptune 4 Max", 420, 420, 480, "direct"),
    ("Elegoo", "Centauri Carbon", 256, 256, 256, "direct"),
    ("Elegoo", "OrangeStorm Giga", 800, 800, 1000, "direct"),
    # Sovol
    ("Sovol", "SV01 Pro", 280, 240, 300, "direct"),
    ("Sovol", "SV06", 220, 220, 250, "direct"),
    ("Sovol", "SV06 Plus", 300, 300, 340, "direct"),
    ("Sovol", "SV07", 220, 220, 250, "direct"),
    ("Sovol", "SV07 Plus", 300, 300, 350, "direct"),
    ("Sovol", "SV08", 350, 350, 345, "direct"),
    # Voron (open source, Klipper)
    ("Voron", "0.2", 120, 120, 120, "direct"),
    ("Voron", "Trident 250", 250, 250, 250, "direct"),
    ("Voron", "Trident 300", 300, 300, 250, "direct"),
    ("Voron", "Trident 350", 350, 350, 250, "direct"),
    ("Voron", "2.4 250", 250, 250, 250, "direct"),
    ("Voron", "2.4 300", 300, 300, 300, "direct"),
    ("Voron", "2.4 350", 350, 350, 350, "direct"),
    ("Voron", "Switchwire", 250, 210, 240, "direct"),
    # RatRig
    ("RatRig", "V-Core 4 300", 300, 300, 300, "direct"),
    ("RatRig", "V-Core 4 400", 400, 400, 400, "direct"),
    ("RatRig", "V-Core 4 500", 500, 500, 500, "direct"),
    # Qidi
    ("Qidi", "Q1 Pro", 245, 245, 240, "direct"),
    ("Qidi", "Plus4", 305, 305, 280, "direct"),
    ("Qidi", "X-Plus 3", 280, 280, 270, "direct"),
    ("Qidi", "X-Max 3", 325, 325, 315, "direct"),
    ("Qidi", "X-Smart 3", 175, 180, 170, "direct"),
    # Artillery
    ("Artillery", "Sidewinder X1", 300, 300, 400, "direct"),
    ("Artillery", "Sidewinder X2", 300, 300, 400, "direct"),
    ("Artillery", "Sidewinder X4 Plus", 300, 300, 330, "direct"),
    ("Artillery", "Sidewinder X4 Pro", 240, 240, 260, "direct"),
    ("Artillery", "Genius Pro", 220, 220, 250, "direct"),
    # Flashforge
    ("Flashforge", "Adventurer 5M", 220, 220, 220, "direct"),
    ("Flashforge", "Adventurer 5M Pro", 220, 220, 220, "direct"),
    ("Flashforge", "AD5X", 220, 220, 220, "direct"),
    # Anker
    ("Anker", "AnkerMake M5", 235, 235, 250, "direct"),
    ("Anker", "AnkerMake M5C", 220, 220, 250, "direct"),
    # Snapmaker
    ("Snapmaker", "Artisan", 400, 400, 400, "direct"),
    ("Snapmaker", "2.0 A350", 320, 350, 330, "direct"),
    ("Snapmaker", "2.0 A250", 230, 250, 235, "direct"),
    # Others
    ("Tronxy", "X5SA", 330, 330, 400, "bowden"),
    ("Two Trees", "SK1", 256, 256, 256, "direct"),
    ("Kingroon", "KP3S Pro V2", 200, 200, 200, "direct"),
    ("Mingda", "Magician X2", 230, 230, 260, "direct"),
    # Your own printer: change the bed size under Printer settings
    ("Custom", "Marlin printer", 220, 220, 250, "direct"),
    ("Custom", "Klipper printer", 300, 300, 300, "direct"),
]

# what the printer looks like, for the picture in the printer list:
#   bedslinger: bed moves front-back, gantry on two posts (Ender-3, Prusa MK4)
#   cantilever: one post, the arm sticks out (A1 mini, Prusa MINI)
#   cube:       open frame box, bed moves down (P1P, Ender-5, Voron Switchwire is not one)
#   enclosed:   closed box with a door (X1, P1S, K1, Qidi)
FORMS = {
    "Bambu Lab A1": "bedslinger", "Bambu Lab A1 mini": "cantilever", "Bambu Lab P1P": "cube",
    "Bambu Lab P1S": "enclosed", "Bambu Lab P2S": "enclosed", "Bambu Lab X1 Carbon": "enclosed",
    "Bambu Lab X1E": "enclosed", "Bambu Lab H2D": "enclosed",
    "Prusa MINI+": "cantilever", "Prusa CORE One": "enclosed", "Prusa XL": "cube",
    "Creality Ender-5": "cube", "Creality Ender-5 Pro": "cube", "Creality Ender-5 Plus": "cube",
    "Creality Ender-5 S1": "cube", "Creality Ender-6": "enclosed", "Creality K1": "enclosed",
    "Creality K1C": "enclosed", "Creality K1 SE": "enclosed", "Creality K1 Max": "enclosed",
    "Creality K2 Plus": "enclosed",
    "Anycubic Kobra S1": "enclosed", "Elegoo Centauri Carbon": "enclosed", "Elegoo OrangeStorm Giga": "bedslinger",
    "Sovol SV08": "cube", "Voron 0.2": "enclosed", "Voron Trident 250": "enclosed", "Voron Trident 300": "enclosed",
    "Voron Trident 350": "enclosed", "Voron 2.4 250": "enclosed", "Voron 2.4 300": "enclosed",
    "Voron 2.4 350": "enclosed", "RatRig V-Core 4 300": "cube", "RatRig V-Core 4 400": "cube",
    "RatRig V-Core 4 500": "cube", "Qidi Q1 Pro": "enclosed", "Qidi Plus4": "enclosed", "Qidi X-Plus 3": "enclosed",
    "Qidi X-Max 3": "enclosed", "Qidi X-Smart 3": "enclosed", "Flashforge Adventurer 5M": "cube",
    "Flashforge Adventurer 5M Pro": "enclosed", "Flashforge AD5X": "cube", "Snapmaker Artisan": "enclosed",
    "Snapmaker 2.0 A350": "cube", "Snapmaker 2.0 A250": "cube", "Tronxy X5SA": "cube", "Two Trees SK1": "cube",
    "Kingroon KP3S Pro V2": "cantilever", "Custom Klipper printer": "cube",
}


def form_of(model):
    return FORMS.get(model, "bedslinger")


NOZZLES = (0.2, 0.4, 0.6, 0.8)
RETRACT_DIRECT = {0.2: 0.8, 0.4: 0.8, 0.6: 1.2, 0.8: 1.4}


VENDORS = sorted({p[0] for p in PRINTERS} | {"Bambu Lab"}, key=lambda v: (v == "Custom", v))

RETRACT = {"direct": (0.8, 40), "bowden": (5.0, 40)}      # length mm, speed mm/s (starting values)


def retract(ext, nozzle):
    """Retraction (length, speed) for an extruder type and nozzle size: starting values to tune."""
    if ext == "bowden":
        return 5.0, 40
    return RETRACT_DIRECT[nozzle], 40


def name(vendor, model, nozzle=0.4):
    return f"{vendor} {model} {nozzle:g} nozzle"


def start_gcode(vendor, model, bx, by):
    """Generic start: heat, home, purge line along the left edge. Works on Marlin, Klipper and Prusa firmware."""
    y1 = min(by - 15, 150)
    note = ("; Bambu printers: set a template under Printer > Machine G-code to print over the network.\n"
            if vendor == "Bambu Lab" else
            "; Generic start G-code. If your printer has its own start macro (Klipper PRINT_START,\n"
            "; Prusa's mesh bed levelling, Creality's auto-level), paste your slicer's start G-code here.\n")
    return (f"; {vendor} {model}\n" + note +
            "M140 S{bed_temp_first}\n"
            "M104 S150\n"
            "G28\n"
            "M190 S{bed_temp_first}\n"
            "M109 S{nozzle_temp_first}\n"
            "G90\n"
            "M83\n"
            "G92 E0\n"
            "G1 Z5 F3000\n"
            "G1 X5 Y15 F6000\n"
            "G1 Z0.3 F600\n"
            f"G1 X5 Y{y1} E{(y1 - 15) * 0.1:.1f} F1200\n"
            f"G1 X5.5 Y{y1}\n"
            f"G1 X5.5 Y15 E{(y1 - 15) * 0.08:.1f} F1500\n"
            "G92 E0\n"
            "G1 Z2 F3000\n")


def end_gcode(bx, by, bz):
    return ("G91\n"
            "G1 E-2 F2400\n"
            "G1 Z10 F600\n"
            "G90\n"
            "M104 S0\n"
            "M140 S0\n"
            "M106 S0\n"
            f"G1 X5 Y{by - 5} F6000\n"
            "M84\n")


def vendor_of(model):
    for v in sorted(VENDORS, key=len, reverse=True):
        if str(model).startswith(v):
            return v
    return "Custom"
