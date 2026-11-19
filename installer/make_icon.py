"""Draws the ArcLayer logo: curved (non-planar) layers, with the nozzle printing the top one.

Writes:
  installer/icon.ico      Windows icon (16 - 256 px)
  app/ui/logo.svg         the mark, for the app (header, intro, help)
  docs/logo.svg           mark + "ArcLayer" word, for the README
  docs/logo.png           512 px mark
One set of shapes drives every file, so they always match.
"""
import math
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

TILE_TOP, TILE_BOT = (30, 34, 42), (11, 38, 34)
LAYERS = [(0, 214, 180), (0, 178, 150), (0, 140, 120), (0, 104, 90)]   # top to bottom
NOZZLE, BLOCK, GLOW = (232, 236, 240), (255, 138, 61), (255, 196, 140)
FAINT = (70, 84, 92)
W_LINE = 17
NOZ_U = 0.42                     # where the nozzle is on the top layer (-1 .. 1)


def arc_y(k, u):
    """Layer k (0 = top): a dome, like curved layers printed over a rounded part."""
    return 128 + k * 27 - 50 * (1 - u * u) + 2


def arc_pts(k, u0=-1.0, u1=1.0, n=48):
    return [(128 + 86 * (u0 + (u1 - u0) * i / n), arc_y(k, u0 + (u1 - u0) * i / n)) for i in range(n + 1)]


def nozzle_shapes():
    """Hot end above the top layer: heat break, heater block, nozzle cone. Polygons in 256 px space."""
    x = 128 + 86 * NOZ_U
    tip = arc_y(0, NOZ_U) - W_LINE / 2 + 1
    return {
        "break": [(x - 9, 14), (x + 9, 14), (x + 9, 34), (x - 9, 34)],
        "block": [(x - 27, 32), (x + 27, 32), (x + 27, tip - 25), (x - 27, tip - 25)],
        "cone": [(x - 14, tip - 26), (x + 14, tip - 26), (x + 4, tip - 2), (x - 4, tip - 2)],
        "tip": (x, tip),
    }


# ------------------------------------------------------------------ SVG
def _poly(pts):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)


def svg_mark(size=256, with_tile=True):
    nz = nozzle_shapes()
    hexc = lambda c: "#%02x%02x%02x" % c
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="{size}" height="{size}">',
           '<defs><linearGradient id="t" x1="0" y1="0" x2="0" y2="1">'
           f'<stop offset="0" stop-color="{hexc(TILE_TOP)}"/><stop offset="1" stop-color="{hexc(TILE_BOT)}"/></linearGradient>'
           '<radialGradient id="g"><stop offset="0" stop-color="#ffc48c" stop-opacity=".9"/>'
           '<stop offset="1" stop-color="#ff8a3d" stop-opacity="0"/></radialGradient></defs>']
    if with_tile:
        out.append('<rect x="8" y="8" width="240" height="240" rx="56" fill="url(#t)"/>')
    for k in range(len(LAYERS) - 1, 0, -1):
        out.append(f'<polyline points="{_poly(arc_pts(k))}" fill="none" stroke="{hexc(LAYERS[k])}" '
                   f'stroke-width="{W_LINE}" stroke-linecap="round" stroke-linejoin="round"/>')
    out.append(f'<polyline points="{_poly(arc_pts(0, NOZ_U, 1))}" fill="none" stroke="{hexc(FAINT)}" '
               f'stroke-width="{W_LINE * .45:.1f}" stroke-linecap="round" stroke-dasharray="1 12"/>')
    out.append(f'<polyline points="{_poly(arc_pts(0, -1, NOZ_U))}" fill="none" stroke="{hexc(LAYERS[0])}" '
               f'stroke-width="{W_LINE}" stroke-linecap="round" stroke-linejoin="round"/>')
    tx, ty = nz["tip"]
    out.append(f'<circle cx="{tx:.1f}" cy="{ty + 3:.1f}" r="20" fill="url(#g)"/>')
    out.append(f'<polygon points="{_poly(nz["break"])}" fill="#9aa3ad"/>')
    out.append(f'<polygon points="{_poly(nz["block"])}" fill="{hexc(BLOCK)}"/>')
    out.append(f'<polygon points="{_poly(nz["cone"])}" fill="{hexc(NOZZLE)}"/>')
    out.append("</svg>")
    return "\n".join(out)


def svg_wordmark():
    mark = svg_mark(96).split("\n", 1)[1].rsplit("\n", 1)[0]
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 110" width="420" height="110">'
            f'<g transform="translate(4 7) scale(.375)">{mark}</g>'
            '<text x="112" y="72" font-family="Segoe UI, Inter, Arial, sans-serif" font-size="52" font-weight="700">'
            '<tspan fill="#00d6b4">Arc</tspan><tspan fill="#e9ecef">Layer</tspan></text></svg>')


# ------------------------------------------------------------------ bitmap (drawn 4x, then scaled down)
def bitmap(px):
    ss = 4
    S = px * ss
    f = S / 256
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TILE_TOP, TILE_BOT)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([8 * f, 8 * f, 248 * f, 248 * f], radius=56 * f, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    sc = lambda pts: [(x * f, y * f) for x, y in pts]

    def stroke(pts, col, w):
        p = sc(pts)
        d.line(p, fill=col + (255,), width=round(w * f), joint="curve")
        r = w * f / 2
        for x, y in (p[0], p[-1]):
            d.ellipse([x - r, y - r, x + r, y + r], fill=col + (255,))
    for k in range(len(LAYERS) - 1, 0, -1):
        stroke(arc_pts(k), LAYERS[k], W_LINE)
    if px >= 48:                                           # the dotted "still to print" part: too fine for small icons
        for x, y in arc_pts(0, NOZ_U + 0.12, 1, 6)[::1]:
            r = 2.6 * f
            d.ellipse([x * f - r, y * f - r, x * f + r, y * f + r], fill=FAINT + (255,))
    stroke(arc_pts(0, -1, NOZ_U), LAYERS[0], W_LINE)
    nz = nozzle_shapes()
    tx, ty = nz["tip"]
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    for i in range(20, 0, -1):
        a = int(120 * (1 - i / 20) ** 1.5)
        r = i * f
        gdraw.ellipse([tx * f - r, (ty + 3) * f - r, tx * f + r, (ty + 3) * f + r], fill=GLOW + (a,))
    img = Image.alpha_composite(img, glow)
    d = ImageDraw.Draw(img)
    d.polygon(sc(nz["break"]), fill=(154, 163, 173, 255))
    d.polygon(sc(nz["block"]), fill=BLOCK + (255,))
    d.polygon(sc(nz["cone"]), fill=NOZZLE + (255,))
    return img.resize((px, px), Image.LANCZOS)


def main():
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    frames = [bitmap(s) for s in sizes]
    frames[-1].save(os.path.join(HERE, "icon.ico"), sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    bitmap(512).save(os.path.join(ROOT, "docs", "logo.png"))
    bitmap(128).save(os.path.join(HERE, "logo.png"))               # the installer's header
    with open(os.path.join(ROOT, "app", "ui", "logo.svg"), "w", encoding="utf-8") as fh:
        fh.write(svg_mark())
    with open(os.path.join(ROOT, "docs", "logo.svg"), "w", encoding="utf-8") as fh:
        fh.write(svg_wordmark())
    print("logo written")


if __name__ == "__main__":
    main()
