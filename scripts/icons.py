"""Draw the home screen icons (helvax/static/icons/*.png) from the logo in templates/_logo.html.

The app has no build step, so the PNGs are committed; run this only when the logo or the accent
colour changes. Needs Pillow, which is not in requirements-dev.txt:

    pip install pillow
    python scripts/icons.py

- icon-192.png, icon-512.png: rounded square for browsers that show the icon as is (manifest "any")
- icon-maskable-512.png: full-bleed, logo inside the inner 60 %, Android crops it to its own shape
- apple-touch-icon.png: 180 px, full-bleed and opaque, iOS rounds the corners itself
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'helvax' / 'static' / 'icons'

ACCENT = (0xC8, 0x10, 0x2E, 255)  # --accent in style.css
WHITE = (255, 255, 255, 255)
SCALE = 4  # draw larger, then downscale for smooth edges


def draw_icon(size, logo_share, rounded):
    """Accent square with the white syringe of _logo.html (viewBox 24) in its centre."""
    big = size * SCALE
    img = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 0.22, fill=ACCENT)
    else:
        d.rectangle((0, 0, big, big), fill=ACCENT)

    unit = big * logo_share / 24  # one unit of the 24×24 logo
    off = (big - 24 * unit) / 2

    def p(x, y):
        return off + x * unit, off + y * unit

    stroke = round(1.8 * unit)
    h = stroke / 2

    def line(points):
        """A polyline with round joins and caps, like the SVG stroke."""
        pts = [p(x, y) for x, y in points]
        d.line(pts, fill=WHITE, width=stroke, joint='curve')
        for x, y in pts:
            d.ellipse((x - h, y - h, x + h, y + h), fill=WHITE)

    # The syringe, drawn upright and turned by rotate(45 12 12) like the <g> in _logo.html
    def rot(x, y):
        c = math.sqrt(0.5)
        return 12 + (x - 12) * c - (y - 12) * c, 12 + (x - 12) * c + (y - 12) * c

    def stroke_path(points, closed=False):
        pts = [rot(x, y) for x, y in points]
        line(pts + pts[:2] if closed else pts)

    def rounded_rect(x, y, w, h, r, steps=6):
        out = []
        for cx, cy, a0 in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0),
                           (x + r, y + h - r, 90), (x + r, y + r, 180)):
            out += [(cx + r * math.cos(math.radians(a0 + 90 * i / steps)),
                     cy + r * math.sin(math.radians(a0 + 90 * i / steps))) for i in range(steps + 1)]
        return out

    stroke_path([(8.75, 0.5), (15.25, 0.5)])  # plunger handle
    stroke_path([(12, 0.5), (12, 4.5)])  # plunger rod
    stroke_path(rounded_rect(7.5, 4.5, 9, 13.5, 1.3), closed=True)  # barrel
    stroke_path([(12, 18), (12, 24)])  # needle
    for y in (8, 11, 14):  # graduation marks
        stroke_path([(7.5, y), (10.5, y)])
    return img.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    icons = {
        'icon-192.png': draw_icon(192, 0.62, rounded=True),
        'icon-512.png': draw_icon(512, 0.62, rounded=True),
        'icon-maskable-512.png': draw_icon(512, 0.56, rounded=False),
        'apple-touch-icon.png': draw_icon(180, 0.6, rounded=False).convert('RGB'),
    }
    for name, img in icons.items():
        img.save(OUT / name, optimize=True)
        print(f'{name}: {img.size[0]}×{img.size[1]}, {(OUT / name).stat().st_size} bytes')


if __name__ == '__main__':
    main()
