"""Layer 34 — populate courses table during sync (Ask My Course picker fix).

Nothing in production ever populated the `courses` table, so the Ask My Course
picker was always empty and the feature was unreachable. This layer adds a
Course upsert inside `sync_connection`, keyed on (connection_id, canvas_course_id).

In-memory SQLite + a mocked Canvas client; no Neon, no TEST_DATABASE_URL.
"""

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Connection, Course, User
from app.sync import sync_connection

BASE = "https://school.test"


def make_engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


def canvas_client(courses):
    """courses: list of {"id": ..., "name": ...} dicts.
    Every course gets an empty assignment list so sync_connection completes
    without errors.
    """
    course_ids = {c["id"] for c in courses}

    def handler(request):
        path = request.url.path
        if path.endswith("/courses"):
            return httpx.Response(200, json=courses)
        for cid in course_ids:
            if path.endswith(f"/courses/{cid}/assignments"):
                return httpx.Response(200, json=[])
        return httpx.Response(200, json=[])

    return httpx.Client(transport=httpx.MockTransport(handler))


def seed_connection(session):
    """One user, one connection. Returns the Connection object."""
    user = User(email="u@test.com", password_hash="h")
    session.add(user)
    session.commit()
    session.refresh(user)
    conn = Connection(
        user_id=user.id,
        label="School",
        base_url=BASE,
        account_type="student",
        access_token="tok",
    )
    session.add(conn)
    session.commit()
    session.refresh(conn)
    return conn


def test_sync_creates_a_course_row_per_canvas_course():
    """After sync, each Canvas course the connection sees has a Course row."""
    courses = [
        {"id": 10, "name": "Algebra I", "course_code": "ALG1", "time_zone": "UTC"},
        {"id": 20, "name": "Biology", "course_code": "BIO1", "time_zone": "UTC"},
    ]
    with Session(make_engine()) as s:
        conn = seed_connection(s)
        sync_connection(s, conn, canvas_client(courses))
        s.commit()

        rows = s.exec(
            select(Course).where(Course.connection_id == conn.id)
        ).all()

        assert len(rows) == 2
        ids = {r.canvas_course_id for r in rows}
        names = {r.name for r in rows}
        assert ids == {10, 20}
        assert names == {"Algebra I", "Biology"}


def test_resync_does_not_duplicate_courses():
    """Running sync_connection twice keeps exactly the same number of Course rows."""
    courses = [
        {"id": 10, "name": "Algebra I", "course_code": "ALG1", "time_zone": "UTC"},
        {"id": 20, "name": "Biology", "course_code": "BIO1", "time_zone": "UTC"},
    ]
    with Session(make_engine()) as s:
        conn = seed_connection(s)
        sync_connection(s, conn, canvas_client(courses))
        s.commit()
        sync_connection(s, conn, canvas_client(courses))
        s.commit()

        rows = s.exec(
            select(Course).where(Course.connection_id == conn.id)
        ).all()
        assert len(rows) == 2


def test_resync_updates_course_name():
    """If Canvas renames a course between syncs, the stored name is updated."""
    first_run = [{"id": 10, "name": "Old Name", "course_code": "ALG1", "time_zone": "UTC"}]
    second_run = [{"id": 10, "name": "New Name", "course_code": "ALG1", "time_zone": "UTC"}]

    with Session(make_engine()) as s:
        conn = seed_connection(s)
        sync_connection(s, conn, canvas_client(first_run))
        s.commit()

        sync_connection(s, conn, canvas_client(second_run))
        s.commit()

        rows = s.exec(
            select(Course).where(Course.connection_id == conn.id)
        ).all()
        assert len(rows) == 1
        assert rows[0].name == "New Name"
