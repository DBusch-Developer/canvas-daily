import os
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, select

from app.auth import verify_password
from app.db import make_engine
from app.models import Assignment, Connection, User
from app.web import create_app, get_canvas_client, get_groq_client, get_session

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (E2E tests need a Neon test branch)",
)


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
    import httpx
    application = create_app()

    def _get_session():
        with Session(engine) as s:
            yield s

    def _empty_canvas():
        return httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[])
        ))

    application.dependency_overrides[get_session] = _get_session
    application.dependency_overrides[get_canvas_client] = _empty_canvas
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


def test_signup_creates_hashed_user(client, engine):
    resp = signup(client, email="new@x.com", password="s3cret-pw")
    assert resp.status_code in (302, 303)

    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == "new@x.com")).one()
    assert user.password_hash != "s3cret-pw"
    assert verify_password("s3cret-pw", user.password_hash)


def test_login_rejects_wrong_password_accepts_right(client):
    signup(client, email="log@x.com", password="rightpass")
    client.post("/logout", follow_redirects=False)

    bad = client.post("/login", data={"email": "log@x.com", "password": "wrong"}, follow_redirects=False)
    assert bad.status_code == 401

    good = client.post("/login", data={"email": "log@x.com", "password": "rightpass"}, follow_redirects=False)
    assert good.status_code in (302, 303)


def test_add_connection_encrypts_token(client, engine):
    signup(client, email="conn@x.com")
    resp = client.post(
        "/connections",
        data={"label": "Kid A", "base_url": "https://k12.test",
              "account_type": "observer", "access_token": "RAW-CANVAS-TOKEN"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)

    with Session(engine) as s:
        raw = s.execute(text("select access_token from connections")).scalar_one()
    assert "RAW-CANVAS-TOKEN" not in raw


def test_report_groups_and_sorts_by_due_date(client, engine):
    signup(client, email="report@x.com")
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == "report@x.com")).one()
        conn = Connection(user_id=user.id, label="L", base_url="b", account_type="student", access_token="t")
        s.add(conn); s.commit(); s.refresh(conn)
        for cid, name, due in [
            (1, "Past-older", datetime(2026, 6, 10, 9, 0)),
            (2, "Past-newer", datetime(2026, 6, 14, 9, 0)),
            (3, "Soon", datetime(2030, 1, 1, 9, 0)),
        ]:
            s.add(Assignment(connection_id=conn.id, canvas_assignment_id=cid, name=name,
                             due_at=due, submission_types=[], html_url="", description=""))
        s.commit()

    body = client.get("/").text
    # Past-older appears before Past-newer (sorted by due date).
    assert body.index("Past-older") < body.index("Past-newer")
    assert "Soon" in body


def test_detail_page_reads_storage_no_live_canvas(client, engine, monkeypatch):
    signup(client, email="detail@x.com")
    assignment_id = seed_assignment(engine, "detail@x.com")

    def explode(*a, **k):
        raise RuntimeError("live Canvas call on detail page!")

    monkeypatch.setattr("app.canvas.fetch_assignments", explode)

    resp = client.get(f"/assignments/{assignment_id}")
    assert resp.status_code == 200
    assert "Lab report" in resp.text
    assert "Measure and write up." in resp.text


def test_breakdown_button_renders_markdown(client, app, engine):
    import httpx
    signup(client, email="ai@x.com")
    assignment_id = seed_assignment(engine, "ai@x.com")

    markdown = "## What's being asked\nMeasure stuff."

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": markdown}}]})

    app.dependency_overrides[get_groq_client] = lambda: httpx.Client(
        transport=httpx.MockTransport(handler)
    )

    resp = client.post(f"/assignments/{assignment_id}/breakdown")
    assert resp.status_code == 200
    # The apostrophe is HTML-escaped by the template, so match unambiguous text.
    assert "being asked" in resp.text
    assert "Measure stuff." in resp.text


def test_logged_out_user_is_blocked_from_report(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers["location"]


def test_cannot_view_another_users_assignment(client, app, engine):
    signup(client, email="owner@x.com")
    assignment_id = seed_assignment(engine, "owner@x.com")

    from fastapi.testclient import TestClient
    other = TestClient(app)
    signup(other, email="intruder@x.com")

    resp = other.get(f"/assignments/{assignment_id}", follow_redirects=False)
    assert resp.status_code == 404


def test_add_connection_auto_syncs_assignments(client, app, engine):
    import httpx
    signup(client, email="auto@x.com")

    def handler(request):
        path = request.url.path
        if path.endswith("/courses"):
            return httpx.Response(200, json=[{"id": 10, "name": "Bio"}])
        if path.endswith("/courses/10/assignments"):
            return httpx.Response(200, json=[{
                "id": 1, "name": "Lab report", "due_at": "2026-06-20T23:59:00Z",
                "points_possible": 25, "submission_types": ["online_upload"],
                "html_url": "https://school.test/a/1", "description": "<p>Do it.</p>",
            }])
        return httpx.Response(200, json=[])

    app.dependency_overrides[get_canvas_client] = lambda: httpx.Client(
        transport=httpx.MockTransport(handler)
    )

    client.post("/connections", data={
        "label": "Mine", "base_url": "https://school.test",
        "account_type": "student", "access_token": "tok",
    }, follow_redirects=False)

    body = client.get("/").text
    assert "Lab report" in body


def test_add_connection_persists_even_when_sync_fails(client, app, engine):
    import httpx
    signup(client, email="failsync@x.com")

    def boom(request):
        return httpx.Response(401, json={"errors": ["bad token"]})

    app.dependency_overrides[get_canvas_client] = lambda: httpx.Client(
        transport=httpx.MockTransport(boom)
    )

    resp = client.post("/connections", data={
        "label": "Mine", "base_url": "https://school.test",
        "account_type": "student", "access_token": "tok",
    }, follow_redirects=False)
    assert resp.status_code in (302, 303)

    with Session(engine) as s:
        assert s.exec(select(Connection)).first() is not None
    # Dashboard still renders, no crash.
    assert client.get("/").status_code == 200
