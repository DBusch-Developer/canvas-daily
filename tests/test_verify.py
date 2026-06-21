"""Layer 23 — verify the Canvas token at entry (Canvas mocked; handler on Neon test branch)."""

import os

import httpx
import pytest
from sqlmodel import Session, SQLModel, select

from app.canvas import verify_token
from app.db import make_engine
from app.models import Connection, User
from app.web import get_canvas_client_factory

BASE = "https://school.test"


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- verify_token unit (Canvas mocked) ---------------------------------------

def test_verify_token_ok_on_200():
    def canvas(request):
        assert request.url.path.endswith("/api/v1/users/self")
        return httpx.Response(200, json={"id": 1, "name": "A Student"})
    assert verify_token(BASE, "good-token", client_for(canvas)) == "ok"


def test_verify_token_invalid_on_401():
    def canvas(request):
        return httpx.Response(401, json={"errors": [{"message": "Invalid access token."}]})
    assert verify_token(BASE, "bad", client_for(canvas)) == "invalid"


def test_verify_token_unreachable_on_network_error():
    def canvas(request):
        raise httpx.ConnectTimeout("canvas down")
    assert verify_token(BASE, "whatever", client_for(canvas)) == "unreachable"


# --- handler wiring (Neon test branch) ---------------------------------------

pg = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (handler tests need a Neon test branch)",
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


def make_app(engine, canvas_handler):
    from app.web import create_app, get_engine
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_canvas_client_factory] = lambda: (
        lambda: client_for(canvas_handler)
    )
    return app


def client_for_app(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


def signup(client, email):
    return client.post("/signup", data={"email": email, "password": "hunter2pw"},
                       follow_redirects=False)


def add_form(client):
    return client.post("/connections", data={
        "label": "Mine", "base_url": BASE,
        "account_type": "student", "access_token": "tok",
    }, follow_redirects=False)


@pg
def test_add_connection_rejects_bad_token_without_saving(engine):
    app = make_app(engine, lambda r: httpx.Response(401, json={"e": 1}))
    client = client_for_app(app)
    signup(client, "rej@x.com")

    resp = add_form(client)

    assert resp.status_code == 400
    assert "Canvas rejected this access token" in resp.text
    with Session(engine) as s:
        assert s.exec(select(Connection)).first() is None


@pg
def test_add_connection_saves_when_token_is_valid(engine):
    def canvas(request):
        path = request.url.path
        if path.endswith("/api/v1/users/self"):
            return httpx.Response(200, json={"id": 1})
        if path.endswith("/courses"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[])
    app = make_app(engine, canvas)
    client = client_for_app(app)
    signup(client, "ok@x.com")

    resp = add_form(client)

    assert resp.status_code in (302, 303)
    with Session(engine) as s:
        conn = s.exec(select(Connection)).first()
        assert conn is not None
        assert resp.headers["location"] == f"/connections/{conn.id}/setup"
