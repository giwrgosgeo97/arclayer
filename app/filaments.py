"""Built-in filament presets: a base per material, plus brand filaments that adjust it.

Values are starting points taken from the makers' recommended ranges (tune them with the
Calibration menu: temperature tower, flow, pressure advance, max volumetric speed).
Max volumetric speed is for a standard 0.4 mm hotend; high-flow ("HF", "Hyper") filaments
are faster. Carbon / glass fibre filaments (-CF, -GF) need a hardened steel nozzle.
"""

# material: nozzle first/other, bed first/other, max volumetric speed mm3/s, density g/cm3, flow,
#           fan min/max %, fan off for first N layers, overhang fan %, min layer time s, pressure advance
BASE = {
    "PLA":     dict(n1=220, n=215, b1=60, b=60, mvs=15, dens=1.24, flow=0.98, fmin=100, fmax=100, off=1, ofan=100, slow=8, pa=0.03),
    "PLA+":    dict(n1=225, n=220, b1=60, b=60, mvs=15, dens=1.23, flow=0.98, fmin=100, fmax=100, off=1, ofan=100, slow=8, pa=0.03),
    "PLA Silk": dict(n1=225, n=220, b1=60, b=60, mvs=10, dens=1.24, flow=0.98, fmin=100, fmax=100, off=1, ofan=100, slow=8, pa=0.03),
    "PLA Matte": dict(n1=220, n=215, b1=60, b=60, mvs=15, dens=1.32, flow=0.98, fmin=100, fmax=100, off=1, ofan=100, slow=8, pa=0.03),
    "PLA-CF":  dict(n1=230, n=225, b1=60, b=60, mvs=12, dens=1.21, flow=0.98, fmin=100, fmax=100, off=1, ofan=100, slow=8, pa=0.03),
    "PETG":    dict(n1=245, n=240, b1=75, b=70, mvs=10, dens=1.27, flow=0.95, fmin=20, fmax=40, off=3, ofan=90, slow=12, pa=0.04),
    "PETG-CF": dict(n1=255, n=250, b1=75, b=70, mvs=8, dens=1.30, flow=0.95, fmin=20, fmax=40, off=3, ofan=90, slow=12, pa=0.04),
    "PCTG":    dict(n1=255, n=250, b1=80, b=80, mvs=10, dens=1.23, flow=0.95, fmin=10, fmax=30, off=3, ofan=70, slow=12, pa=0.04),
    "ABS":     dict(n1=255, n=250, b1=100, b=100, mvs=16, dens=1.04, flow=0.95, fmin=0, fmax=30, off=3, ofan=60, slow=12, pa=0.03),
    "ASA":     dict(n1=260, n=255, b1=100, b=100, mvs=16, dens=1.07, flow=0.95, fmin=0, fmax=30, off=3, ofan=60, slow=12, pa=0.03),
    "TPU":     dict(n1=230, n=225, b1=40, b=35, mvs=3.5, dens=1.21, flow=1.0, fmin=80, fmax=100, off=1, ofan=100, slow=8, pa=0.1),
    "PC":      dict(n1=270, n=265, b1=110, b=110, mvs=12, dens=1.20, flow=0.95, fmin=0, fmax=20, off=3, ofan=40, slow=15, pa=0.04),
    "PA":      dict(n1=275, n=270, b1=90, b=90, mvs=8, dens=1.14, flow=0.97, fmin=0, fmax=30, off=3, ofan=40, slow=12, pa=0.04),
    "PA-CF":   dict(n1=285, n=280, b1=90, b=90, mvs=8, dens=1.20, flow=0.97, fmin=0, fmax=20, off=3, ofan=40, slow=12, pa=0.04),
    "PET-CF":  dict(n1=285, n=280, b1=80, b=80, mvs=8, dens=1.29, flow=0.97, fmin=0, fmax=20, off=3, ofan=40, slow=12, pa=0.04),
    "HIPS":    dict(n1=245, n=240, b1=100, b=100, mvs=8, dens=1.04, flow=0.97, fmin=0, fmax=30, off=3, ofan=60, slow=12, pa=0.03),
    "PVA":     dict(n1=220, n=215, b1=60, b=60, mvs=4, dens=1.23, flow=0.97, fmin=50, fmax=100, off=1, ofan=100, slow=8, pa=0.05),
    "PP":      dict(n1=235, n=230, b1=90, b=85, mvs=6, dens=0.90, flow=0.97, fmin=0, fmax=30, off=3, ofan=60, slow=12, pa=0.05),
}
TYPES = list(BASE)

