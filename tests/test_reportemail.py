"""Layer 22 - branded HTML daily report email.

Renders the daily report as HTML: sections, assignment names linking to their
Canvas Daily detail pages, status pills, a Quiz tag, no access token. send_email
gains an optional HTML alternative. In-memory SQLite; no Neon needed.
"""

from datetime import datetime

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.mailer import build_report_html, send_email
from app.models import Assignment, Connection, User

BASE = "https://cd.test"


def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


def seed(s, *, token="canvas-tok"):
    user = User(email="r@x.com", password_hash="h")
    s.add(user)
    s.commit()
    s.refresh(user)
    conn = Connection(user_id=user.id, label="Yavapai", base_url="https://school.test",
                      account_type="student", access_token=token)
    s.add(conn)
    s.commit()
    s.refresh(conn)

    def add(cid, name, due, **kw):
        s.add(Assignment(connection_id=conn.id, canvas_assignment_id=cid, name=name,
                         due_at=due, submission_types=kw.get("st", ["online_upload"]),
                         course_code=kw.get("code", "CSA250 Intro (1)"),
                         time_zone="America/Phoenix", html_url="https://school.test/a",
                         workflow_state="unsubmitted", missing=kw.get("missing", False)))

    add(1, "Essay", datetime(2026, 6, 10, 6, 59), missing=True)
    add(2, "Pop Quiz", datetime(2030, 1, 1, 6, 59), st=["online_quiz"])
    s.commit()
    return user


NOW = datetime(2026, 6, 15, 19, 0)  # mid-day June 15 Arizona


def test_html_has_sections_and_linked_names():
    with Session(engine()) as s:
        user = seed(s)
        html = build_report_html(s, user, NOW, BASE)
    assert "PAST DUE" in html      # section chips render uppercased
    assert "UPCOMING" in html
    assert "Essay" in html
    assert "Pop Quiz" in html
    assert 'href="https://cd.test/assignments/' in html


def test_html_shows_status_pill_and_quiz_tag():
    with Session(engine()) as s:
        user = seed(s)
        html = build_report_html(s, user, NOW, BASE)
    assert "Missing" in html
    assert ">Quiz<" in html


def test_html_never_contains_the_token():
    with Session(engine()) as s:
        user = seed(s, token="SUPER-SECRET-TOKEN")
        html = build_report_html(s, user, NOW, BASE)
    assert "SUPER-SECRET-TOKEN" not in html


def test_send_email_attaches_html_alternative():
    class FakeSMTP:
        def __init__(self):
            self.messages = []

        def send_message(self, msg):
            self.messages.append(msg)

    smtp = FakeSMTP()
    send_email(smtp, "from@cd.test", "to@x.com", "Subj", "plain body",
               html="<b>rich body</b>")
    msg = smtp.messages[0]
    assert msg.is_multipart()
    html_part = msg.get_body(preferencelist=("html",))
    assert "rich body" in html_part.get_content()
    text_part = msg.get_body(preferencelist=("plain",))
    assert "plain body" in text_part.get_content()
