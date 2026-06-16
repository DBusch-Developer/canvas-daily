import os
from datetime import datetime

import pytest
from sqlmodel import Session, SQLModel

from app.db import make_engine
from app.mailer import build_report_email, send_daily_reports, send_email
from app.models import Assignment, Connection, User

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (mailer tests need a Neon test branch)",
)


class FakeSMTP:
    """Stands in for smtplib.SMTP — records messages instead of sending."""

    def __init__(self):
        self.messages = []

    def send_message(self, msg):
        self.messages.append(msg)


@pytest.fixture(scope="module")
def engine():
    eng = make_engine(os.environ["TEST_DATABASE_URL"])
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture
def session(engine):
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
    user = User(email=email, password_hash="h")
    session.add(user)
    session.flush()
    return user


def a_connection(session, user_id, label="Mine", token="canvas-tok"):
    conn = Connection(user_id=user_id, label=label, base_url="https://school.test",
                      account_type="student", access_token=token)
    session.add(conn)
    session.flush()
    return conn


def an_assignment(connection_id, canvas_id, name, due):
    return Assignment(connection_id=connection_id, canvas_assignment_id=canvas_id,
                      name=name, due_at=due, submission_types=[], html_url="",
                      description="", workflow_state="unsubmitted")


def test_build_email_groups_sorts_and_labels_by_connection(session):
    now = datetime(2026, 6, 15, 12, 0, 0)
    user = a_user(session, "report@x.com")
    mine = a_connection(session, user.id, label="Mine")
    kid = a_connection(session, user.id, label="Kid A")
    session.add(an_assignment(mine.id, 1, "Essay", datetime(2026, 6, 10, 9, 0)))
    session.add(an_assignment(mine.id, 2, "Lab", datetime(2026, 6, 14, 9, 0)))
    session.add(an_assignment(kid.id, 3, "Quiz", datetime(2030, 1, 1, 9, 0)))
    session.flush()

    subject, body = build_report_email(session, user, now)

    assert body.index("Past due") < body.index("Upcoming")
    assert body.index("Essay") < body.index("Lab")  # sorted by due date
    assert "Mine" in body and "Kid A" in body       # labeled by connection
    assert "Quiz" in body
    assert "3" in subject                            # count of assignments


def test_email_never_contains_the_access_token(session):
    now = datetime(2026, 6, 15, 12, 0, 0)
    user = a_user(session, "leak@x.com")
    conn = a_connection(session, user.id, label="L", token="SUPER-SECRET-TOKEN")
    session.add(an_assignment(conn.id, 1, "Essay", datetime(2026, 6, 10, 9, 0)))
    session.flush()

    _, body = build_report_email(session, user, now)
    assert "SUPER-SECRET-TOKEN" not in body


def test_send_email_hands_a_message_to_smtp(session):
    smtp = FakeSMTP()
    send_email(smtp, "from@cd.test", "to@x.com", "Your report", "Body text here")

    assert len(smtp.messages) == 1
    msg = smtp.messages[0]
    assert msg["To"] == "to@x.com"
    assert msg["From"] == "from@cd.test"
    assert msg["Subject"] == "Your report"
    assert "Body text here" in msg.get_content()


def test_send_daily_reports_one_email_per_user(session):
    now = datetime(2026, 6, 15, 12, 0, 0)
    for email in ("a@x.com", "b@x.com"):
        user = a_user(session, email)
        conn = a_connection(session, user.id)
        session.add(an_assignment(conn.id, 1, "Essay", datetime(2026, 6, 10, 9, 0)))
    session.flush()

    smtp = FakeSMTP()
    sent = send_daily_reports(session, smtp, "from@cd.test", now)

    assert sent == 2
    assert {m["To"] for m in smtp.messages} == {"a@x.com", "b@x.com"}
