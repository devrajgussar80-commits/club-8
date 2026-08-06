"""FastAPI entrypoint for the Club 8 REST API.

Route handlers live in `routers/`; this module only assembles the app so the
wiring (CORS, static mounts, the round clock) is readable in one screen.
"""

import asyncio
import contextlib
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import config
import database
import shared_rounds
from game_engine import python_engine
from routers import (
    admin,
    admin_covers,
    admin_games,
    admin_maintenance,
    analytics,
    app_download,
    auth,
    game,
    pages,
    referrals,
    wallet,
)
from routers.games import (
    aviator,
    chicken,
    dice,
    fishtiger,
    lottery,
    megaslots,
    mines,
    roulette,
    slots,
    vortex,
)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    """Run the round clock alongside the app.

    Some WSGI hosts never let this task run, which is why `get_status` also
    ticks lazily on every read. Both paths take the engine's tick lock.
    """
    clock = asyncio.create_task(python_engine.start_loop())
    # Fish vs Tiger and Vortex settle on their own clock for the same reason,
    # so no player's poll ever pays for a round boundary.
    table_clock = asyncio.create_task(shared_rounds.run_clock())
    # XAviator too: its rounds only advanced when a request asked for them, so
    # the game did not exist between visits and the first player to arrive
    # started it from round one with nothing behind them.
    aviator_clock = asyncio.create_task(aviator.run_clock())
    try:
        yield
    finally:
        for task in (clock, table_clock, aviator_clock):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # Hand the Postgres connections back before the process exits, so Neon
        # is not left holding sockets against the connection limit.
        database.close_pool()


def create_app() -> FastAPI:
    database.init_db()
    os.makedirs(config.QR_UPLOAD_DIR, exist_ok=True)

    application = FastAPI(title="Club 8 API", version="1.0.0", lifespan=lifespan)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.FRONTEND_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def disable_local_preview_cache(request, call_next):
        response = await call_next(request)
        # Images keep whatever their route decided. QR codes are immutable per
        # id, and a cover URL names the exact revision it wants -- caching them
        # is the point, and stamping no-store here would defeat it.
        if request.url.path.startswith(("/api/qr-image/", "/api/cover/")):
            return response
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        return response

    application.include_router(pages.router)
    application.include_router(auth.router)
    application.include_router(game.router)
    application.include_router(wallet.router)
    application.include_router(referrals.router)
    application.include_router(app_download.router)
    application.include_router(admin.router)
    application.include_router(admin_games.router)
    application.include_router(admin_covers.router)
    application.include_router(admin_maintenance.router)
    application.include_router(analytics.router)

    # One router per arcade game; each owns its own rules and payout table.
    application.include_router(aviator.router)
    application.include_router(slots.router)
    application.include_router(megaslots.router)
    application.include_router(roulette.router)
    application.include_router(dice.router)
    application.include_router(fishtiger.router)
    application.include_router(vortex.router)
    application.include_router(chicken.router)
    application.include_router(mines.router)
    application.include_router(lottery.router)

    application.mount(
        "/uploads", StaticFiles(directory=config.UPLOAD_ROOT), name="uploads"
    )
    # Mounted last: a catch-all at "/" would shadow every route above it.
    if config.SERVE_FRONTEND:
        application.mount(
            "/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="static"
        )

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        reload=not config.IS_PRODUCTION,
    )
