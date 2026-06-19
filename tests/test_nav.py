"""Layer 20 - responsive top-nav (hamburger).

The top bar gains a hamburger toggle that collapses the nav on small screens.
Here we pin the rendered markup (toggle button + wired nav); the responsive CSS
and the JS toggle are verified live in the browser at phone width. FastAPI
TestClient + in-memory SQLite.
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.web import create_app, get_session


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
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


def test_topbar_has_hamburger_toggle(client, engine):
    client.post("/signup", data={"email": "nav@x.com", "password": "hunter2pw"},
                follow_redirects=False)
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'class="topbar__toggle"' in resp.text
    assert 'aria-expanded="false"' in resp.text
    assert 'aria-controls="primary-nav"' in resp.text


def test_topbar_nav_is_wired_and_keeps_links(client, engine):
    client.post("/signup", data={"email": "nav2@x.com", "password": "hunter2pw"},
                follow_redirects=False)
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'id="primary-nav"' in resp.text
    assert "Dashboard" in resp.text
    assert "Account" in resp.text
    assert "Log out" in resp.text
