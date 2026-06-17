"""HTMX in-place swap for the AI breakdown.

These run offline against an in-memory SQLite branch, so they don't need a Neon
test branch like the E2E suite. They pin the new behavior: an HTMX request
(HX-Request header) to the breakdown route gets just the result fragment — no
site header, no full HTML document — while a normal POST still gets the full
page exactly as before. Groq is mocked; the AI logic itself is untouched.
"""

from datetime import datetime

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Assignment, Connection, User
from app.web import create_app, get_groq_client, get_session

MARKDOWN = (
    "## What's being asked\nMeasure stuff.\n"
    "## Step-by-step plan\nDo it.\n## Watch out for\nUnits.\n## Time estimate\n1 hour"
)


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture
def app(engine):
    application = create_app()

    def _get_session():
        with Session(engine) as s:
            yield s

    application.dependency_overrides[get_session] = _get_session
    return application


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


def seed_logged_in_assignment(client, engine, email="htmx@x.com"):
    """Sign up (gets a session cookie) and seed one owned assignment."""
    client.post("/signup", data={"email": email, "password": "hunter2pw"},
                follow_redirects=False)
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=user.id, label="Mine", base_url="https://school.test",
                          account_type="student", access_token="canvas-tok")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        a = Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Lab report",
                       description="<p>Measure and write up.</p>",
                       due_at=datetime(2026, 6, 20, 12, 0), points_possible=25.0,
                       submission_types=["online_upload"], html_url="https://school.test/a/1",
                       workflow_state="unsubmitted")
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def mock_groq(app, handler):
    app.dependency_overrides[get_groq_client] = lambda: httpx.Client(
        transport=httpx.MockTransport(handler)
    )


def is_full_page(text):
    return "<!doctype" in text.lower() or 'class="topbar"' in text


def test_htmx_success_returns_only_the_fragment(client, app, engine):
    assignment_id = seed_logged_in_assignment(client, engine)
    mock_groq(app, lambda r: httpx.Response(
        200, json={"choices": [{"message": {"content": MARKDOWN}}]}))

    resp = client.post(f"/assignments/{assignment_id}/breakdown",
                       headers={"HX-Request": "true"})

    assert resp.status_code == 200
    assert "Measure stuff." in resp.text
    assert not is_full_page(resp.text)


def test_htmx_timeout_returns_message_fragment(client, app, engine):
    assignment_id = seed_logged_in_assignment(client, engine)

    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    mock_groq(app, handler)

    resp = client.post(f"/assignments/{assignment_id}/breakdown",
                       headers={"HX-Request": "true"})

    assert "too long" in resp.text.lower()
    assert not is_full_page(resp.text)


def test_htmx_error_returns_message_fragment(client, app, engine):
    assignment_id = seed_logged_in_assignment(client, engine)
    mock_groq(app, lambda r: httpx.Response(500, json={"error": "upstream boom"}))

    resp = client.post(f"/assignments/{assignment_id}/breakdown",
                       headers={"HX-Request": "true"})

    assert "unavailable" in resp.text.lower()
    assert "upstream boom" not in resp.text
    assert not is_full_page(resp.text)


def test_normal_post_still_returns_full_page(client, app, engine):
    assignment_id = seed_logged_in_assignment(client, engine)
    mock_groq(app, lambda r: httpx.Response(
        200, json={"choices": [{"message": {"content": MARKDOWN}}]}))

    resp = client.post(f"/assignments/{assignment_id}/breakdown")

    assert resp.status_code == 200
    assert "Measure stuff." in resp.text
    assert is_full_page(resp.text)
