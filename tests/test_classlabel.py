"""Layer 17 - class label: short code on the detail page, trimmed on the card.

`course_short` is the leading token of course_code (e.g. 'CSA250'); the detail
page header pill shows it instead of the connection label. `course_trimmed` is
course_code without a trailing '(...)' section number; the dashboard card shows
it instead of the verbose full string. Both are pure properties; the page tests
use the FastAPI TestClient against in-memory SQLite.
"""

from datetime import datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Assignment, Connection, User
from app.web import create_app, get_session

VERBOSE = "CSA250 Intro Artificial Intelligence (22255)"


# ---- pure properties ----

def test_course_short_is_leading_token():
    a = Assignment(connection_id=1, canvas_assignment_id=1, name="x", course_code=VERBOSE)
    assert a.course_short == "CSA250"


def test_course_short_empty_when_no_code():
    a = Assignment(connection_id=1, canvas_assignment_id=2, name="x", course_code="")
    assert a.course_short == ""


def test_course_trimmed_strips_trailing_parenthetical():
    a = Assignment(connection_id=1, canvas_assignment_id=1, name="x", course_code=VERBOSE)
    assert a.course_trimmed == "CSA250 Intro Artificial Intelligence"


def test_course_trimmed_leaves_plain_value_and_empty():
    plain = Assignment(connection_id=1, canvas_assignment_id=2, name="x", course_code="BIO 101")
    empty = Assignment(connection_id=1, canvas_assignment_id=3, name="x", course_code="")
    assert plain.course_trimmed == "BIO 101"
    assert empty.course_trimmed == ""


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


def seed(client, engine, *, course_code, label="Diana", email="cl@x.com"):
    client.post("/signup", data={"email": email, "password": "hunter2pw"},
                follow_redirects=False)
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=user.id, label=label, base_url="https://school.test",
                          account_type="student", access_token="tok")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        a = Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Lab",
                       due_at=datetime(2026, 6, 25, 12, 0), points_possible=20.0,
                       submission_types=["online_upload"], html_url="https://school.test/a/1",
                       workflow_state="unsubmitted", course_code=course_code)
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


# ---- detail page header pill ----

def test_detail_header_pill_shows_short_code(client, engine):
    aid = seed(client, engine, course_code=VERBOSE, label="Diana")
    resp = client.get(f"/assignments/{aid}")
    assert resp.status_code == 200
    # The header pill renders the short code, not the connection label.
    assert 'course-pill course-pill--lg">CSA250<' in resp.text


def test_detail_header_pill_falls_back_to_connection(client, engine):
    aid = seed(client, engine, course_code="", label="Solo")
    resp = client.get(f"/assignments/{aid}")
    assert resp.status_code == 200
    assert 'course-pill course-pill--lg">Solo<' in resp.text


# ---- dashboard card ----

def test_card_shows_trimmed_class(client, engine):
    seed(client, engine, course_code=VERBOSE)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "CSA250 Intro Artificial Intelligence" in resp.text
    assert "(22255)" not in resp.text
