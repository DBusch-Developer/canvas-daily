"""Engine creation. The base URL lives on the connection string we are given —
never global Canvas config; this is purely the app's own Postgres (Neon).
"""

import os

from sqlmodel import create_engine


def make_engine(url=None):
    url = url or os.environ["DATABASE_URL"]
    # SQLAlchemy needs an explicit driver; Neon hands out plain postgresql:// URLs.
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    # SQLite (local dev only) is served across FastAPI's threadpool.
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    # Neon (serverless Postgres) drops idle connections, so a pooled connection
    # can be dead by the next request. pre_ping checks liveness and transparently
    # replaces a dead connection; recycle retires connections older than 5 minutes.
    return create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_recycle=300,
    )
