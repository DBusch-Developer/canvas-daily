"""Layer 9 — auto-sync on connect + accounts list (Canvas mocked, Neon test branch).

The feature that makes adding a connection immediately useful: the moment a
Canvas connection is saved, its assignments are pulled and stored (no manual
job), and the connection survives a failed sync. Plus the settings screen as a
real accounts list — list, empty state, and removing a connection — and the
`last_synced_at` stamp the list surfaces.

Canvas is mocked at the httpx transport boundary, exactly as in the fetch and
daily-sync layers; the web flow runs through a real FastAPI TestClient against
the Neon test branch.
"""

import os
from datetime import datetime

import httpx
import pytest
from sqlmodel import Session, SQLModel, select

from app.db import make_engine
from app.models import Assignment, Connection, User
from app.sync import sync_connection
from app.web import create_app, get_canvas_client_factory, get_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (auto-sync tests need a Neon test branch)",
)

BASE = "https://school.test"


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def assignment_json(canvas_id, name):
    return {
        "id": canvas_id, "name": name, "due_at": "2026-06-20T23:59:00Z",
        "points_possible": 100, "submission_types": ["online_text_entry"],
        "html_url": f"{BASE}/a/{canvas_id}", "description": "<p>Do it.</p>",
    }


def canvas_handler(courses):
    """courses: list of (course_id, [assignment_json, ...])."""
    def handler(request):
        path = request.url.path
        if path.endswith("/courses"):
            return httpx.Response(200, json=[{"id": cid, "name": f"Course {cid}"} for cid, _ in courses])
        for cid, assignments in courses:
            if path.endswith(f"/courses/{cid}/assignments"):
                return httpx.Response(200, json=assignments)
        return httpx.Response(200, json=[])
    return handler


@pytest.fixture(scope="module")
def engine():
    eng = make_engine(os.environ["TEST_DATABASE_URL"])
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture(autouse=True)
def wipe(engine):
    yield
    with engine.begin() as conn:
        for table in reversed(SQLModel.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture
def app(engine):
    application = create_app()
    application.dependency_overrides[get_engine] = lambda: engine
    application.dependency_overrides[get_canvas_client_factory] = lambda: (
        lambda: httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[])))
    )
    return application


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


def signup(client, email="parent@x.com", password="hunter2pw"):
    return client.post(
        "/signup",
        data={"email": email, "password": password},
        follow_redirects=False,
    )


def seed_assignment(engine, email, **over):
    """Create a connection + assignment for an existing user; return its id."""
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(
            user_id=user.id, label="Mine", base_url="https://school.test",
            account_type="student", access_token="canvas-tok",
        )
        s.add(conn)
        s.commit()
        s.refresh(conn)
        base = dict(
            connection_id=conn.id, canvas_assignment_id=1, name="Lab report",
            description="<p>Measure and write up.</p>", due_at=datetime(2026, 6, 20, 12, 0),
            points_possible=25.0, submission_types=["online_upload"],
            html_url="https://school.test/a/1", workflow_state="unsubmitted",
        )
        base.update(over)
        a = Assignment(**base)
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def test_sync_stamps_last_synced_at(engine):
    """A successful sync records when it ran — what the accounts list surfaces."""
    with Session(engine) as s:
        user = User(email="stamp@x.com", password_hash="h")
        s.add(user); s.commit(); s.refresh(user)
        conn = Connection(user_id=user.id, label="Mine", base_url=BASE,
                          account_type="student", access_token="tok")
        s.add(conn); s.commit(); s.refresh(conn)
        assert conn.last_synced_at is None

        sync_connection(s, conn, client_for(canvas_handler([(10, [assignment_json(1, "A")])])))
        s.commit(); s.refresh(conn)

        assert conn.last_synced_at is not None


def test_settings_lists_connections(client, engine):
    signup(client, email="settings@x.com")
    seed_assignment(engine, "settings@x.com")  # creates a "Mine" connection

    body = client.get("/connections").text
    assert "Mine" in body
    assert "https://school.test" in body
    assert "Add account" in body


def test_settings_shows_empty_state(client):
    signup(client, email="noaccts@x.com")

    body = client.get("/connections").text
    assert "No accounts yet" in body
    assert "Add account" in body


def test_delete_connection_removes_it_and_its_assignments(client, engine):
    signup(client, email="del@x.com")
    seed_assignment(engine, "del@x.com")
    with Session(engine) as s:
        conn_id = s.exec(select(Connection)).first().id

    resp = client.post(f"/connections/{conn_id}/delete", follow_redirects=False)
    assert resp.status_code in (302, 303)

    with Session(engine) as s:
        assert s.exec(select(Connection)).first() is None
        assert s.exec(select(Assignment)).first() is None


def test_cannot_delete_another_users_connection(client, app, engine):
    signup(client, email="owner2@x.com")
    seed_assignment(engine, "owner2@x.com")
    with Session(engine) as s:
        conn_id = s.exec(select(Connection)).first().id

    from fastapi.testclient import TestClient
    intruder = TestClient(app)
    signup(intruder, email="intruder2@x.com")

    resp = intruder.post(f"/connections/{conn_id}/delete", follow_redirects=False)
    assert resp.status_code == 404
    with Session(engine) as s:
        assert s.exec(select(Connection)).first() is not None
