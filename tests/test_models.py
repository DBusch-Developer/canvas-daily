import os
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, select

from app.db import make_engine
from app.models import Assignment, Connection, User
from app.reports import report_for_user

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (integration tests need a Neon test branch)",
)


@pytest.fixture(scope="module")
def engine():
    eng = make_engine(os.environ["TEST_DATABASE_URL"])
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture
def session(engine):
    """Each test runs in a transaction that is rolled back — no leftover rows."""
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn)
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def a_user(session, email):
    user = User(email=email, password_hash="hash")
    session.add(user)
    session.flush()
    return user


def a_connection(session, user_id, label="Mine", token="canvas-token"):
    conn = Connection(
        user_id=user_id, label=label, base_url="https://school.test",
        account_type="observer", access_token=token,
    )
    session.add(conn)
    session.flush()
    return conn


def an_assignment(connection_id, **over):
    base = dict(
        connection_id=connection_id, canvas_assignment_id=1, name="Essay",
        description="<p>clean</p>", due_at=datetime(2026, 6, 20, 12, 0, 0),
        points_possible=100.0, submission_types=["online_text_entry"],
        html_url="https://school.test/a/1", workflow_state="unsubmitted",
        score=None, submitted_at=None, late=False, missing=False, excused=False,
        fetched_at=datetime(2026, 6, 15, 0, 0, 0),
    )
    base.update(over)
    return Assignment(**base)


def test_user_owns_many_connections(session):
    user = a_user(session, "parent@x.com")
    for label in ("Mine", "Kid A", "Kid B"):
        a_connection(session, user.id, label=label)

    session.refresh(user)
    assert len(user.connections) == 3


def test_connection_owns_many_assignments(session):
    user = a_user(session, "many@x.com")
    conn = a_connection(session, user.id)
    for i in range(4):
        session.add(an_assignment(conn.id, canvas_assignment_id=i))
    session.flush()

    found = session.exec(
        select(Assignment).where(Assignment.connection_id == conn.id)
    ).all()
    assert len(found) == 4


def test_deleting_connection_cascades_to_assignments(session):
    user = a_user(session, "cascade@x.com")
    conn = a_connection(session, user.id)
    session.add(an_assignment(conn.id))
    session.flush()
    conn_id = conn.id

    session.delete(conn)
    session.flush()

    remaining = session.exec(
        select(Assignment).where(Assignment.connection_id == conn_id)
    ).all()
    assert remaining == []


def test_stored_assignment_round_trips(session):
    user = a_user(session, "round@x.com")
    conn = a_connection(session, user.id)
    saved = an_assignment(
        conn.id, name="Lab report", submission_types=["online_upload", "online_text_entry"],
        due_at=datetime(2026, 7, 1, 9, 0, 0), score=None, points_possible=25.0,
    )
    session.add(saved)
    session.flush()
    assignment_id = saved.id

    session.expunge_all()  # force a real reload from the database
    got = session.get(Assignment, assignment_id)

    assert got.name == "Lab report"
    assert got.submission_types == ["online_upload", "online_text_entry"]
    assert got.due_at == datetime(2026, 7, 1, 9, 0, 0)
    assert got.points_possible == 25.0
    assert got.score is None  # null until graded — never coerced to 0
    assert got.late is False


def test_access_token_encrypted_at_rest(session):
    user = a_user(session, "secret@x.com")
    secret = "canvas-token-SUPER-SECRET-xyz"
    conn = a_connection(session, user.id, token=secret)
    conn_id = conn.id

    # The raw stored column is ciphertext, not the plaintext token.
    raw = session.execute(
        text("select access_token from connections where id = :id"),
        {"id": conn_id},
    ).scalar_one()
    assert raw != secret
    assert secret not in raw

    # Reading back through the ORM decrypts it.
    session.expunge_all()
    got = session.get(Connection, conn_id)
    assert got.access_token == secret


def test_report_groups_by_status_and_sorts_by_due_date(session):
    now = datetime(2026, 6, 15, 12, 0, 0)
    user = a_user(session, "report@x.com")
    conn = a_connection(session, user.id)
    session.add_all([
        an_assignment(conn.id, canvas_assignment_id=1, name="Past-older", due_at=datetime(2026, 6, 10, 9, 0)),
        an_assignment(conn.id, canvas_assignment_id=2, name="Past-newer", due_at=datetime(2026, 6, 14, 9, 0)),
        an_assignment(conn.id, canvas_assignment_id=3, name="Today", due_at=datetime(2026, 6, 15, 18, 0)),
        an_assignment(conn.id, canvas_assignment_id=4, name="Soon", due_at=datetime(2026, 6, 16, 9, 0)),
        an_assignment(conn.id, canvas_assignment_id=5, name="Later", due_at=datetime(2026, 6, 20, 9, 0)),
    ])
    session.flush()

    report = report_for_user(session, user.id, now)

    assert [a.name for a in report["past_due"]] == ["Past-older", "Past-newer"]
    assert [a.name for a in report["due_today"]] == ["Today"]
    assert [a.name for a in report["upcoming"]] == ["Soon", "Later"]
