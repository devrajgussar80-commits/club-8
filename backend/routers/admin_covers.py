"""Lobby cover art: what each game shows on the home screen, and replacing it.

Every game ships with a drawn SVG in `assets/covers`. That stays the default
and the fallback; uploading here stores an override in the database, and
deleting the override puts the bundled art back. Nothing is ever overwritten
on disk, so a bad upload is one click from being undone.

Stored as bytes in Postgres rather than as a file: the API runs on a host with
an ephemeral filesystem, where anything written at runtime disappears on the
next deploy -- the uploaded QR images are kept the same way for the same
reason.

The read route is public and unauthenticated, because the player's browser
loads it straight into an <img> on the home screen. It serves artwork, not
anything private.
"""

import io

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from PIL import Image, UnidentifiedImageError

from database import get_db_connection
from deps import require_admin

router = APIRouter(tags=["covers"])

# game key -> (display name, bundled SVG slug). The key is what the lobby tile
# and every API path use; the slug is the file already sitting in assets.
COVERS = {
    "wingo": ("WinGo", "wingo"),
    "aviator": ("XAviator", "aviator"),
    "aviator10": ("XAviator 10s", "aviator-10"),
    "chicken-road": ("Chicken Road", "chicken-road"),
    "slots": ("Lucky Reels", "lucky-reels"),
    "megaslots": ("Mega Slots", "mega-slots"),
    "roulette": ("Roulette", "roulette"),
    "dice": ("Dice Roll", "dice"),
    "fishtiger": ("Fish vs Tiger", "fishtiger"),
    "vortex": ("Vortex", "vortex"),
    "lottery": ("Daily Lottery", "lottery"),
    "mines": ("Mines", "mines"),
    "mines-pro": ("Mines Pro", "mines-pro"),
    "ronaldinho": ("Ronaldinho da Sorte", "ronaldinho"),
    "pubg": ("PUBG 1MIN", "pubg"),
    "cricket": ("Cricket", "cricket"),
    "limbo": ("Limbo", "limbo"),
    "javelin": ("Javelin", "javelin"),
}

# Raster or vector both fine. Anything else is refused rather than stored and
# served as a broken image later.
ALLOWED = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}
MAX_BYTES = 4 * 1024 * 1024

# A lobby tile is about 180 CSS pixels wide, so this is already generous at 3x
# and still a fraction of a 4K upload. Eighteen originals is tens of megabytes
# on a phone, which is long enough for the bundled artwork to sit on screen
# waiting -- the whole reason covers appeared to "change" after loading.
THUMB_BOX = (720, 960)
THUMB_TYPE = "image/webp"


