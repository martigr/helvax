"""Draw the home screen icons (helvax/static/icons/*.png) from the logo in templates/_logo.html.

The app has no build step, so the PNGs are committed; run this only when the logo or the accent
colour changes. Needs Pillow, which is not in requirements-dev.txt:

    pip install pillow
    python scripts/icons.py

- icon-192.png, icon-512.png: rounded square for browsers that show the icon as is (manifest "any")
- icon-maskable-512.png: full-bleed, logo inside the inner 60 %, Android crops it to its own shape
- apple-touch-icon.png: 180 px, full-bleed and opaque, iOS rounds the corners itself
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'helvax' / 'static' / 'icons'

ACCENT = (0x1D, 0x5F, 0xD1, 255)  # --accent in style.css
WHITE = (255, 255, 255, 255)
SCALE = 4  # draw larger, then downscale for smooth edges


def draw_icon(size, logo_share, rounded):
    """Accent square with the white shield and cross of _logo.html (viewBox 24) in its centre."""
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

    # The shield: M12 2.5 l7.5 3 v6 c0 4.6 -3.2 8.6 -7.5 10 c-4.3 -1.4 -7.5 -5.4 -7.5 -10 v-6 z
    def curve(p0, p1, p2, p3, steps=24):
        out = []
        for i in range(steps + 1):
            t = i / steps
            out.append(tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t ** 2 * c + t ** 3 * e
                             for a, b, c, e in zip(p0, p1, p2, p3, strict=True)))
        return out
    right = curve((19.5, 11.5), (19.5, 16.1), (16.3, 20.1), (12, 21.5))
    left = curve((12, 21.5), (7.7, 20.1), (4.5, 16.1), (4.5, 11.5))
    line([(12, 2.5), (19.5, 5.5)] + right + left + [(4.5, 5.5), (12, 2.5)])
    line([(12, 8), (12, 15)])  # the cross
    line([(8.5, 11.5), (15.5, 11.5)])
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
