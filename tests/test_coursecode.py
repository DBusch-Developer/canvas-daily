"""Layer 15 - class (course code) on the dashboard card.

fetch_courses captures each course's code, sync stores it on the assignment, and
the dashboard card shows it as a small class line. Canvas is mocked at the httpx
transport boundary; the web test uses the FastAPI TestClient against in-memory
SQLite, so this layer runs without a Neon branch.
"""

from datetime import datetime

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.canvas import fetch_courses
from app.models import Assignment, Connection, User
from app.sync import sync_connection
from app.web import create_app, get_session

BASE = "https://school.test"


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def in_memory_engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


def assignment_json(canvas_id, name):
    return {"id": canvas_id, "name": name, "due_at": "2026-06-20T23:59:00Z",
            "points_possible": 100, "submission_types": ["online_text_entry"],
            "html_url": f"{BASE}/a/{canvas_id}", "description": "<p>Do it.</p>"}


def canvas_handler(courses):
    """courses: list of (course_id, course_code, [assignment_json, ...])."""
    def handler(request):
        path = request.url.path
        if path.endswith("/courses"):
            return httpx.Response(200, json=[
                {"id": cid, "name": f"Course {cid}", "course_code": code}
                for cid, code, _ in courses
            ])
        for cid, code, assignments in courses:
            if path.endswith(f"/courses/{cid}/assignments"):
                return httpx.Response(200, json=assignments)
        return httpx.Response(200, json=[])
    return handler


# ---- fetch_courses captures the code ----

def test_fetch_courses_includes_course_code():
    def handler(request):
        return httpx.Response(200, json=[
            {"id": 1, "name": "Biology", "course_code": "BIO 101"},
            {"id": 2, "name": "Untitled"},  # no course_code
        ])
    courses = fetch_courses(BASE, "tok", client_for(handler))
    assert courses[0]["code"] == "BIO 101"
    assert not courses[1]["code"]  # missing course_code -> falsy


# ---- sync stores the code on the assignment ----

def test_sync_stores_course_code():
    eng = in_memory_engine()
    with Session(eng) as s:
        user = User(email="cc@x.com", password_hash="h")
        s.add(user)
        s.commit()
        s.refresh(user)
        conn = Connection(user_id=user.id, label="Mine", base_url=BASE,
                          account_type="student", access_token="tok")
        s.add(conn)
        s.commit()
        s.refresh(conn)

        sync_connection(s, conn, client_for(canvas_handler(
            [(10, "BIO 101", [assignment_json(1, "Lab")])])))

        stored = s.exec(
            select(Assignment).where(Assignment.connection_id == conn.id)).one()
        assert stored.course_code == "BIO 101"


# ---- web: the card shows / omits the class line ----

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


def seed(client, engine, *, course_code, email="card@x.com"):
    client.post("/signup", data={"email": email, "password": "hunter2pw"},
                follow_redirects=False)
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=user.id, label="Yavapai", base_url=BASE,
                          account_type="student", access_token="tok")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        a = Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Lab",
                       due_at=datetime(2026, 6, 25, 12, 0), points_possible=20.0,
                       submission_types=["online_upload"], html_url=f"{BASE}/a/1",
                       workflow_state="unsubmitted", course_code=course_code)
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def test_card_shows_course_code(client, engine):
    seed(client, engine, course_code="BIO 101")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "card__class" in resp.text
    assert "BIO 101" in resp.text


def test_card_omits_class_when_no_code(client, engine):
    seed(client, engine, course_code="")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "card__class" not in resp.text
