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
    return create_engine(url, connect_args=connect_args)
