"""Command line:  python -m nonplanar model.stl [options]"""
import argparse
import os
import sys
import time

from .config import Settings
from .slicer import slice_model
from .gcode import write_gcode
from .collision import check
from .preview import write_preview
from . import bambu


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nonplanar", description="Conical non-planar slicer for 3D printers")
    ap.add_argument("model", help="STL / OBJ / 3MF model")
    ap.add_argument("-o", "--out", help="output name without extension (default: next to the model)")
    ap.add_argument("--config", help="JSON settings file (see settings.example.json)")
    ap.add_argument("--template", help="a Bambu .gcode.3mf sliced for your printer - gives the real start/end G-code "
                                       "and lets us write a printable .gcode.3mf")
    ap.add_argument("--angle", type=float, help="cone angle in degrees (+ outward overhangs, - inward)")
    ap.add_argument("--center", type=float, nargs=2, metavar=("X", "Y"), help="cone axis position on the bed")
    ap.add_argument("--ramp", type=float, help="height over which layers go from flat to conical (mm)")
    ap.add_argument("--layer", type=float, help="layer height (mm)")
    ap.add_argument("--infill", type=float, help="infill density 0-1")
    ap.add_argument("--walls", type=int, help="wall count")
    ap.add_argument("--support", action="store_true", help="generate supports for what the cone can't print")
    ap.add_argument("--support-plate-only", action="store_true", help="supports only where they stand on the bed")
    ap.add_argument("--no-preview", action="store_true")
    a = ap.parse_args(argv)

    s = Settings.load(a.config) if a.config else Settings()
    for key, val in (("cone_angle", a.angle), ("ramp_height", a.ramp), ("layer_height", a.layer),
                     ("infill_density", a.infill), ("wall_count", a.walls)):
        if val is not None:
            setattr(s, key, val)
    if a.support or a.support_plate_only:
        s.enable_support = True
        s.support_on_build_plate_only = a.support_plate_only
    if a.center:
        s.cone_center = tuple(a.center)

    out = a.out or os.path.splitext(a.model)[0] + "_nonplanar"
    t0 = time.time()
    mesh, tf, layers = slice_model(a.model, s)

    start = end = None
    if a.template:
        start, end = bambu.read_template(a.template)
        print(f"Using start/end G-code from {a.template}")

    text, preview, stats = write_gcode(layers, s, tf, start, end)
    result = check(preview, s)

    with open(out + ".gcode", "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"Wrote {out}.gcode")
    if a.template and a.template.lower().endswith(".3mf"):
        bambu.write_3mf(a.template, text, out + ".gcode.3mf")
        print(f"Wrote {out}.gcode.3mf  (send this to the printer)")
    if not a.no_preview:
        info = (f"{os.path.basename(a.model)}  |  conical {tf.angle:+.1f} deg  |  ramp {tf.H:.1f} mm  |  "
                f"{sum(1 for L in layers if L.paths)} layers\n"
                f"collision check: {'OK' if not result['hits'] else str(result['hits']) + ' warnings'}\n"
                f"drag = rotate, right-drag = pan, wheel = zoom")
        write_preview(preview, out + "_preview.html", info=info)
        print(f"Wrote {out}_preview.html")
    print(f"Done in {time.time() - t0:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
