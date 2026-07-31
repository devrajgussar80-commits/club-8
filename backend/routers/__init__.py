"""Route modules, mounted by main.create_app()."""

from routers import admin, auth, game, pages, wallet

__all__ = ["admin", "auth", "game", "pages", "wallet"]
