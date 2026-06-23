"""Layer 26 — "Mark excused" button on the assignment detail page.

The detail page gains a button that POSTs to an excuse endpoint after a native
confirm() prompt. The endpoint excuses the assignment and sends the user back to
the dashboard (303 to /), where the item now sits in Completed instead of Past
due. FastAPI TestClient + in-memory SQLite; no Neon.
"""

from datetime import datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Assignment, Connection, User
from app.web import create_app, get_session

PAST = datetime(2026, 6, 10, 9, 0)  # clearly past due


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


def seed_past_due_missing(engine, email):
    """The signed-up user gets a connection + one past-due missing assignment."""
    from sqlmodel import select
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=user.id, label="Mine", base_url="https://school.test",
                          account_type="student", access_token="tok")
        s.add(conn); s.commit(); s.refresh(conn)
        a = Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Late lab",
                       due_at=PAST, submission_types=[],
                       html_url="https://school.test/a/1", description="", missing=True)
        s.add(a); s.commit(); s.refresh(a)
        return a.id


def signup(client, email="stu@x.com"):
    return client.post("/signup", data={"email": email, "password": "hunter2pw"},
                       follow_redirects=False)


def test_detail_page_has_confirming_excuse_button(client, engine):
    signup(client, "stu@x.com")
    aid = seed_past_due_missing(engine, "stu@x.com")

    body = client.get(f"/assignments/{aid}").text

    assert f'action="/assignments/{aid}/excuse"' in body
    assert "Mark excused" in body
    assert "confirm(" in body  # native confirm prompt before the POST fires


def test_excuse_endpoint_redirects_to_dashboard(client, engine):
    signup(client, "stu@x.com")
    aid = seed_past_due_missing(engine, "stu@x.com")

    resp = client.post(f"/assignments/{aid}/excuse", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_excused_assignment_moves_to_completed_on_the_dashboard(client, engine):
    signup(client, "stu@x.com")
    aid = seed_past_due_missing(engine, "stu@x.com")

    # Before: the item is on the board with its detail link, nothing completed.
    before = client.get("/").text
    assert f"/assignments/{aid}" in before
    assert "Completed (" not in before

    client.post(f"/assignments/{aid}/excuse", follow_redirects=False)

    # After: it has left the board for the Completed disclosure.
    after = client.get("/").text
    assert "Completed (1)" in after
    assert "Late lab" in after
