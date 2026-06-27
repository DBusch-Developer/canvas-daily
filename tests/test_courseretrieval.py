"""Layer 31 — course-scoped full-text retrieval over stored chunks.

Real Postgres (Neon test branch) because search relies on tsvector/ts_rank. The
key correctness property: a query against course A never returns course B's
chunks. Skips unless TEST_DATABASE_URL is set, like the other Neon layers.
"""

import os

import pytest
from sqlmodel import Session, SQLModel

from app.db import make_engine
from app.models import Connection, Course, CourseDocument, DocumentChunk, User
from app.rag.fts import ensure_search_vector
from app.rag.retrieve import retrieve

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (retrieval needs a Neon test branch)",
)


@pytest.fixture(scope="module")
def engine():
    eng = make_engine(os.environ["TEST_DATABASE_URL"])
    SQLModel.metadata.create_all(eng)
    ensure_search_vector(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture
def session(engine):
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn)
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def _course_with_chunk(s, course_name, chunk):
    user = User(email=f"{course_name}@x.com", password_hash="h")
    s.add(user); s.flush()
    conn = Connection(user_id=user.id, label="Mine", base_url="https://s.test",
                      account_type="student", access_token="tok")
    s.add(conn); s.flush()
    course = Course(connection_id=conn.id, canvas_course_id=1, name=course_name)
    s.add(course); s.flush()
    doc = CourseDocument(course_id=course.id, source_type="syllabus",
                         title="Syllabus", canvas_url="u", raw_text=chunk)
    s.add(doc); s.flush()
    s.add(DocumentChunk(course_id=course.id, document_id=doc.id, chunk_text=chunk,
                        source_title="Syllabus", source_url="u"))
    s.flush()
    return course


def test_retrieval_is_scoped_to_one_course(session):
    bio = _course_with_chunk(session, "Bio", "Late work loses ten percent per day.")
    eng = _course_with_chunk(session, "Eng", "Essays use MLA citation format.")

    hits = retrieve(session, bio.id, "late work policy")
    assert hits, "expected a hit in the Bio course"
    assert all(h["source_title"] == "Syllabus" for h in hits)
    assert "ten percent" in hits[0]["chunk_text"]

    # The English course must not surface for a Biology query, and vice versa.
    eng_hits = retrieve(session, eng.id, "late work policy")
    assert eng_hits == []


def test_no_match_returns_empty(session):
    bio = _course_with_chunk(session, "Bio2", "Office hours are Tuesdays.")
    assert retrieve(session, bio.id, "quantum chromodynamics") == []


def test_empty_question_returns_empty_list(session):
    """An empty or whitespace-only question must return [] without hitting Postgres."""
    bio = _course_with_chunk(session, "Bio3", "Office hours are Wednesdays.")
    assert retrieve(session, bio.id, "   ") == []
