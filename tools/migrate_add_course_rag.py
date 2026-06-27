"""Create the RAG tables and the Postgres full-text index on the live Neon branch.

    python tools/migrate_add_course_rag.py

New tables only (courses, course_documents, document_chunks) — safe and additive.
Then adds the generated search_vector column + GIN index via
app.rag.fts.ensure_search_vector. Idempotent. Prints the target host (no
credentials).
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
from app.rag.fts import ensure_search_vector  # noqa: E402
import app.models  # noqa: E402,F401  (registers the tables on the metadata)


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set. Refusing to run.")
        raise SystemExit(2)
    if url.startswith("sqlite"):
        print("This looks like local SQLite, not Neon. Point DATABASE_URL at Neon.")
        raise SystemExit(1)

    print(f"About to create RAG tables in: {urlparse(url).hostname}")
    engine = make_engine(url)
    SQLModel.metadata.create_all(engine)  # additive: only missing tables
    ensure_search_vector(engine)
    print("RAG tables and full-text index ready. Done.")


if __name__ == "__main__":
    main()
