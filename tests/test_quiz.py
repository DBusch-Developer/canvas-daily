"""Layer 14 - quiz indicator.

Canvas marks quiz assignments with "online_quiz" in submission_types (already
fetched and stored). `Assignment.is_quiz` reads that flag, and every surface -
detail page, dashboard, daily email - labels quizzes from it. Quizzes usually
carry no assignment description, so the detail page shows a quiz-specific message
instead of the generic empty state. Groq/SMTP are not involved here; the web
tests use the FastAPI TestClient against in-memory SQLite, like the htmx layer.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.mailer import build_report_email
from app.models import Assignment, Connection, User
from app.web import create_app, get_session


# ---- is_quiz property (pure) ----

def test_is_quiz_true_for_online_quiz():
    a = Assignment(connection_id=1, canvas_assignment_id=1, name="Pop quiz",
                   submission_types=["online_quiz"])
    assert a.is_quiz is True


def test_is_quiz_false_for_non_quiz_and_empty():
    upload = Assignment(connection_id=1, canvas_assignment_id=2, name="Essay",
                        submission_types=["online_upload"])
    none = Assignment(connection_id=1, canvas_assignment_id=3, name="Reading",
                      submission_types=[])
    assert upload.is_quiz is False
    assert none.is_quiz is False


# ---- web fixtures ----

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


def seed_assignment(client, engine, *, submission_types, description="",
                    email="quiz@x.com", name="Midterm"):
    """Sign up (session cookie) and seed one owned assignment due tomorrow."""
    client.post("/signup", data={"email": email, "password": "hunter2pw"},
                follow_redirects=False)
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=user.id, label="Yavapai", base_url="https://school.test",
                          account_type="student", access_token="canvas-tok")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        a = Assignment(connection_id=conn.id, canvas_assignment_id=1, name=name,
                       description=description,
                       due_at=datetime(2026, 6, 25, 12, 0), points_possible=20.0,
                       submission_types=submission_types,
                       html_url="https://school.test/a/1", workflow_state="unsubmitted")
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


# ---- detail page ----

def test_quiz_detail_shows_pill_and_message(client, engine):
    aid = seed_assignment(client, engine, submission_types=["online_quiz"], description="")
    resp = client.get(f"/assignments/{aid}")
    assert resp.status_code == 200
    assert "Quiz" in resp.text
    assert "This is a quiz" in resp.text
    assert "open it in Canvas to take it" in resp.text
    # The generic empty state is replaced for quizzes.
    assert "No instructions provided" not in resp.text


def test_nonquiz_detail_has_no_quiz_markers(client, engine):
    aid = seed_assignment(client, engine, submission_types=["online_upload"], description="")
    resp = client.get(f"/assignments/{aid}")
    assert resp.status_code == 200
    assert "This is a quiz" not in resp.text
    assert "No instructions provided" in resp.text


def test_quiz_with_description_shows_description_not_message(client, engine):
    aid = seed_assignment(client, engine, submission_types=["online_quiz"],
                          description="<p>Covers chapters 1-3.</p>")
    resp = client.get(f"/assignments/{aid}")
    assert resp.status_code == 200
    assert "Covers chapters 1-3." in resp.text
    assert "This is a quiz" not in resp.text
    # Pill still shows even when a description exists.
    assert "Quiz" in resp.text


# ---- no AI breakdown for quizzes ----

def test_quiz_detail_hides_ai_breakdown(client, engine):
    aid = seed_assignment(client, engine, submission_types=["online_quiz"], description="")
    resp = client.get(f"/assignments/{aid}")
    assert resp.status_code == 200
    assert "Break this down with AI" not in resp.text
    assert 'id="breakdown-dialog"' not in resp.text


def test_nonquiz_detail_still_shows_ai_breakdown(client, engine):
    aid = seed_assignment(client, engine, submission_types=["online_upload"], description="")
    resp = client.get(f"/assignments/{aid}")
    assert resp.status_code == 200
    assert "Break this down with AI" in resp.text


def test_breakdown_route_refuses_for_quiz(client, engine):
    # Defense in depth: even a direct POST must not run a breakdown for a quiz,
    # and must not call Groq (no client mock is provided here).
    aid = seed_assignment(client, engine, submission_types=["online_quiz"], description="")
    resp = client.post(f"/assignments/{aid}/breakdown")
    assert resp.status_code == 400
    assert "quiz" in resp.text.lower()


# ---- dashboard ----

def test_dashboard_card_tags_quiz(client, engine):
    seed_assignment(client, engine, submission_types=["online_quiz"], description="")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "tag--quiz" in resp.text


def test_dashboard_card_no_tag_for_nonquiz(client, engine):
    seed_assignment(client, engine, submission_types=["online_upload"], description="")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "tag--quiz" not in resp.text


# ---- email ----

def test_email_marks_quiz_line(engine):
    with Session(engine) as s:
        user = User(email="e@x.com", password_hash="x")
        s.add(user)
        s.commit()
        s.refresh(user)
        conn = Connection(user_id=user.id, label="Yavapai", base_url="https://school.test",
                          account_type="student", access_token="t")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        now = datetime(2026, 6, 24, 8, 0)
        s.add(Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Quiz 1",
                         submission_types=["online_quiz"],
                         due_at=now + timedelta(days=1), workflow_state="unsubmitted"))
        s.add(Assignment(connection_id=conn.id, canvas_assignment_id=2, name="Essay 1",
                         submission_types=["online_upload"],
                         due_at=now + timedelta(days=1), workflow_state="unsubmitted"))
        s.commit()
        subject, body = build_report_email(s, user, now)

    assert "Quiz 1 (Quiz) — due" in body
    assert "Essay 1 — due" in body
    assert "Essay 1 (Quiz)" not in body
