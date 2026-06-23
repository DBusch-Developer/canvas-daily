"""Layer 23 — excuse an assignment so it leaves Past due for Completed.

A past-due missing assignment sits in the past_due bucket. Excusing it marks it
done, so the next report drops it out of past_due and into completed. In-memory
SQLite; no Neon, no TEST_DATABASE_URL.
"""

from datetime import datetime

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Assignment, Connection, User
from app.reports import excuse_assignment, report_for_user

NOW = datetime(2026, 6, 17, 12, 0)
PAST = datetime(2026, 6, 10, 9, 0)  # clearly past due vs NOW


def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


def seed_past_due_missing(s):
    """One user, one connection, one past-due missing assignment. Return its id."""
    user = User(email="x@x.com", password_hash="h")
    s.add(user); s.commit(); s.refresh(user)
    conn = Connection(user_id=user.id, label="Mine", base_url="https://school.test",
                      account_type="student", access_token="tok")
    s.add(conn); s.commit(); s.refresh(conn)
    a = Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Late lab",
                   due_at=PAST, submission_types=[], html_url="https://school.test/a/1",
                   description="", missing=True)
    s.add(a); s.commit(); s.refresh(a)
    return user.id, a.id


def test_excuse_moves_assignment_from_past_due_to_completed():
    with Session(engine()) as s:
        user_id, assignment_id = seed_past_due_missing(s)

        before = report_for_user(s, user_id, NOW)
        assert len(before["past_due"]) == 1
        assert before["completed"] == []

        excuse_assignment(s, assignment_id)

        after = report_for_user(s, user_id, NOW)
        assert after["past_due"] == []
        assert len(after["completed"]) == 1
