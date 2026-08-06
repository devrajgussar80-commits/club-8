"""Write the supplied Club 8 banners into the home carousel's asset folder.

Every slide must be the same shape -- the carousel is one fixed rectangle and
the artwork arrived at three different ratios (1.50, 1.60, 1.78). Cropping them
all to a common ratio would cut the "JOIN CLUB 8 NOW" bar off the bottom of the
squarer ones, which is the part of a promo banner that has to survive.

So each is fitted whole onto a 16:9 canvas, over a blurred, zoomed copy of
itself. Nothing is clipped, every file comes out the same size, and the filler
reads as part of the artwork rather than as bars.
"""

import io
import os

from PIL import Image, ImageFilter

# Where the supplied artwork was read from. Point this at wherever the new
# banners are when they are replaced.
SRC = os.path.expanduser("~/Downloads")
OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "banners"
)

# 16:9 -- the ratio most of the artwork already is, so most slides need no
# filler at all.
CANVAS = (1200, 675)
QUALITY = 84

# In display order. The private-jet hero leads.
BANNERS = [
    ("file_000000000dd48208a5be99ef47a2e163.png", "hero-1"),  # Private play
    ("file_00000000dd3c8208857a6fbedc99552a.png", "hero-2"),  # Gold throne
    ("file_0000000000108211acdc24955ab053f8.png", "hero-3"),  # VIP jet
    ("file_0000000002e08208ba4440ec9a05bae2.png", "hero-4"),  # Champions
    ("file_0000000098d4821194f95a31600db389.png", "hero-5"),  # Infinity jackpot
    ("file_000000004a84820880553cb7c853aa04.png", "hero-6"),  # Green casino
    ("file_000000002aac8208b7ce20bca18ced83.png", "hero-7"),  # Purple vault
]


def backdrop(image):
    """A blurred, cropped-to-fill copy, used behind a banner that is squarer
    than the canvas."""
    ratio = max(CANVAS[0] / image.width, CANVAS[1] / image.height)
    filled = image.resize(
        (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
        Image.LANCZOS,
    )
    left = (filled.width - CANVAS[0]) // 2
    top = (filled.height - CANVAS[1]) // 2
    return (
        filled.crop((left, top, left + CANVAS[0], top + CANVAS[1]))
        .filter(ImageFilter.GaussianBlur(28))
        .point(lambda v: int(v * 0.72))  # darkened, so it sits behind
    )


def export():
    os.makedirs(OUT, exist_ok=True)
    total = 0
    for filename, slug in BANNERS:
        path = os.path.join(SRC, filename)
        if not os.path.exists(path):
            print(f"  MISSING {filename}")
            continue

        with Image.open(path) as raw:
            image = raw.convert("RGB")
            canvas = backdrop(image)
            # Fit the whole banner inside the canvas.
            scale = min(CANVAS[0] / image.width, CANVAS[1] / image.height)
            fitted = image.resize(
                (round(image.width * scale), round(image.height * scale)), Image.LANCZOS
            )
            canvas.paste(
                fitted,
                ((CANVAS[0] - fitted.width) // 2, (CANVAS[1] - fitted.height) // 2),
            )

            out = io.BytesIO()
            canvas.save(out, format="WEBP", quality=QUALITY, method=6)

        target = os.path.join(OUT, f"{slug}.webp")
        with open(target, "wb") as handle:
            handle.write(out.getvalue())
        total += len(out.getvalue())
        filler = "full bleed" if fitted.size == CANVAS else f"fitted {fitted.size[0]}x{fitted.size[1]}"
        print(
            f"  {slug}.webp  {raw.size[0]}x{raw.size[1]} -> {CANVAS[0]}x{CANVAS[1]}"
            f"  {len(out.getvalue()) // 1024:>4} KB  ({filler})"
        )

    print(f"\nWrote {len(BANNERS)} banners, {total // 1024} KB total, to {OUT}")


if __name__ == "__main__":
    export()
