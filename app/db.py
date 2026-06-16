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
    return create_engine(url)
