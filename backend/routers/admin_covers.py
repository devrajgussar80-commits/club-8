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

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile

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


@router.get("/api/covers")
def public_covers():
    """Which games currently have an uploaded cover.

    The player app fetches this once and repoints only those tiles, instead of
    routing every tile through the API -- the frontend is served from a static
    host, and the bundled art should keep working even if the API is down.
    """
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT game FROM game_covers").fetchall()
    finally:
        conn.close()
    return {"custom": [row["game"] for row in rows]}


@router.get("/api/cover/{game}")
def serve_cover(game: str):
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT data, content_type FROM game_covers WHERE game = ?", (game,)
        ).fetchone()
    finally:
        conn.close()

    if not row or not row["data"]:
        raise HTTPException(status_code=404, detail="No custom cover for this game.")

    return Response(
        content=bytes(row["data"]),
        media_type=row["content_type"] or "image/png",
        # Uploads replace the row in place, so a long cache would keep serving
        # the old art. Short and revalidating rather than immutable.
        headers={"Cache-Control": "public, max-age=60, must-revalidate"},
    )


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
            "default_url": f"assets/covers/{slug}.svg",
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

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO game_covers (game, filename, content_type, data, size_bytes, uploaded_at)
            VALUES (?, ?, ?, ?, ?, NOW())
            ON CONFLICT (game) DO UPDATE SET
                filename = excluded.filename,
                content_type = excluded.content_type,
                data = excluded.data,
                size_bytes = excluded.size_bytes,
                uploaded_at = NOW()
            """,
            (game, file.filename, content_type, data, len(data)),
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
