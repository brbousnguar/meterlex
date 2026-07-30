"""Rasterise the Agentic Spend mark into favicon.ico + PNGs using Pillow.

Replicates frontend/public/brand-mark.svg by hand (no SVG renderer needed).
Drawn at high resolution then downsampled for crisp anti-aliasing.
"""
from PIL import Image, ImageDraw

ORANGE = (200, 98, 10, 255)
CREAM = (247, 240, 227, 255)

S = 30  # supersample factor (34px design -> 1020px)
W = 34 * S


def cream(alpha):
    return (CREAM[0], CREAM[1], CREAM[2], int(CREAM[3] * alpha))


def draw_mark(img):
    d = ImageDraw.Draw(img, "RGBA")
    # Background chip (rounded rect, rx=7 -> 7*S)
    d.rounded_rectangle([0, 0, 34 * S, 34 * S], radius=7 * S, fill=ORANGE)
    # Subtle inner highlight (top half)
    d.rounded_rectangle(
        [1 * S, 1 * S, 33 * S, 17 * S], radius=6 * S, fill=cream(0.07)
    )
    # Terminal chevron  (6,12)->(11,17)->(6,22)
    d.line(
        [(6 * S, 12 * S), (11 * S, 17 * S), (6 * S, 22 * S)],
        fill=CREAM, width=int(2.2 * S), joint="curve",
    )
    # round caps on chevron endpoints
    cap = int(2.2 * S / 2)
    for cx, cy in [(6 * S, 12 * S), (6 * S, 22 * S)]:
        d.ellipse([cx - cap, cy - cap, cx + cap, cy + cap], fill=CREAM)
    # Underscore cursor
    uw = int(2 * S)
    d.line([(13 * S, 22 * S), (16 * S, 22 * S)], fill=cream(0.7), width=uw)
    # Rising spend bars  (x,y,w,h, alpha)
    bars = [(18, 21, 3, 6, 0.55), (23, 16, 3, 11, 0.75), (28, 11, 3, 16, 0.95)]
    for x, y, w, h, a in bars:
        d.rounded_rectangle(
            [x * S, y * S, (x + w) * S, (y + h) * S], radius=int(1 * S),
            fill=cream(a),
        )
    # Trend line (dashed) connecting bar tops
    pts = [(19.5 * S, 21 * S), (24.5 * S, 16 * S), (29.5 * S, 11 * S)]
    for i in range(len(pts) - 1):
        seg = int(2 * S)
        gap = int(1 * S)
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        dx, dy = x1 - x0, y1 - y0
        dist = (dx * dx + dy * dy) ** 0.5
        steps = int(dist // (seg + gap)) + 1
        for s in range(steps):
            t0 = s * (seg + gap) / dist
            t1 = min(1.0, (s * (seg + gap) + seg) / dist)
            d.line(
                [(x0 + dx * t0, y0 + dy * t0), (x0 + dx * t1, y0 + dy * t1)],
                fill=cream(0.5), width=max(1, int(1 * S)),
            )


def main():
    hi = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    draw_mark(hi)
    hi.load()

    def resize(n):
        return hi.resize((n, n), Image.LANCZOS)

    # Multi-size .ico (Windows tab, taskbar, etc.)
    resize(128).save("public/favicon.ico", format="ICO",
                     sizes=[(16, 16), (32, 32), (64, 64), (128, 128)])
    # PNGs for explicit link sizes
    resize(32).save("public/favicon-32.png", format="PNG")
    resize(16).save("public/favicon-16.png", format="PNG")
    # Apple touch icon (180x180, opaque)
    apple = Image.new("RGBA", (180, 180), ORANGE)
    apple.paste(resize(180), (0, 0), resize(180))
    apple.convert("RGB").save("public/apple-touch-icon.png", format="PNG")
    print("wrote favicon.ico, favicon-32.png, favicon-16.png, apple-touch-icon.png")


if __name__ == "__main__":
    main()