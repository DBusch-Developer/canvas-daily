"""Layer 37 — manual "Sync now" per account.

The accounts page gets a Sync now button on each connection that kicks off the
same background pull used when a connection is first added — so a user can
refresh one account on demand instead of waiting for the daily job. The route
schedules the work in a FastAPI BackgroundTask and redirects back to the
accounts list; it is login- and ownership-guarded.

In-memory SQLite + StaticPool + mocked Canvas at the httpx transport boundary.
No Neon, no TEST_DATABASE_URL required.
"""

import httpx
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Connection, User
from app.web import (
    create_app,
    get_canvas_client_factory,
    get_engine,
    get_session,
)
from fastapi.testclient import TestClient

BASE = "https://school.test"


def _make_sqlite_engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


def _empty_canvas(request):
    return httpx.Response(200, json=[])


def _make_app(engine, canvas_handler=_empty_canvas):
    application = create_app()

    def _get_session():
        with Session(engine) as s:
            yield s

    application.dependency_overrides[get_session] = _get_session
    application.dependency_overrides[get_engine] = lambda: engine

    def _canvas_factory():
        return lambda: httpx.Client(transport=httpx.MockTransport(canvas_handler))

    application.dependency_overrides[get_canvas_client_factory] = _canvas_factory
    return application


def _signup(client, email, password="hunter2pw"):
    return client.post("/signup", data={"email": email, "password": password},
                       follow_redirects=False)


def _seed_connection(engine, user_email, status=None):
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == user_email)).one()
        conn = Connection(
            user_id=user.id, label="Mine", base_url=BASE,
            account_type="student", access_token="tok", sync_status=status,
        )
        s.add(conn)
        s.commit()
        s.refresh(conn)
        return conn.id


# ---------------------------------------------------------------------------
# Tests — each fails before the route exists
# ---------------------------------------------------------------------------

def test_sync_now_redirects_to_connections():
    """POST /connections/{id}/sync returns 303 back to the accounts list.

    Fails before impl: the route does not exist (404/405).
    """
    engine = _make_sqlite_engine()
    client = TestClient(_make_app(engine))
    _signup(client, "a@test.com")
    conn_id = _seed_connection(engine, "a@test.com")

    resp = client.post(f"/connections/{conn_id}/sync", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers.get("location", "").endswith("/connections")


def test_sync_now_runs_background_sync():
    """The background pull runs before TestClient returns and records success.

    Fails before impl: the route does not exist, so no sync runs.
    """
    engine = _make_sqlite_engine()
    client = TestClient(_make_app(engine))
    _signup(client, "b@test.com")
    conn_id = _seed_connection(engine, "b@test.com")

    client.post(f"/connections/{conn_id}/sync", follow_redirects=False)

    with Session(engine) as s:
        conn = s.get(Connection, conn_id)
    assert conn.sync_status == "ok", "Background sync must run and record success"


def test_sync_now_requires_login():
    """An anonymous POST is bounced to the login page, not run.

    Fails before impl: the route does not exist (404).
    """
    engine = _make_sqlite_engine()
    client = TestClient(_make_app(engine))
    # seed a connection under a real user, but post without logging in
    _signup(client, "owner@test.com")
    conn_id = _seed_connection(engine, "owner@test.com")
    client.cookies.clear()

    resp = client.post(f"/connections/{conn_id}/sync", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers.get("location", "").endswith("/login")


def test_sync_now_owner_succeeds_but_other_user_is_rejected():
    """The owner can sync (303); a different user cannot (404).

    Fails before impl: the route does not exist, so even the owner's POST 404s
    instead of redirecting.
    """
    engine = _make_sqlite_engine()
    client = TestClient(_make_app(engine))
    _signup(client, "owner2@test.com")
    others_conn = _seed_connection(engine, "owner2@test.com")

    owner_resp = client.post(f"/connections/{others_conn}/sync", follow_redirects=False)
    assert owner_resp.status_code == 303, "Owner must be able to sync their connection"

    # Log in as a different user
    client.cookies.clear()
    _signup(client, "intruder@test.com")

    intruder_resp = client.post(f"/connections/{others_conn}/sync", follow_redirects=False)
    assert intruder_resp.status_code == 404, "A user cannot sync someone else's connection"
