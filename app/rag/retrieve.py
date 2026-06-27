"""Course-scoped full-text retrieval over document_chunks.

Lexical search via Postgres tsvector/ts_rank — no embeddings. Always filtered by
course_id so one course's answer never draws on another's. Raw SQL because
search_vector is a Postgres-only generated column not mapped on the model.
"""

from sqlalchemy import text

_SQL = text(
    "SELECT chunk_text, source_title, source_url, "
    "       ts_rank(search_vector, plainto_tsquery('english', :q)) AS rank "
    "FROM document_chunks "
    "WHERE course_id = :cid "
    "  AND search_vector @@ to_tsquery('english', "
    "      regexp_replace(plainto_tsquery('english', :q)::text, ' & ', ' | ', 'g')) "
    "ORDER BY rank DESC "
    "LIMIT :k"
)


def retrieve(session, course_id, question, k=5):
    if not question.strip():
        return []
    # ts_rank scores with the AND-form query (plainto_tsquery); the WHERE clause
    # matches with the OR-form so partial hits still surface.
    rows = session.execute(
        _SQL, {"q": question, "cid": course_id, "k": k}
    ).all()
    return [
        {"chunk_text": r.chunk_text, "source_title": r.source_title,
         "source_url": r.source_url, "rank": float(r.rank)}
        for r in rows
    ]
