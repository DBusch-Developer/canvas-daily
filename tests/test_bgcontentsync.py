"""Layer 35 — background course-content sync.

Syncing a course's content now runs in a FastAPI BackgroundTask so
PDF-heavy courses (a minute+ of work) do not time out behind Render's proxy.
The route redirects immediately to /courses/{id}/ask?syncing=1; the ask page
shows a notice when the query param is present.

Uses in-memory SQLite + StaticPool + mocked Canvas at the httpx transport
boundary. No Neon, no TEST_DATABASE_URL required.
"""

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Connection, Course, CourseDocument, User
from app.web import (
    create_app,
    get_canvas_client_factory,
    get_engine,
    get_session,
)
from fastapi.testclient import TestClient

BASE = "https://school.test"
CANVAS_COURSE_ID = 42


# ---------------------------------------------------------------------------
# Engine + schema
# ---------------------------------------------------------------------------

def _make_sqlite_engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


# ---------------------------------------------------------------------------
# Canvas mock handler
# ---------------------------------------------------------------------------

def _canvas_with_syllabus(request):
    """Syllabus returns text; all other endpoints return empty lists."""
    if request.url.path == f"/api/v1/courses/{CANVAS_COURSE_ID}":
        return httpx.Response(200, json={
            "id": CANVAS_COURSE_ID,
            "syllabus_body": "<p>Late work loses 10 percent per day.</p>",
        })
    return httpx.Response(200, json=[])


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def _make_app(engine, canvas_handler=None):
    application = create_app()

    def _get_session():
        with Session(engine) as s:
            yield s

    def _get_engine():
        return engine

    application.dependency_overrides[get_session] = _get_session
    application.dependency_overrides[get_engine] = _get_engine

    if canvas_handler is not None:
        def _canvas_factory():
            return lambda: httpx.Client(
                transport=httpx.MockTransport(canvas_handler)
            )
        application.dependency_overrides[get_canvas_client_factory] = _canvas_factory

    return application


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signup(client, email, password="hunter2"):
    return client.post("/signup", data={"email": email, "password": password},
                       follow_redirects=False)


def _seed_course(engine, user_email, canvas_course_id=CANVAS_COURSE_ID):
    """Seed a user's connection and course in the SQLite DB. Returns the course id."""
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
            connection_id=conn.id,
            canvas_course_id=canvas_course_id,
            name="Test Course",
        )
        s.add(course)
        s.commit()
        s.refresh(course)
        return course.id


# ---------------------------------------------------------------------------
# Tests — each genuinely fails before any implementation exists
# ---------------------------------------------------------------------------

def test_run_course_content_sync_populates_documents(monkeypatch):
    """run_course_content_sync writes CourseDocument rows for the course.

    Fails before impl: the function does not exist yet (ImportError).
    """
    monkeypatch.setenv("ASK_COURSE_ENABLED", "1")
    # Lazy import so an ImportError is scoped to this test only, not the module.
    from app.web import run_course_content_sync  # noqa: PLC0415

    engine = _make_sqlite_engine()

    with Session(engine) as s:
        user = User(email="u@test.com", password_hash="h")
        s.add(user)
        s.commit()
        s.refresh(user)
        conn = Connection(
            user_id=user.id, label="Conn", base_url=BASE,
            account_type="student", access_token="tok",
        )
        s.add(conn)
        s.commit()
        s.refresh(conn)
        course = Course(
            connection_id=conn.id,
            canvas_course_id=CANVAS_COURSE_ID,
            name="Test Course",
        )
        s.add(course)
        s.commit()
        s.refresh(course)
        course_id = course.id

    def client_factory():
        return httpx.Client(transport=httpx.MockTransport(_canvas_with_syllabus))

    run_course_content_sync(engine, course_id, client_factory)

    with Session(engine) as s:
        docs = s.exec(
            select(CourseDocument).where(CourseDocument.course_id == course_id)
        ).all()

    assert len(docs) > 0, "CourseDocument rows must exist after background sync"


def test_sync_content_route_redirects_with_syncing_flag(monkeypatch):
    """POST /courses/{id}/sync-content returns 303 to /courses/{id}/ask?syncing=1
    and the background task writes CourseDocument rows before TestClient returns.

    Fails before impl: current route redirects without ?syncing=1.
    """
    monkeypatch.setenv("ASK_COURSE_ENABLED", "1")
    engine = _make_sqlite_engine()
    app = _make_app(engine, canvas_handler=_canvas_with_syllabus)
    client = TestClient(app)

    _signup(client, "bg@test.com")
    course_id = _seed_course(engine, "bg@test.com")

    resp = client.post(f"/courses/{course_id}/sync-content", follow_redirects=False)
    assert resp.status_code == 303
    location = resp.headers.get("location", "")
    assert location.endswith(f"/courses/{course_id}/ask?syncing=1"), (
        f"Expected redirect to end with /courses/{course_id}/ask?syncing=1, got: {location!r}"
    )

    # TestClient flushes background tasks before returning; docs must exist.
    with Session(engine) as s:
        docs = s.exec(
            select(CourseDocument).where(CourseDocument.course_id == course_id)
        ).all()
    assert len(docs) > 0, "Background sync must have written CourseDocument rows"


def test_course_page_shows_syncing_notice(monkeypatch):
    """GET /courses/{id}/ask?syncing=1 shows a syncing notice containing 'background';
    GET /courses/{id}/ask (no param) does not show the notice.

    Fails before impl: template has no syncing notice.
    """
    monkeypatch.setenv("ASK_COURSE_ENABLED", "1")
    engine = _make_sqlite_engine()
    app = _make_app(engine)
    client = TestClient(app)

    _signup(client, "notice@test.com")
    course_id = _seed_course(engine, "notice@test.com")

    resp_with = client.get(f"/courses/{course_id}/ask?syncing=1")
    assert resp_with.status_code == 200
    assert "background" in resp_with.text.lower(), (
        "Page must show a syncing notice mentioning 'background' when ?syncing=1 is set"
    )

    resp_without = client.get(f"/courses/{course_id}/ask")
    assert resp_without.status_code == 200
    assert "Syncing course content in the background" not in resp_without.text, (
        "Syncing notice must NOT appear when ?syncing param is absent"
    )
