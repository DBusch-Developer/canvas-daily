import os
from datetime import datetime

import httpx
import pytest
from sqlmodel import Session, SQLModel, select

from app.canvas import fetch_courses
from app.db import make_engine
from app.models import Assignment, Connection, User
from app.sync import run_daily_sync, sync_connection

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (sync tests need a Neon test branch)",
)

BASE = "https://school.test"


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def assignment_json(canvas_id, name):
    return {
        "id": canvas_id, "name": name, "due_at": "2026-06-20T23:59:00Z",
        "points_possible": 100, "submission_types": ["online_text_entry"],
        "html_url": f"{BASE}/a/{canvas_id}", "description": "<p>Do it.</p>",
    }


def canvas_handler(courses):
    """courses: list of (course_id, [assignment_json, ...])."""
    def handler(request):
        path = request.url.path
        if path.endswith("/courses"):
            return httpx.Response(200, json=[{"id": cid, "name": f"Course {cid}"} for cid, _ in courses])
        for cid, assignments in courses:
            if path.endswith(f"/courses/{cid}/assignments"):
                return httpx.Response(200, json=assignments)
        return httpx.Response(200, json=[])
    return handler


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


def a_connection(session, user_id, label="Mine", base_url=BASE, token="tok"):
    conn = Connection(user_id=user_id, label=label, base_url=base_url,
                      account_type="student", access_token=token)
    session.add(conn)
    session.flush()
    return conn


def test_fetch_courses_follows_pagination():
    def handler(request):
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[{"id": 2, "name": "Two"}])
        return httpx.Response(
            200, json=[{"id": 1, "name": "One"}],
            headers={"Link": f'<{BASE}/api/v1/courses?page=2>; rel="next"'},
        )

    courses = fetch_courses(BASE, "tok", client_for(handler))
    assert [c["id"] for c in courses] == [1, 2]


def test_sync_stores_assignments_across_courses(session):
    user = a_user(session, "sync@x.com")
    conn = a_connection(session, user.id)
    courses = [(10, [assignment_json(1, "A")]),
               (11, [assignment_json(2, "B"), assignment_json(3, "C")])]

    sync_connection(session, conn, client_for(canvas_handler(courses)))

    stored = session.exec(
        select(Assignment).where(Assignment.connection_id == conn.id)
    ).all()
    assert {a.canvas_assignment_id for a in stored} == {1, 2, 3}
    # Stored detail is sanitized and dates normalized (naive UTC).
    one = next(a for a in stored if a.canvas_assignment_id == 1)
    assert "<p>" not in one.description or one.description == "<p>Do it.</p>"
    assert one.due_at == datetime(2026, 6, 20, 23, 59, 0)


def test_sync_upserts_without_duplicating(session):
    user = a_user(session, "upsert@x.com")
    conn = a_connection(session, user.id)
    holder = {"courses": [(10, [assignment_json(1, "Old name")])]}

    def handler(request):
        return canvas_handler(holder["courses"])(request)

    sync_connection(session, conn, client_for(handler))
    holder["courses"] = [(10, [assignment_json(1, "New name")])]
    sync_connection(session, conn, client_for(handler))

    stored = session.exec(
        select(Assignment).where(
            Assignment.connection_id == conn.id,
            Assignment.canvas_assignment_id == 1,
        )
    ).all()
    assert len(stored) == 1
    assert stored[0].name == "New name"


def test_run_daily_sync_covers_every_connection(session):
    user = a_user(session, "all@x.com")
    c1 = a_connection(session, user.id, label="A", base_url="https://a.test", token="ta")
    c2 = a_connection(session, user.id, label="B", base_url="https://b.test", token="tb")

    run_daily_sync(session, client_for(canvas_handler([(10, [assignment_json(1, "X")])])))

    for conn in (c1, c2):
        stored = session.exec(
            select(Assignment).where(Assignment.connection_id == conn.id)
        ).all()
        assert len(stored) == 1
