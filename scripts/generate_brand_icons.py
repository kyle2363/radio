"""Generate the 4 Radio brand icons as PNG files, with light + dark variants.

Compact redesign: all artwork fits within a central 60% safe area (y=36..144
in a 180x180 viewBox) with thicker strokes for 16x16 legibility. Background is
transparent so the icon adapts to whatever surface it sits on.

Outputs (16 PNGs):
  frontend/electron/icons/{name}-{mode}.png         (256x256, installer)
  frontend/electron/icons/{name}-{mode}-tray.png    (32x32,  system tray)
where {name} in {drift,orbit,pulse,spectrum}
      {mode} in {dark,light}
"""

import math
import os
from PIL import Image, ImageDraw

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "electron", "icons")
os.makedirs(OUT_DIR, exist_ok=True)

# ── Color palettes per concept, per mode ──────────────────────────
PALETTES = {
    "drift": {
        "dark":  {"main": (192, 132, 252), "ghost_alpha": 26},   # #c084fc
        "light": {"main": (124,  58, 237), "ghost_alpha": 26},   # #7c3aed
    },
    "orbit": {
        "dark":  {"main": ( 78, 204, 196), "rings": [(78, 204, 196, 51), (78, 204, 196, 140), (78, 204, 196, 255)]},
        "light": {"main": ( 14, 116, 144), "rings": [(14, 116, 144, 51), (14, 116, 144, 140), (14, 116, 144, 255)]},
    },
    "pulse": {
        "dark":  {"rings": [(245, 158, 11, 18), (251, 191, 36, 82), (253, 230, 138, 148), (253, 230, 138, 230)], "dot": (253, 230, 138)},
        "light": {"rings": [(180,  83,   9, 18), (217, 119,   6, 82), (245, 158,  11, 148), (180,  83,   9, 230)], "dot": (180, 83, 9)},
    },
    "spectrum": {
        "dark":  {"bar": (251, 113, 133)},   # #fb7185
        "light": {"bar": (190,  18,  60)},   # #be123c
    },
}

VIEW = 180  # Source viewBox dimension


def _pt(size, v):
    """Map a 180-unit viewBox value to the actual pixel grid."""
    return v * size / VIEW


def _draw_drift(img, draw, mode):
    p = PALETTES["drift"][mode]
    main = p["main"]
    ghost_a = p["ghost_alpha"]

    # Cubic wave: M 30 90 C 50 62, 70 62, 90 90 C 110 118, 130 118, 150 90
    pts = [
        (30, 90), (50, 62), (70, 62), (90, 90),
        (110, 118), (130, 118), (150, 90),
    ]

    # Ghost (thick, low-alpha) wave
    ghost = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(ghost)
    pixel_pts = [(_pt(img.size[0], x), _pt(img.size[1], y)) for x, y in pts]
    gd.line(pixel_pts, fill=(*main, ghost_a), width=max(1, int(_pt(img.size[0], 16))), joint="curve")
    img.alpha_composite(ghost)

    # Main wave
    main_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    md = ImageDraw.Draw(main_layer)
    md.line(pixel_pts, fill=(*main, 255), width=max(1, int(_pt(img.size[0], 5))), joint="curve")
    img.alpha_composite(main_layer)


def _draw_orbit(img, draw, mode):
    p = PALETTES["orbit"][mode]
    rings = p["rings"]
    size = img.size[0]
    cx = size / 2
    cy = size / 2

    # 2 concentric ellipses (rotated -22°) — the third one blurred to mud
    # at tray size, so the smallest is dropped. Strokes are thicker so the
    # design survives 16x16 downscale.
    for rx, ry, w, rgba in [
        (44, 15, 3.0, rings[0]),
        (26,  9, 4.0, rings[2]),
    ]:
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.ellipse(
            [cx - _pt(size, rx), cy - _pt(size, ry), cx + _pt(size, rx), cy + _pt(size, ry)],
            outline=rgba, width=max(1, int(_pt(size, w))),
        )
        layer = layer.rotate(-22, resample=Image.BICUBIC, center=(cx, cy))
        img.alpha_composite(layer)

    # Center dot — larger so it stays visible at 16x16
    r = max(1, int(_pt(size, 10)))
    cd = ImageDraw.Draw(img)
    cd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=p["main"])


def _draw_pulse(img, draw, mode):
    p = PALETTES["pulse"][mode]
    rings = p["rings"]
    dot = p["dot"]
    size = img.size[0]
    cx = size / 2
    cy = size / 2

    # 2 concentric rings (was 3) — extra ring blurred to a halo at 16x16.
    # Strokes are thick so the design reads at any size.
    for r_val, w, rgba in [
        (40, 3.0, rings[0]),
        (18, 3.5, rings[2]),
    ]:
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        rad = _pt(size, r_val)
        ld.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], outline=rgba, width=max(1, int(_pt(size, w))))
        img.alpha_composite(layer)

    # Center dot
    r = max(1, int(_pt(size, 6)))
    cd = ImageDraw.Draw(img)
    cd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=dot)


def _draw_spectrum(img, draw, mode):
    p = PALETTES["spectrum"][mode]
    bar = p["bar"]
    size = img.size[0]

    # 5 bars: width=18, gap=12, total=5*18+4*12=138, centered at x=21..159
    bars = [
        (21, 50), (51, 80), (81, 100), (111, 70), (141, 40),
    ]
    bar_w = _pt(size, 18)
    radius = bar_w / 2
    for x, h in bars:
        bx = _pt(size, x)
        top = _pt(size, 90) - _pt(size, h) / 2
        bottom = _pt(size, 90) + _pt(size, h) / 2
        draw.rounded_rectangle([bx, top, bx + bar_w, bottom], radius=radius, fill=bar)


DRAWERS = {
    "drift": _draw_drift,
    "orbit": _draw_orbit,
    "pulse": _draw_pulse,
    "spectrum": _draw_spectrum,
}


def render(name, mode, size, path):
    # RGBA so we get a transparent background
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    DRAWERS[name](img, draw, mode)
    img.save(path, "PNG")
    print(f"  wrote {path} ({size}x{size}, {mode})")


def main():
    for name in DRAWERS:
        for mode in ("dark", "light"):
            render(name, mode, 256, os.path.join(OUT_DIR, f"{name}-{mode}.png"))
            render(name, mode, 32,  os.path.join(OUT_DIR, f"{name}-{mode}-tray.png"))


if __name__ == "__main__":
    main()