def _make_thumb(data: bytes):
    """Rescale an upload to tile size. Returns None if it cannot be read.

    SVG is already small and resolution-independent, so it is left alone --
    Pillow cannot open it anyway, which is what the None path covers.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB")
            image.thumbnail(THUMB_BOX, Image.LANCZOS)
            out = io.BytesIO()
            image.save(out, format="WEBP", quality=82, method=4)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def _version(uploaded_at, size_bytes) -> str:
    """Short token that changes whenever the artwork does."""
    stamp = int(uploaded_at.timestamp()) if uploaded_at else 0
    return f"{stamp:x}{int(size_bytes or 0):x}"


@router.get("/api/covers")
def public_covers():
    """Which games currently have an uploaded cover.

    The player app fetches this once and repoints only those tiles, instead of
    routing every tile through the API -- the frontend is served from a static
    host, and the bundled art should keep working even if the API is down.
    """
    conn = get_db_connection(readonly=True)
    try:
        rows = conn.execute(
            "SELECT game, uploaded_at, size_bytes FROM game_covers"
        ).fetchall()
    finally:
        conn.close()

    # A version per cover, so the client can ask for an exact revision and keep
    # it in the browser cache for good. Without one the tile had to re-check
    # the API on every load, and the bundled art showed until it answered.
    versions = {
        row["game"]: _version(row["uploaded_at"], row["size_bytes"])
        for row in rows
    }
    return {"custom": sorted(versions), "versions": versions}


@router.get("/api/cover/{game}")
def serve_cover(game: str, v: str = "", full: bool = False):
    """The cover a lobby tile shows.

    Serves the rescaled copy, not the original: `full=true` is for the admin
    panel, which is reviewing the actual upload.
    """
    conn = get_db_connection(readonly=True)
    try:
        row = conn.execute(
            "SELECT data, content_type, uploaded_at, size_bytes, thumb, thumb_type "
            "FROM game_covers WHERE game = ?",
            (game,),
        ).fetchone()
    finally:
        conn.close()

    if not row or not row["data"]:
        raise HTTPException(status_code=404, detail="No custom cover for this game.")

    body, media = bytes(row["data"]), row["content_type"] or "image/png"
    if not full:
        if row["thumb"]:
            body, media = bytes(row["thumb"]), row["thumb_type"] or THUMB_TYPE
        else:
            # Uploaded before rescaling existed. Build it now and keep it, so
            # this costs one request per cover rather than every request.
            made = _make_thumb(body)
            if made:
                body, media = made, THUMB_TYPE
                _store_thumb(game, made)

    # A request that names a version is asking for that exact image, and the
    # version changes on every upload -- so it can be cached for good. Without
    # one the URL is "whatever is current", which must be revalidated.
    current = _version(row["uploaded_at"], row["size_bytes"])
    cache = (
        "public, max-age=31536000, immutable"
        if v and v == current
        else "public, max-age=60, must-revalidate"
    )

    return Response(
        content=body,
        media_type=media,
        headers={"Cache-Control": cache},
    )


def _store_thumb(game: str, thumb: bytes) -> None:
    """Keep a thumbnail built on the fly. Failure here is not worth an error:
    the image has already been rescaled and is about to be served either way.
    """
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE game_covers SET thumb = ?, thumb_type = ? WHERE game = ?",
            (thumb, THUMB_TYPE, game),
        )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


@router.get("/api/admin/covers")
def list_covers(_: bool = Depends(require_admin)):
    conn = get_db_connection()
    try:
        rows = {
            row["game"]: row
            for row in conn.execute(
                "SELECT game, filename, content_type, size_bytes, uploaded_at FROM game_covers"
            ).fetchall()
        }
    finally:
        conn.close()

    out = []
    for game, (label, slug) in COVERS.items():
        custom = rows.get(game)
        out.append({
            "game": game,
            "label": label,
            # What the lobby actually shows. `export_covers.py` writes these
            # from the uploads below, so this is the live artwork, not a
            # fallback -- the lobby no longer reads covers from the database.
            "live_url": f"assets/covers/{slug}.webp",
            "default_url": f"assets/covers/{slug}.webp",
            "custom": bool(custom),
            "filename": custom["filename"] if custom else None,
            "size_bytes": int(custom["size_bytes"] or 0) if custom else 0,
            "uploaded_at": str(custom["uploaded_at"]) if custom else None,
        })
    return {"covers": out, "max_bytes": MAX_BYTES,
            "accepts": sorted(ALLOWED)}


@router.post("/api/admin/covers/{game}")
async def upload_cover(game: str, file: UploadFile = File(...), _: bool = Depends(require_admin)):
    if game not in COVERS:
        raise HTTPException(status_code=404, detail=f"Unknown game: {game}")

    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED:
        raise HTTPException(
            status_code=400,
            detail=f"Use a PNG, JPG, WEBP or SVG. Got {content_type or 'an unknown type'}.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Too large: {len(data) / 1048576:.1f} MB. Keep it under "
                   f"{MAX_BYTES // 1048576} MB.",
        )

    thumb = _make_thumb(data)

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO game_covers
                (game, filename, content_type, data, size_bytes, uploaded_at,
                 thumb, thumb_type)
            VALUES (?, ?, ?, ?, ?, NOW(), ?, ?)
            ON CONFLICT (game) DO UPDATE SET
                filename = excluded.filename,
                content_type = excluded.content_type,
                data = excluded.data,
                size_bytes = excluded.size_bytes,
                uploaded_at = NOW(),
                thumb = excluded.thumb,
                thumb_type = excluded.thumb_type
            """,
            (game, file.filename, content_type, data, len(data),
             thumb, THUMB_TYPE if thumb else None),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"status": "success", "game": game, "size_bytes": len(data)}


@router.delete("/api/admin/covers/{game}")
def reset_cover(game: str, _: bool = Depends(require_admin)):
    """Drop the override so the bundled artwork shows again."""
    if game not in COVERS:
        raise HTTPException(status_code=404, detail=f"Unknown game: {game}")
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM game_covers WHERE game = ?", (game,))
        conn.commit()
    finally:
        conn.close()
    return {"status": "success", "game": game}
