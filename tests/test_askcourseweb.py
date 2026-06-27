"""Layer 33 — Ask My Course web flow.

TestClient + Neon test branch. The picker lists only the signed-in user's
courses; syncing stores documents/chunks; asking renders an answer and its
sources; the routes 404 when the feature flag is off; and a user cannot ask
another user's course. Canvas and Groq are mocked at the httpx boundary.
Skips unless TEST_DATABASE_URL is set.
"""

import os

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.db import make_engine
from app.models import Connection, Course, User
from app.rag.fts import ensure_search_vector
from app.web import create_app, get_canvas_client_factory, get_groq_client, get_session

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (web flow needs a Neon test branch)",
)

BASE = "https://school.test"
CANVAS_COURSE_ID = 42


@pytest.fixture(scope="module")
def engine():
    eng = make_engine(os.environ["TEST_DATABASE_URL"])
    SQLModel.metadata.create_all(eng)
    ensure_search_vector(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture(autouse=True)
def wipe(engine):
    yield
    with engine.begin() as conn:
        for table in reversed(SQLModel.metadata.sorted_tables):
            conn.execute(table.delete())


# ---------------------------------------------------------------------------
# Canvas mock handlers
# ---------------------------------------------------------------------------

def _canvas_noop(request):
    """All Canvas endpoints return empty; syllabus returns empty body."""
    if request.url.path == f"/api/v1/courses/{CANVAS_COURSE_ID}":
        return httpx.Response(200, json={"id": CANVAS_COURSE_ID, "syllabus_body": ""})
    return httpx.Response(200, json=[])


def _canvas_with_syllabus(request):
    """Syllabus returns late-policy text; all other endpoints empty."""
    if request.url.path == f"/api/v1/courses/{CANVAS_COURSE_ID}":
        return httpx.Response(200, json={
            "id": CANVAS_COURSE_ID,
            "syllabus_body": "<p>Late work loses 10 percent per day.</p>",
        })
    return httpx.Response(200, json=[])


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def _make_app(engine, canvas_handler=None, groq_handler=None):
    application = create_app()

    def _get_session():
        with Session(engine) as s:
            yield s

    application.dependency_overrides[get_session] = _get_session

    if canvas_handler is not None:
        def _canvas_factory():
            return lambda: httpx.Client(
                transport=httpx.MockTransport(canvas_handler)
            )
        application.dependency_overrides[get_canvas_client_factory] = _canvas_factory

    if groq_handler is not None:
        def _groq():
            c = httpx.Client(transport=httpx.MockTransport(groq_handler))
            try:
                yield c
            finally:
                c.close()
        application.dependency_overrides[get_groq_client] = _groq

    return application


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signup(client, email, password="hunter2"):
    return client.post("/signup", data={"email": email, "password": password},
                       follow_redirects=False)


def _seed_course(engine, user_email, course_name,
                 canvas_course_id=CANVAS_COURSE_ID):
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == user_email)).one()
        conn = Connection(
            user_id=user.id, label="Conn", base_url=BASE,
            account_type="student", access_token="tok",
        )
        s.add(conn)
        s.commit()
        s.refresh(conn)
        course = Course(
            connection_id=conn.id, canvas_course_id=canvas_course_id,
            name=course_name,
        )
        s.add(course)
        s.commit()
        s.refresh(course)
        return course.id


# ---------------------------------------------------------------------------
# Tests (all four must be genuinely red before implementation exists)
# ---------------------------------------------------------------------------

