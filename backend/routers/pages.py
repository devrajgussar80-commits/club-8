"""Health check and SPA entry points.

The frontend is normally served by Vercel; these routes only matter when the
API also serves the static build (``SERVE_FRONTEND=true``).
"""

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

import config
from database import get_db_connection

router = APIRouter()

INDEX_HTML = os.path.join(config.FRONTEND_DIR, "index.html")
# The dashboard is its own document. Sharing one file meant both apps'
# markup was live at once, so player-facing overlays could paint over the
# console and each side had to keep opting out of the other by pathname.
ADMIN_HTML = os.path.join(config.FRONTEND_DIR, "admin.html")
DOWNLOAD_HTML = os.path.join(config.FRONTEND_DIR, "download.html")


@router.get("/api/health")
def health_check():
    return {"status": "ok", "service": "club-8-api"}


@router.get("/api/qr-image/{qr_id}")
def serve_qr_image(qr_id: str):
    """Serve an uploaded deposit QR from the database.

    Deliberately unauthenticated: the player's browser loads this straight
    into an <img>, and the QR is the payment address anyway, not a secret.
    """
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT image_data, image_type FROM qr_codes WHERE id = ?", (qr_id,)
        ).fetchone()
    finally:
        conn.close()

    if not row or not row["image_data"]:
        raise HTTPException(status_code=404, detail="QR image not found")

    return Response(
        content=bytes(row["image_data"]),
        media_type=row["image_type"] or "image/png",
        # The bytes never change for a given id -- a new upload gets a new id.
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/login", response_class=FileResponse)
def serve_login():
    return FileResponse(INDEX_HTML)


@router.get("/game", response_class=FileResponse)
def serve_game():
    return FileResponse(INDEX_HTML)


@router.get("/download", response_class=FileResponse)
def serve_download():
    """The public APK landing page the Share button links to.

    Vercel serves this in production by mapping /download to download.html on
    its own. The static mount here does not do that -- it looks for a file
    literally named "download" -- so without this route the page 404s in local
    development only, and the share link looks broken while testing.
    """
    return FileResponse(DOWNLOAD_HTML)


@router.get("/admin", response_class=FileResponse)
def serve_admin():
    return FileResponse(ADMIN_HTML)
