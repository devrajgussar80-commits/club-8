"""Serve the Android APK the admin uploaded.

Storage is the app_downloads table, one row (id = 'current'). The bytes are
streamed back with an attachment header so a browser downloads the file rather
than trying to open it. Both routes are public: an APK is meant to be shared.
"""

from fastapi import APIRouter, HTTPException, Request
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


def _client_ip(request: Request) -> str:
    """The visitor's address, not the proxy's."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def _record_hit(request: Request, visitor: str, session: str, user: str) -> None:
    """Log that the file was actually fetched.

    The dashboard needs to separate "opened the download page" from "took the
    app", and the page view alone cannot tell the difference. Recorded here
    rather than in the browser because the APK is fetched by a plain navigation
    -- no script of ours runs on that request.

    Never allowed to break the download: a failure to count is not a reason to
    withhold the file.
    """
    try:
        conn = get_db_connection()
        try:
            conn.execute(
                """
                INSERT INTO app_download_hits
                    (visitor_id, session_id, user_id, ip, user_agent)
                VALUES (?, ?, ?, ?, ?)
                """,
                (visitor[:64] or None, session[:64] or None, user[:64] or None,
                 _client_ip(request), request.headers.get("user-agent", "")[:512]),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


@router.get("/download")
def app_download(request: Request, v: str = "", s: str = "", u: str = ""):
    _record_hit(request, v, s, u)
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
