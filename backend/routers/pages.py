"""Health check and SPA entry points.

The frontend is normally served by Vercel; these routes only matter when the
API also serves the static build (``SERVE_FRONTEND=true``).
"""

import os

from fastapi import APIRouter
from fastapi.responses import FileResponse

import config

router = APIRouter()

INDEX_HTML = os.path.join(config.FRONTEND_DIR, "index.html")


@router.get("/api/health")
def health_check():
    return {"status": "ok", "service": "club-8-api"}


@router.get("/login", response_class=FileResponse)
def serve_login():
    return FileResponse(INDEX_HTML)


@router.get("/game", response_class=FileResponse)
def serve_game():
    return FileResponse(INDEX_HTML)


@router.get("/admin", response_class=FileResponse)
def serve_admin():
    return FileResponse(INDEX_HTML)