# (brand, name, material, colour, changes to the base)
FILAMENTS = [
    # Generic (one per material; Generic PLA / PETG / TPU are defined in profiles.py)
    ("Generic", "PLA+", "PLA+", "#60a5fa", {}),
    ("Generic", "PLA Silk", "PLA Silk", "#d4a017", {}),
    ("Generic", "PLA Matte", "PLA Matte", "#9ca3af", {}),
    ("Generic", "PLA-CF", "PLA-CF", "#3f3f46", {}),
    ("Generic", "PETG-CF", "PETG-CF", "#27272a", {}),
    ("Generic", "PCTG", "PCTG", "#a7f3d0", {}),
    ("Generic", "ABS", "ABS", "#ef4444", {}),
    ("Generic", "ASA", "ASA", "#f5f5f4", {}),
    ("Generic", "PC", "PC", "#e5e7eb", {}),
    ("Generic", "PA (Nylon)", "PA", "#fef3c7", {}),
    ("Generic", "PA-CF", "PA-CF", "#18181b", {}),
    ("Generic", "PET-CF", "PET-CF", "#1f2937", {}),
    ("Generic", "HIPS", "HIPS", "#fafafa", {}),
    ("Generic", "PVA", "PVA", "#fef9c3", {}),
    ("Generic", "PP", "PP", "#f1f5f9", {}),
    # Bambu Lab
    ("Bambu", "PLA Matte", "PLA Matte", "#e8e4dc", dict(n1=220, n=220, b1=65, b=65, mvs=22)),
    ("Bambu", "PLA Silk+", "PLA Silk", "#c0c0c0", dict(n1=230, n=230, b1=65, b=65, mvs=12)),
    ("Bambu", "PLA Tough+", "PLA+", "#4b5563", dict(n1=230, n=230, b1=65, b=65, mvs=21)),
    ("Bambu", "PLA-CF", "PLA-CF", "#2d2d2d", dict(n1=230, n=230, b1=65, b=65, mvs=15)),
    ("Bambu", "PLA Basic Gradient", "PLA", "#a855f7", dict(n1=220, n=220, b1=65, b=65, mvs=21)),
    ("Bambu", "PETG Basic", "PETG", "#f8fafc", dict(n1=255, n=255, b1=70, b=70, mvs=13)),
    ("Bambu", "PETG HF", "PETG", "#111827", dict(n1=240, n=240, b1=70, b=70, mvs=21)),
    ("Bambu", "PETG Translucent", "PETG", "#bae6fd", dict(n1=250, n=250, b1=70, b=70, mvs=12)),
    ("Bambu", "PETG-CF", "PETG-CF", "#1c1917", dict(n1=255, n=255, b1=70, b=70, mvs=12)),
    ("Bambu", "ABS", "ABS", "#dc2626", dict(n1=270, n=270, b1=90, b=90, mvs=20)),
    ("Bambu", "ASA", "ASA", "#f5f5f5", dict(n1=270, n=270, b1=90, b=90, mvs=18)),
    ("Bambu", "TPU 95A HF", "TPU", "#0ea5e9", dict(n1=230, n=230, b1=35, b=35, mvs=12)),
    ("Bambu", "PC", "PC", "#f3f4f6", dict(n1=270, n=270, b1=100, b=100, mvs=16)),
    ("Bambu", "PAHT-CF", "PA-CF", "#1f1f1f", dict(n1=290, n=290, b1=80, b=80, mvs=10)),
    ("Bambu", "Support for PLA", "PLA", "#e5e5e5", dict(n1=220, n=220, b1=65, b=65, mvs=8)),
    # Polymaker
    ("Polymaker", "PolyTerra PLA", "PLA Matte", "#6b8e23", dict(n1=215, n=210, mvs=15)),
    ("Polymaker", "PolyLite PLA", "PLA", "#2563eb", dict(n1=215, n=210)),
    ("Polymaker", "PolyLite PLA Pro", "PLA+", "#1e3a8a", dict(n1=220, n=215)),
    ("Polymaker", "Panchroma Silk", "PLA Silk", "#eab308", {}),
    ("Polymaker", "PolyLite PETG", "PETG", "#0f766e", dict(n1=240, n=235)),
    ("Polymaker", "PolyLite ABS", "ABS", "#b91c1c", dict(n1=255, n=250, b1=100, b=100)),
    ("Polymaker", "PolyLite ASA", "ASA", "#f5f5f4", dict(n1=255, n=250, b1=100, b=100)),
    ("Polymaker", "PolyMax PC", "PC", "#e5e7eb", dict(n1=265, n=260, b1=100, b=100)),
    ("Polymaker", "PolyFlex TPU95", "TPU", "#f97316", dict(n1=220, n=215)),
    # Prusament
    ("Prusament", "PLA", "PLA", "#f97316", dict(n1=215, n=215, b1=60, b=60)),
    ("Prusament", "PLA Blend", "PLA", "#22c55e", dict(n1=215, n=215, b1=60, b=60)),
    ("Prusament", "PETG", "PETG", "#ea580c", dict(n1=240, n=250, b1=85, b=90)),
    ("Prusament", "ASA", "ASA", "#262626", dict(n1=260, n=260, b1=105, b=110)),
    ("Prusament", "PC Blend", "PC", "#e5e7eb", dict(n1=275, n=275, b1=110, b=115)),
    ("Prusament", "PA11 Carbon Fiber", "PA-CF", "#171717", dict(n1=285, n=285, b1=100, b=100)),
    # eSUN
    ("eSUN", "PLA+", "PLA+", "#ffffff", dict(n1=215, n=210)),
    ("eSUN", "PLA Matte", "PLA Matte", "#4b5563", {}),
    ("eSUN", "Silk PLA", "PLA Silk", "#d97706", {}),
    ("eSUN", "PETG", "PETG", "#3b82f6", dict(n1=240, n=235)),
    ("eSUN", "ABS+", "ABS", "#111111", dict(n1=245, n=240, b1=100, b=95)),
    ("eSUN", "eTPU-95A", "TPU", "#111111", dict(n1=225, n=220)),
    # Sunlu
    ("Sunlu", "PLA", "PLA", "#ffffff", dict(n1=215, n=210)),
    ("Sunlu", "PLA+", "PLA+", "#1d4ed8", dict(n1=220, n=215)),
    ("Sunlu", "PLA Meta", "PLA", "#10b981", dict(n1=215, n=210, mvs=18)),
    ("Sunlu", "Silk PLA+", "PLA Silk", "#facc15", {}),
    ("Sunlu", "PETG", "PETG", "#64748b", dict(n1=240, n=235)),
    # Elegoo
    ("Elegoo", "PLA", "PLA", "#f4f4f5", dict(n1=215, n=210)),
    ("Elegoo", "PLA+", "PLA+", "#0f172a", dict(n1=220, n=215)),
    ("Elegoo", "Rapid PLA+", "PLA+", "#2563eb", dict(n1=220, n=220, mvs=20)),
    ("Elegoo", "PETG", "PETG", "#16a34a", dict(n1=240, n=235)),
    ("Elegoo", "Rapid PETG", "PETG", "#0d9488", dict(n1=245, n=245, mvs=18)),
    # Creality
    ("Creality", "Hyper PLA", "PLA", "#e11d48", dict(n1=220, n=220, mvs=23)),
    ("Creality", "Ender PLA", "PLA", "#f8fafc", dict(n1=210, n=205)),
    ("Creality", "CR-PLA", "PLA", "#22c55e", dict(n1=210, n=205)),
    ("Creality", "Hyper PETG", "PETG", "#0284c7", dict(n1=240, n=240, mvs=18)),
    ("Creality", "Hyper ABS", "ABS", "#1f2937", dict(n1=260, n=260, mvs=20)),
    ("Creality", "CR-TPU", "TPU", "#ea580c", dict(n1=220, n=215)),
    # Overture
    ("Overture", "PLA", "PLA", "#f8fafc", dict(n1=210, n=205)),
    ("Overture", "PLA Professional", "PLA+", "#374151", dict(n1=215, n=210)),
    ("Overture", "Matte PLA", "PLA Matte", "#7c3aed", {}),
    ("Overture", "PETG", "PETG", "#06b6d4", dict(n1=240, n=235)),
    ("Overture", "TPU", "TPU", "#dc2626", dict(n1=215, n=210)),
    # Others
    ("Hatchbox", "PLA", "PLA", "#facc15", dict(n1=210, n=205)),
    ("Hatchbox", "PETG", "PETG", "#9333ea", dict(n1=240, n=235)),
    ("Jayo", "PLA", "PLA", "#f472b6", dict(n1=210, n=205)),
    ("Jayo", "PLA+", "PLA+", "#38bdf8", dict(n1=215, n=210)),
    ("Anycubic", "PLA", "PLA", "#f8fafc", dict(n1=210, n=205)),
    ("Anycubic", "High Speed PLA", "PLA", "#ef4444", dict(n1=215, n=215, mvs=20)),
    ("Anycubic", "PETG", "PETG", "#22c55e", dict(n1=240, n=235)),
    ("Geeetech", "PLA", "PLA", "#60a5fa", dict(n1=210, n=205)),
    ("Kingroon", "PLA", "PLA", "#f97316", dict(n1=210, n=205)),
    ("Fiberlogy", "Easy PLA", "PLA", "#84cc16", dict(n1=215, n=210)),
    ("Fiberlogy", "Easy PETG", "PETG", "#0ea5e9", dict(n1=240, n=235)),
    ("ColorFabb", "PLA PHA", "PLA", "#16a34a", dict(n1=215, n=210)),
    ("ColorFabb", "nGen", "PETG", "#f1f5f9", dict(n1=240, n=240, b1=80, b=80)),
    ("Extrudr", "PLA NX2", "PLA", "#0f172a", dict(n1=220, n=215)),
    ("FormFutura", "EasyFil PLA", "PLA", "#f43f5e", dict(n1=215, n=210)),
    ("Spectrum", "PLA Premium", "PLA", "#facc15", dict(n1=215, n=210)),
    ("Ninjatek", "NinjaFlex 85A", "TPU", "#111111", dict(n1=230, n=230, mvs=2)),
]


def name(brand, fname):
    return f"{brand} {fname}"


def preset(material, colour, changes, base_fields):
    v = {**BASE[material], **changes}
    return {**base_fields, "type": material, "color": colour, "filament_density": v["dens"], "flow": v["flow"],
            "max_volumetric_speed": v["mvs"], "nozzle_temp_first": v["n1"], "nozzle_temp": v["n"],
            "bed_temp_first": v["b1"], "bed_temp": v["b"], "fan_min_speed": v["fmin"], "fan_max_speed": v["fmax"],
            "close_fan_first_layers": v["off"], "overhang_fan_speed": v["ofan"], "slow_down_layer_time": v["slow"],
            "pressure_advance": v["pa"]}