def test_flag_gates_all_four_routes(engine, monkeypatch):
    """Flag ON: GET /ask returns 200 (positive — genuine red when route absent).
    Flag OFF: all four routes return 404."""
    monkeypatch.setenv("ASK_COURSE_ENABLED", "1")
    app = _make_app(engine, canvas_handler=_canvas_noop)
    client = TestClient(app)

    _signup(client, "flagtest@x.com")
    course_id = _seed_course(engine, "flagtest@x.com", "Flag Test Course")

    # Positive assertion: route must exist and return 200 — fails before impl.
    resp = client.get("/ask")
    assert resp.status_code == 200, "GET /ask should be 200 with flag ON"

    # All four routes must 404 when the flag is off.
    monkeypatch.delenv("ASK_COURSE_ENABLED", raising=False)
    assert client.get("/ask").status_code == 404
    assert client.post(f"/courses/{course_id}/sync-content").status_code == 404
    assert client.get(f"/courses/{course_id}/ask").status_code == 404
    assert client.post(
        f"/courses/{course_id}/ask", data={"question": "test"}
    ).status_code == 404


def test_picker_lists_only_my_courses(engine, monkeypatch):
    """GET /ask returns 200 and the body contains only the signed-in user's
    courses — not another user's courses."""
    monkeypatch.setenv("ASK_COURSE_ENABLED", "1")
    app = _make_app(engine)
    client_a = TestClient(app)
    client_b = TestClient(app)

    _signup(client_a, "alice@x.com")
    _signup(client_b, "bob@x.com")
    _seed_course(engine, "alice@x.com", "Biology 101", canvas_course_id=10)
    _seed_course(engine, "bob@x.com", "English 201", canvas_course_id=20)

    resp = client_a.get("/ask")
    assert resp.status_code == 200
    body = resp.text
    # Positive: A's course must appear — genuine red when route absent.
    assert "Biology 101" in body
    # Isolation: B's course must not appear.
    assert "English 201" not in body


def test_sync_then_ask_renders_answer_with_sources(engine, monkeypatch):
    """Sync stores a syllabus; asking a question renders the Groq answer and a
    source link — full positive end-to-end, genuine red when routes absent."""
    monkeypatch.setenv("ASK_COURSE_ENABLED", "1")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")

    def groq_handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {
                "content": "Late work loses 10 percent per day."
            }}],
        })

    app = _make_app(engine,
                    canvas_handler=_canvas_with_syllabus,
                    groq_handler=groq_handler)
    client = TestClient(app)

    _signup(client, "sync@x.com")
    course_id = _seed_course(engine, "sync@x.com", "Test Course")

    # Sync: Canvas mock returns a syllabus with late-policy text.
    resp = client.post(f"/courses/{course_id}/sync-content",
                       follow_redirects=False)
    assert resp.status_code == 303

    # Ask: retrieve chunks, call Groq mock, render answer and sources.
    resp = client.post(f"/courses/{course_id}/ask",
                       data={"question": "late work policy"})
    assert resp.status_code == 200
    body = resp.text
    # Answer text must appear.
    assert "10 percent" in body
    # Syllabus source link must appear.
    assert f"/courses/{CANVAS_COURSE_ID}/assignments/syllabus" in body


def test_ownership_enforced_on_course_routes(engine, monkeypatch):
    """Owner A reaches GET /courses/{id}/ask (200 — genuine red); intruder B
    gets 404 on all three course-scoped routes."""
    monkeypatch.setenv("ASK_COURSE_ENABLED", "1")
    app = _make_app(engine, canvas_handler=_canvas_noop)
    client_a = TestClient(app)
    client_b = TestClient(app)

    _signup(client_a, "owner@x.com")
    _signup(client_b, "intruder@x.com")
    course_id = _seed_course(engine, "owner@x.com", "Owner Course")

    # Owner can access their own course ask page — genuine red when route absent.
    assert client_a.get(f"/courses/{course_id}/ask").status_code == 200

    # Intruder gets 404 on all three course-scoped routes.
    assert client_b.get(f"/courses/{course_id}/ask").status_code == 404
    assert client_b.post(
        f"/courses/{course_id}/ask", data={"question": "test"}
    ).status_code == 404
    assert client_b.post(
        f"/courses/{course_id}/sync-content"
    ).status_code == 404
