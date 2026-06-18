"""Layer 11 — completed work in its own section (Neon test branch + TestClient)."""

import os
from datetime import datetime

import httpx
import pytest
from sqlmodel import Session, SQLModel, select

from app.db import make_engine
from app.mailer import build_report_email
from app.models import Assignment, Connection, User
from app.reports import report_for_user
from app.web import create_app, get_canvas_client_factory, get_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (completed-section tests need a Neon test branch)",
)

NOW = datetime(2026, 6, 17, 12, 0)
PAST = datetime(2026, 6, 10, 9, 0)          # clearly past, even vs the real clock
TODAY_LATER = datetime(2026, 6, 17, 18, 0)  # same calendar day as NOW, later
FUTURE = datetime(2030, 1, 1, 9, 0)


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
    return client.post("/signup", data={"email": email, "password": password},
                       follow_redirects=False)


def _user(session, email):
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        user = User(email=email, password_hash="h")
        session.add(user); session.commit(); session.refresh(user)
    return user


def seed(engine, email, *, cid=1, name="A", due_at=PAST, **fields):
    """Create (user if needed) + a fresh connection + one assignment. Return assignment id."""
    with Session(engine) as s:
        user = _user(s, email)
        conn = Connection(user_id=user.id, label="Mine", base_url="https://school.test",
                          account_type="student", access_token="tok")
        s.add(conn); s.commit(); s.refresh(conn)
        a = Assignment(connection_id=conn.id, canvas_assignment_id=cid, name=name,
                       due_at=due_at, submission_types=[], html_url="https://school.test/a/1",
                       description="", **fields)
        s.add(a); s.commit(); s.refresh(a)
        return a.id


def _buckets(engine, email):
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        return report_for_user(s, user.id, NOW)


def test_submitted_past_due_goes_to_completed(engine):
    seed(engine, "a@x.com", due_at=PAST, submitted_at=datetime(2026, 6, 9, 8, 0))
    b = _buckets(engine, "a@x.com")
    assert len(b["completed"]) == 1
    assert b["past_due"] == []


def test_graded_goes_to_completed(engine):
    seed(engine, "b@x.com", due_at=PAST, workflow_state="graded")
    b = _buckets(engine, "b@x.com")
    assert len(b["completed"]) == 1
    assert b["past_due"] == []


def test_excused_goes_to_completed(engine):
    seed(engine, "c@x.com", due_at=PAST, excused=True)
    b = _buckets(engine, "c@x.com")
    assert len(b["completed"]) == 1
    assert b["past_due"] == []


def test_missing_past_due_stays_in_past_due(engine):
    seed(engine, "d@x.com", due_at=PAST, missing=True)
    b = _buckets(engine, "d@x.com")
    assert len(b["past_due"]) == 1
    assert b["completed"] == []


def test_not_done_due_today_stays_in_due_today(engine):
    seed(engine, "e@x.com", due_at=TODAY_LATER)
    b = _buckets(engine, "e@x.com")
    assert len(b["due_today"]) == 1
    assert b["completed"] == []


def test_completed_item_is_separated_from_the_board(client, engine):
    signup(client, email="dash@x.com")
    todo_id = seed(engine, "dash@x.com", cid=1, name="Todo lab", due_at=PAST, missing=True)
    done_id = seed(engine, "dash@x.com", cid=2, name="Done lab", due_at=PAST,
                   submitted_at=datetime(2026, 6, 9, 8, 0))

    body = client.get("/").text

    # The not-done item is in the board with its detail link.
    assert f"/assignments/{todo_id}" in body
    assert "Todo lab" in body
    # The completed item shows in the Completed disclosure, with NO detail link.
    assert "Completed (1)" in body
    assert "Done lab" in body
    assert f"/assignments/{done_id}" not in body


def test_no_completed_disclosure_when_none_completed(client, engine):
    signup(client, email="nodone@x.com")
    seed(engine, "nodone@x.com", name="Todo only", due_at=PAST, missing=True)

    body = client.get("/").text
    assert "Todo only" in body
    assert "Completed (" not in body


def test_email_excludes_completed_from_body_and_total(engine):
    seed(engine, "mail@x.com", cid=1, name="Todo", due_at=PAST, missing=True)
    seed(engine, "mail@x.com", cid=2, name="Done", due_at=PAST,
         submitted_at=datetime(2026, 6, 9, 8, 0))

    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == "mail@x.com")).one()
        subject, body = build_report_email(s, user, NOW)

    assert "Todo" in body
    assert "Done" not in body
    assert "— 1 assignment" in subject   # only the not-done item is counted
