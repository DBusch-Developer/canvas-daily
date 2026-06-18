"""Layer 10 — background sync + account setup page (Canvas mocked, Neon test branch)."""

import logging
import os
from datetime import datetime

import httpx
import pytest
from sqlmodel import Session, SQLModel, select

from app.db import make_engine
from app.models import Assignment, Connection, User

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (setup-flow tests need a Neon test branch)",
)

BASE = "https://school.test"


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


def a_user_and_connection(engine, email="d@x.com", token="tok"):
    """Create a user + one connection; return the connection id."""
    with Session(engine) as s:
        user = User(email=email, password_hash="h")
        s.add(user); s.commit(); s.refresh(user)
        conn = Connection(user_id=user.id, label="Mine", base_url=BASE,
                          account_type="student", access_token=token)
        s.add(conn); s.commit(); s.refresh(conn)
        return conn.id


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def canvas_ok(request):
    path = request.url.path
    if path.endswith("/courses"):
        return httpx.Response(200, json=[{"id": 10, "name": "Bio"}])
    if path.endswith("/courses/10/assignments"):
        return httpx.Response(200, json=[{
            "id": 1, "name": "Lab report", "due_at": "2026-06-20T23:59:00Z",
            "points_possible": 25, "submission_types": ["online_upload"],
            "html_url": f"{BASE}/a/1", "description": "<p>Do it.</p>",
        }])
    return httpx.Response(200, json=[])


def test_new_connection_defaults_to_pending(engine):
    conn_id = a_user_and_connection(engine)
    with Session(engine) as s:
        assert s.get(Connection, conn_id).sync_status == "pending"


def test_background_sync_stores_and_marks_ok(engine):
    from app.web import run_connection_sync
    conn_id = a_user_and_connection(engine)

    run_connection_sync(engine, conn_id, lambda: client_for(canvas_ok))

    with Session(engine) as s:
        conn = s.get(Connection, conn_id)
        assert conn.sync_status == "ok"
        assert conn.last_synced_at is not None
        stored = s.exec(select(Assignment).where(Assignment.connection_id == conn_id)).all()
        assert len(stored) == 1


def test_background_sync_keeps_connection_and_marks_error(engine, caplog):
    from app.web import run_connection_sync
    conn_id = a_user_and_connection(engine, token="secret-token")

    def boom(request):
        return httpx.Response(401, json={"errors": ["bad token"]})

    with caplog.at_level(logging.WARNING):
        run_connection_sync(engine, conn_id, lambda: client_for(boom))

    with Session(engine) as s:
        conn = s.get(Connection, conn_id)
        assert conn is not None
        assert conn.sync_status == "error"
    assert "secret-token" not in caplog.text


@pytest.fixture
def app(engine):
    from app.web import create_app, get_engine, get_canvas_client_factory
    application = create_app()
    application.dependency_overrides[get_engine] = lambda: engine
    application.dependency_overrides[get_canvas_client_factory] = lambda: (
        lambda: client_for(canvas_ok)
    )
    return application


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


def signup(client, email="parent@x.com", password="hunter2pw"):
    return client.post("/signup", data={"email": email, "password": password},
                       follow_redirects=False)


def add_form(client, label="Mine"):
    return client.post("/connections", data={
        "label": label, "base_url": BASE,
        "account_type": "student", "access_token": "tok",
    }, follow_redirects=False)


def test_add_connection_redirects_to_setup(client, engine):
    signup(client, email="setup@x.com")
    resp = add_form(client)
    assert resp.status_code in (302, 303)
    with Session(engine) as s:
        conn_id = s.exec(select(Connection)).first().id
    assert resp.headers["location"] == f"/connections/{conn_id}/setup"


def test_assignments_appear_after_background_sync(client, engine):
    signup(client, email="bg@x.com")
    add_form(client)  # TestClient runs the background task after the response
    body = client.get("/").text
    assert "Lab report" in body


def test_add_connection_failure_marks_error_and_keeps_it(client, app, engine):
    signup(client, email="bgfail@x.com")
    app.dependency_overrides[
        __import__("app.web", fromlist=["get_canvas_client_factory"]).get_canvas_client_factory
    ] = lambda: (lambda: client_for(lambda r: httpx.Response(401, json={"e": 1})))
    add_form(client)
    with Session(engine) as s:
        conn = s.exec(select(Connection)).first()
        assert conn is not None
        assert conn.sync_status == "error"
