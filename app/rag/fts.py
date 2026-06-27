"""Postgres-only full-text-search wiring for document_chunks.

search_vector is a generated tsvector kept out of the SQLModel model so the
tables still build under SQLite. This adds it (and its GIN index) on Postgres,
idempotently. Used by the migration script and by the retrieval test fixture.
"""

from sqlalchemy import text


def ensure_search_vector(engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS search_vector "
            "tsvector GENERATED ALWAYS AS (to_tsvector('english', chunk_text)) STORED"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_document_chunks_search "
            "ON document_chunks USING GIN (search_vector)"
        ))
