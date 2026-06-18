"""Create all tables in whatever DATABASE_URL points at.

    python tools/init_db.py

One-time setup for a fresh database (e.g. your Neon production branch).
Creates tables only — never inserts data, never drops anything. Safe to run
again; existing tables are left as-is. Prints the target host so you can
confirm you are pointed at the right database before anything happens.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlmodel import SQLModel  # noqa: E402

from app.db import make_engine  # noqa: E402
from app.models import Assignment, Connection, User  # noqa: E402  (import registers tables)


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set. Refusing to run.")
        raise SystemExit(2)

    # Show where we're about to write, with no credentials.
    parsed = urlparse(url)
    where = parsed.hostname or url.split(":", 1)[0]
    print(f"About to create tables in: {where}")

    if url.startswith("sqlite"):
        print("This looks like a local SQLite file, not your Neon branch.")
        print("Point DATABASE_URL at Neon first, then re-run.")
        raise SystemExit(1)

    engine = make_engine(url)
    SQLModel.metadata.create_all(engine)
    print("Tables created (or already present). Done.")


if __name__ == "__main__":
    main()
