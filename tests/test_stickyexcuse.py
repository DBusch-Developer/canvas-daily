"""Layer 27 — a manual excuse survives the daily sync.

You press Mark excused and the assignment leaves Past due for Completed. But the
nightly sync re-fetches every assignment straight from Canvas, which knows nothing
about your manual excuse, so it used to stamp the Canvas copy back over yours and
the item reappeared as past due the next morning.

The manual excuse is sticky: it is a separate, user-owned flag the sync never
touches. After a full sync of Canvas's (un-excused) copy, the assignment stays
Completed. In-memory SQLite + a mocked Canvas client; no Neon, no TEST_DATABASE_URL.
"""

from datetime import datetime

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Assignment, Connection, User
from app.reports import excuse_assignment, report_for_user
from app.sync import sync_connection
from app.web import create_app, get_session

BASE = "https://school.test"
NOW = datetime(2026, 6, 17, 12, 0)
PAST = datetime(2026, 6, 10, 9, 0)  # clearly past due vs NOW


def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


def canvas_client_missing(canvas_id):
    """A Canvas that reports the assignment as a plain missing item — no
    submission, and crucially NOT excused on Canvas's side."""
    def handler(request):
        path = request.url.path
        if path.endswith("/courses"):
            return httpx.Response(200, json=[{"id": 10, "name": "Course 10"}])
        if path.endswith("/courses/10/assignments"):
            return httpx.Response(200, json=[{
                "id": canvas_id, "name": "Late lab",
                "due_at": "2026-06-10T09:00:00Z", "points_possible": 100,
                "submission_types": ["online_text_entry"],
                "html_url": f"{BASE}/a/{canvas_id}", "description": "<p>Do it.</p>",
            }])
        return httpx.Response(200, json=[])
    return httpx.Client(transport=httpx.MockTransport(handler))


def seed_past_due_missing(s):
    """One user, one connection, one past-due missing assignment. Returns ids."""
    user = User(email="x@x.com", password_hash="h")
    s.add(user); s.commit(); s.refresh(user)
    conn = Connection(user_id=user.id, label="Mine", base_url=BASE,
                      account_type="student", access_token="tok")
    s.add(conn); s.commit(); s.refresh(conn)
    a = Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Late lab",
                   due_at=PAST, submission_types=[], html_url=f"{BASE}/a/1",
                   description="", missing=True)
    s.add(a); s.commit(); s.refresh(a)
    return user.id, conn, a.id


def test_manual_excuse_survives_the_daily_sync():
    with Session(engine()) as s:
        user_id, conn, assignment_id = seed_past_due_missing(s)

        excuse_assignment(s, assignment_id)
        assert report_for_user(s, user_id, NOW)["past_due"] == []

        # The nightly sync re-fetches Canvas's un-excused copy.
        sync_connection(s, conn, canvas_client_missing(1))
        s.commit()

        after = report_for_user(s, user_id, NOW)
        assert after["past_due"] == []           # excuse must still hold
        assert len(after["completed"]) == 1


@pytest.fixture
def web_engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture
def client(web_engine):
    from fastapi.testclient import TestClient
    app = create_app()

    def _get_session():
        with Session(web_engine) as s:
            yield s

    app.dependency_overrides[get_session] = _get_session
    return TestClient(app)


def test_detail_page_hides_excuse_button_once_manually_excused(client, web_engine):
    """A manual excuse is 'done' everywhere — the detail page must stop offering
    Mark excused, just as it does for submitted or graded work."""
    client.post("/signup", data={"email": "stu@x.com", "password": "hunter2pw"},
                follow_redirects=False)
    with Session(web_engine) as s:
        user = s.exec(select(User).where(User.email == "stu@x.com")).one()
        conn = Connection(user_id=user.id, label="Mine", base_url=BASE,
                          account_type="student", access_token="tok")
        s.add(conn); s.commit(); s.refresh(conn)
        a = Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Late lab",
                       due_at=PAST, submission_types=[], html_url=f"{BASE}/a/1",
                       description="", missing=True)
        s.add(a); s.commit(); s.refresh(a)
        aid = a.id

    assert "Mark excused" in client.get(f"/assignments/{aid}").text

    client.post(f"/assignments/{aid}/excuse", follow_redirects=False)

    assert "Mark excused" not in client.get(f"/assignments/{aid}").text
