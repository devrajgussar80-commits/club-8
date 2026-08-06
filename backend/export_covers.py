"""Write the covers uploaded from the admin panel out as static files.

The lobby used to fetch `/api/covers` on load and repoint each tile, which
meant the bundled artwork was painted first and visibly replaced once the
answer came back. Serving them as ordinary files removes the swap entirely:
the browser has the right image before it paints, and the lobby no longer
depends on the API being up at all.

The uploads are 4K, a couple of megabytes each. A tile is about 180 CSS pixels
wide, so they are rescaled on the way out -- eighteen originals is tens of
megabytes on a phone.

Run after replacing covers in the admin panel:

    python backend/export_covers.py
"""

import io
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_db_connection  # noqa: E402
from routers.admin_covers import COVERS  # noqa: E402

# Generous at 3x for a tile this size, and a fraction of the original.
BOX = (720, 960)
QUALITY = 82

OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "covers"
)


def export() -> int:
    conn = get_db_connection(readonly=True)
    try:
        rows = conn.execute("SELECT game, data FROM game_covers").fetchall()
    finally:
        conn.close()

    stored = {row["game"]: bytes(row["data"]) for row in rows if row["data"]}
    if not stored:
        print("No uploaded covers in the database; nothing to export.")
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    written = 0
    for game, (label, slug) in COVERS.items():
        data = stored.get(game)
        if not data:
            continue
        try:
            with Image.open(io.BytesIO(data)) as image:
                image = image.convert(
                    "RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB"
                )
                image.thumbnail(BOX, Image.LANCZOS)
                size = image.size
                out = io.BytesIO()
                image.save(out, format="WEBP", quality=QUALITY, method=6)
        except Exception as error:  # noqa: BLE001 -- report and keep going
            print(f"  skip {game}: {error}")
            continue

        path = os.path.join(OUT_DIR, f"{slug}.webp")
        with open(path, "wb") as handle:
            handle.write(out.getvalue())
        written += 1
        print(
            f"  {label:22} {len(data) // 1024:>5} KB -> "
            f"{len(out.getvalue()) // 1024:>4} KB  {size[0]}x{size[1]}  {slug}.webp"
        )

    print(f"\nWrote {written} cover(s) to {OUT_DIR}")
    return written


if __name__ == "__main__":
    export()
