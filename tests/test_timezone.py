"""Layer 19 - show Canvas due dates in the course's timezone.

Canvas returns due dates in UTC and tells us each course's time_zone. We keep
storing UTC but convert to that zone for display and bucketing, so a due date
Canvas shows as 'Jun 19 by 11:59pm' (Arizona) no longer appears as the next
morning. Canvas is mocked at the transport boundary; the page/bucketing tests use
the FastAPI TestClient against in-memory SQLite.
"""

from datetime import datetime

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.canvas import fetch_courses
from app.dates import to_local
from app.models import Assignment, Connection, User
from app.reports import report_for_user
from app.sync import sync_connection
from app.web import create_app, get_session

BASE = "https://school.test"
PHX = "America/Phoenix"
# 11:59pm June 19 Arizona == 06:59:59 UTC June 20.
DUE_UTC = datetime(2026, 6, 20, 6, 59, 59)


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def in_memory_engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


# ---- to_local (pure) ----

def test_to_local_converts_utc_to_phoenix():
    local = to_local(DUE_UTC, PHX)
    assert (local.year, local.month, local.day) == (2026, 6, 19)
    assert (local.hour, local.minute) == (23, 59)
    assert local.utcoffset().total_seconds() == -7 * 3600


def test_to_local_falls_back_to_utc_for_empty_or_bad_zone():
    assert to_local(DUE_UTC, "").day == 20           # stays UTC (June 20)
    assert to_local(DUE_UTC, "Not/AZone").day == 20  # invalid -> UTC
    assert to_local(None, PHX) is None


# ---- model properties ----

def test_due_display_formats_local_time():
    a = Assignment(connection_id=1, canvas_assignment_id=1, name="x",
                   due_at=DUE_UTC, time_zone=PHX)
    assert a.due_display == "Jun 19, 2026 · 11:59 PM"


def test_due_display_no_due_date():
    a = Assignment(connection_id=1, canvas_assignment_id=2, name="x",
                   due_at=None, time_zone=PHX)
    assert a.due_display == "No due date"


# ---- fetch + sync ----

def test_fetch_courses_includes_time_zone():
    def handler(request):
        return httpx.Response(200, json=[
            {"id": 1, "name": "Bio", "course_code": "BIO 101", "time_zone": PHX}])
    courses = fetch_courses(BASE, "tok", client_for(handler))
    assert courses[0]["time_zone"] == PHX


def test_sync_stores_time_zone():
    eng = in_memory_engine()
    with Session(eng) as s:
        user = User(email="tz@x.com", password_hash="h")
        s.add(user)
        s.commit()
        s.refresh(user)
        conn = Connection(user_id=user.id, label="Mine", base_url=BASE,
                          account_type="student", access_token="tok")
        s.add(conn)
        s.commit()
        s.refresh(conn)

        def handler(request):
            path = request.url.path
            if path.endswith("/courses"):
                return httpx.Response(200, json=[
                    {"id": 10, "name": "Course 10", "course_code": "C10", "time_zone": PHX}])
            if path.endswith("/courses/10/assignments"):
                return httpx.Response(200, json=[{
                    "id": 1, "name": "Lab", "due_at": "2026-06-20T06:59:59Z",
                    "points_possible": 10, "submission_types": ["online_upload"],
                    "html_url": f"{BASE}/a/1", "description": ""}])
            return httpx.Response(200, json=[])

        sync_connection(s, conn, client_for(handler))
        stored = s.exec(select(Assignment).where(Assignment.connection_id == conn.id)).one()
        assert stored.time_zone == PHX


# ---- bucketing in local time ----

def test_due_tonight_local_buckets_as_due_today():
    eng = in_memory_engine()
    with Session(eng) as s:
        user = User(email="b@x.com", password_hash="h")
        s.add(user)
        s.commit()
        s.refresh(user)
        conn = Connection(user_id=user.id, label="Mine", base_url=BASE,
                          account_type="student", access_token="t")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        s.add(Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Tonight",
                         due_at=DUE_UTC, time_zone=PHX,
                         submission_types=["online_upload"], workflow_state="unsubmitted"))
        s.commit()
        # Mid-day June 19 Arizona == 19:00 UTC June 19.
        now = datetime(2026, 6, 19, 19, 0)
        buckets = report_for_user(s, user.id, now)

    assert [a.name for a in buckets["due_today"]] == ["Tonight"]
    assert buckets["upcoming"] == []


# ---- page render ----

@pytest.fixture
def engine():
    eng = in_memory_engine()
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


def seed(client, engine, email="pg@x.com"):
    client.post("/signup", data={"email": email, "password": "hunter2pw"},
                follow_redirects=False)
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=user.id, label="Yavapai", base_url=BASE,
                          account_type="student", access_token="t")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        a = Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Lab",
                       due_at=DUE_UTC, time_zone=PHX, points_possible=20.0,
                       submission_types=["online_upload"], html_url=f"{BASE}/a/1",
                       workflow_state="unsubmitted")
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def test_detail_page_shows_local_due(client, engine):
    aid = seed(client, engine)
    resp = client.get(f"/assignments/{aid}")
    assert resp.status_code == 200
    assert "Jun 19, 2026 · 11:59 PM" in resp.text
    assert "06:59:59" not in resp.text


def test_dashboard_card_shows_local_due(client, engine):
    seed(client, engine)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Jun 19, 2026 · 11:59 PM" in resp.text
    assert "06:59:59" not in resp.text
