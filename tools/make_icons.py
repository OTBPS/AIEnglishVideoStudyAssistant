"""Generate the app icon (.ico) and the extension icons (.png) from one drawing.

    uv run --project backend python tools/make_icons.py

Outputs are committed; rerun only when the drawing changes.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ICO = ROOT / "backend" / "videotutor" / "assets" / "app.ico"
EXT_ICONS = ROOT / "extension" / "icons"
BLUE = (37, 99, 235, 255)
WHITE = (255, 255, 255, 255)


def draw(size: int) -> Image.Image:
    """Play button inside a speech bubble: video + language. Drawn at 4x, downsampled."""
    big = size * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = big / 64
    d.rounded_rectangle((4 * s, 6 * s, 60 * s, 48 * s), radius=10 * s, fill=BLUE)
    d.polygon([(16 * s, 46 * s), (30 * s, 46 * s), (14 * s, 60 * s)], fill=BLUE)
    d.polygon([(25 * s, 15 * s), (25 * s, 39 * s), (44 * s, 27 * s)], fill=WHITE)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    ICO.parent.mkdir(parents=True, exist_ok=True)
    draw(256).save(ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    EXT_ICONS.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 48, 128):
        draw(size).save(EXT_ICONS / f"icon{size}.png")
    print(f"wrote {ICO.relative_to(ROOT)} and {EXT_ICONS.relative_to(ROOT)}/*.png")


if __name__ == "__main__":
    main()
