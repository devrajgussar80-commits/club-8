"""Serve the Android APK the admin uploaded.

Storage is the app_downloads table, one row (id = 'current'). The bytes are
streamed back with an attachment header so a browser downloads the file rather
than trying to open it. Both routes are public: an APK is meant to be shared.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from database import get_db_connection

router = APIRouter(prefix="/api/app", tags=["app"])

CURRENT = "current"


@router.get("/info")
def app_info():
    """Lightweight metadata for the download button: does an app exist, and
    which version/size, without shipping the whole file."""
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT filename, version, size_bytes, uploaded_at FROM app_downloads WHERE id = ?",
            (CURRENT,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {"available": False}
    return {
        "available": True,
        "filename": row["filename"],
        "version": row["version"],
        "size_bytes": int(row["size_bytes"] or 0),
        "uploaded_at": row["uploaded_at"].isoformat() if row["uploaded_at"] else None,
    }


@router.get("/download")
def app_download():
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT filename, content_type, data FROM app_downloads WHERE id = ?",
            (CURRENT,),
        ).fetchone()
    finally:
        conn.close()

    if not row or not row["data"]:
        raise HTTPException(status_code=404, detail="No app has been uploaded yet.")

    filename = row["filename"] or "club8.apk"
    return Response(
        content=bytes(row["data"]),
        media_type=row["content_type"] or "application/vnd.android.package-archive",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # A new upload replaces the row, so a short cache is safe and spares
            # the free-tier backend from re-streaming on every tap.
            "Cache-Control": "public, max-age=600",
        },
    )
