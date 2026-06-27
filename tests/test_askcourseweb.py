"""Layer 33 — the Ask My Course web flow.

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
from app.web import (
    create_app,
    get_canvas_client_factory,
    get_groq_client,
    get_session,
)

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


def _make_app(engine, canvas_handler=None, groq_handler=None):
    """Build the app with session + optional Canvas/Groq dependency overrides."""
    application = create_app()

    def _get_session():
        with Session(engine) as s:
            yield s

    application.dependency_overrides[get_session] = _get_session

    if canvas_handler is not None:
        def _canvas_factory():
            return lambda: httpx.Client(transport=httpx.MockTransport(canvas_handler))
        application.dependency_overrides[get_canvas_client_factory] = _canvas_factory

    if groq_handler is not None:
        def _groq_client():
            client = httpx.Client(transport=httpx.MockTransport(groq_handler))
            try:
                yield client
            finally:
                client.close()
        application.dependency_overrides[get_groq_client] = _groq_client

    return application


def _signup(client, email):
    """Sign up via the web endpoint (sets session cookie on the TestClient)."""
    client.post("/signup", data={"email": email, "password": "hunter2pw"},
                follow_redirects=False)


def _seed_connection_and_course(engine, email, course_name="Bio 101",
                                canvas_course_id=CANVAS_COURSE_ID):
    """Add a connection + course to the already-signed-up user; return course.id."""
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=user.id, label="School", base_url=BASE,
                          account_type="student", access_token="test-token")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        course = Course(connection_id=conn.id, canvas_course_id=canvas_course_id,
                        name=course_name)
        s.add(course)
        s.commit()
        s.refresh(course)
        return course.id


def _canvas_with_syllabus(request):
    """Return a syllabus for /courses/42; empty list for everything else."""
    path = request.url.path
    if path.endswith(f"/courses/{CANVAS_COURSE_ID}"):
        return httpx.Response(200, json={
            "id": CANVAS_COURSE_ID,
            "syllabus_body": "<p>Late work loses 10% per day.</p>",
        })
    return httpx.Response(200, json=[])


def _groq_grounded_answer(request):
    """Return a grounded answer from the Groq mock."""
    return httpx.Response(200, json={
        "choices": [{"message": {"content": "Late work loses 10% per day."}}]
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_flag_off_hides_routes(monkeypatch, engine):
    """With ASK_COURSE_ENABLED unset, GET /ask returns 404."""
    monkeypatch.delenv("ASK_COURSE_ENABLED", raising=False)
    app = _make_app(engine)
    client = TestClient(app)

    resp = client.get("/ask")
    assert resp.status_code == 404


def test_picker_lists_only_my_courses(monkeypatch, engine):
    """The picker shows the signed-in user's courses and not another user's."""
    monkeypatch.setenv("ASK_COURSE_ENABLED", "1")

    app = _make_app(engine)

    client_a = TestClient(app)
    _signup(client_a, "a@test.com")
    _seed_connection_and_course(engine, "a@test.com", course_name="Algebra",
                                canvas_course_id=10)

    client_b = TestClient(app)
    _signup(client_b, "b@test.com")
    _seed_connection_and_course(engine, "b@test.com", course_name="Biology",
                                canvas_course_id=20)

    body_a = client_a.get("/ask").text
    assert "Algebra" in body_a
    assert "Biology" not in body_a


def test_sync_then_ask_renders_answer_with_sources(monkeypatch, engine):
    """Syncing then asking renders the answer and the syllabus source link."""
    monkeypatch.setenv("ASK_COURSE_ENABLED", "1")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    app = _make_app(engine,
                    canvas_handler=_canvas_with_syllabus,
                    groq_handler=_groq_grounded_answer)
    client = TestClient(app)

    _signup(client, "student@test.com")
    course_id = _seed_connection_and_course(engine, "student@test.com",
                                            course_name="History 101",
                                            canvas_course_id=CANVAS_COURSE_ID)

    # Sync course content (Canvas mock returns the syllabus)
    sync_resp = client.post(f"/courses/{course_id}/sync-content",
                            follow_redirects=False)
    assert sync_resp.status_code in (302, 303)

    # Ask a question (Groq mock echoes the grounded answer)
    ask_resp = client.post(f"/courses/{course_id}/ask",
                           data={"question": "late policy"})
    assert ask_resp.status_code == 200
    body = ask_resp.text

    assert "Late work loses 10% per day." in body
    syllabus_url = f"{BASE}/courses/{CANVAS_COURSE_ID}/assignments/syllabus"
    assert syllabus_url in body


def test_cannot_ask_another_users_course(monkeypatch, engine):
    """A user cannot ask a course belonging to another user — 404."""
    monkeypatch.setenv("ASK_COURSE_ENABLED", "1")

    app = _make_app(engine)

    owner = TestClient(app)
    _signup(owner, "owner@test.com")
    course_id = _seed_connection_and_course(engine, "owner@test.com",
                                            course_name="History",
                                            canvas_course_id=CANVAS_COURSE_ID)

    intruder = TestClient(app)
    _signup(intruder, "intruder@test.com")

    resp = intruder.post(f"/courses/{course_id}/ask",
                         data={"question": "anything"})
    assert resp.status_code == 404
