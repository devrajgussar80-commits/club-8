"""Load `backend/.env.local` into os.environ for local development.

`.env.example` has always told you to create `.env.local`, but nothing read it,
so every local run needed the variables exported by hand. Imported first by
`config`, `database` and `auth` -- all three read os.environ at module scope,
so the file has to be loaded before any of them.

Real environment variables always win, so a host like Render is unaffected
(there is no .env.local there anyway).
"""

import os

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.local")


def load(path: str = ENV_PATH) -> None:
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # Never clobber a variable the process was actually started with.
            if key and key not in os.environ:
                os.environ[key] = value


load()
