"""Add courses.hidden and courses.has_assignments to whatever DATABASE_URL points at.

    python tools/migrate_add_course_visibility.py

One-time migration for the live Neon branch. Idempotent (ADD COLUMN IF NOT
EXISTS) and safe to re-run. Prints the target host (no credentials). Fresh
databases get the columns via tools/init_db.py.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import text  # noqa: E402

from app.db import make_engine  # noqa: E402


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set. Refusing to run.")
        raise SystemExit(2)

    parsed = urlparse(url)
    where = parsed.hostname or url.split(":", 1)[0]
    print(f"About to alter courses table in: {where}")

    if url.startswith("sqlite"):
        print("This looks like a local SQLite file, not your Neon branch.")
        print("Point DATABASE_URL at Neon first, then re-run.")
        raise SystemExit(1)

    engine = make_engine(url)
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE courses ADD COLUMN IF NOT EXISTS hidden BOOLEAN"
        ))
        conn.execute(text(
            "ALTER TABLE courses ADD COLUMN IF NOT EXISTS has_assignments BOOLEAN DEFAULT FALSE"
        ))
    print("Columns hidden and has_assignments added (or already present). Done.")


if __name__ == "__main__":
    main()
