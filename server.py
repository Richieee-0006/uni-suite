"""Uvicorn entrypoint for UNI Suite (default 127.0.0.1:5052)."""

from __future__ import annotations

import uvicorn

import db
from webapp.config import load_settings


def main() -> None:
    """Validate configuration, initialize database, and run uvicorn."""
    settings = load_settings()
    db.init_db(settings.db_path)
    uvicorn.run(
        "webapp.main:app",
        host=settings.host,
        port=settings.port,
        proxy_headers=settings.trust_proxy,
        forwarded_allow_ips="127.0.0.1" if settings.trust_proxy else None,
    )


if __name__ == "__main__":
    main()
