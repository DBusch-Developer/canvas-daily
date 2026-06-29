"""Layer 39 — Ask My Course picker: account selector + real-class filtering.

In-memory SQLite + StaticPool, Groq mocked at the transport boundary. The picker
classifies undecided courses on load (show-all on failure), groups by a selected
account, and offers a Hidden courses disclosure with a Show action.
"""
import json
import os

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Connection, Course, User
from app.web import create_app, get_session, get_engine, get_groq_client
from app.ai import get_api_key  # same object the picker route depends on
from fastapi.testclient import TestClient

# ASK_COURSE must be enabled for these routes.
os.environ["ASK_COURSE_ENABLED"] = "1"


def _engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


def _classify_handler(bools):
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps({"result": bools})}}]
        })
    return handler


def _app(engine, classify=None):
    application = create_app()

    def _get_session():
        with Session(engine) as s:
            yield s
    application.dependency_overrides[get_session] = _get_session
    application.dependency_overrides[get_engine] = lambda: engine

    def _groq():
        handler = classify or _classify_handler([])
        return httpx.Client(transport=httpx.MockTransport(handler))
    application.dependency_overrides[get_groq_client] = _groq
    application.dependency_overrides[get_api_key] = lambda: "k"
    return application


def _signup(client, email="a@test.com"):
    return client.post("/signup", data={"email": email, "password": "hunter2pw"},
                       follow_redirects=False)


def _seed(engine, email):
    with Session(engine) as s:
        u = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=u.id, label="Mine", base_url="https://school.test",
                          account_type="student", access_token="tok")
        s.add(conn); s.commit(); s.refresh(conn)
        return conn.id


def _add_course(engine, conn_id, cid, name, hidden=None, has=True):
    with Session(engine) as s:
        s.add(Course(connection_id=conn_id, canvas_course_id=cid, name=name,
                     hidden=hidden, has_assignments=has))
        s.commit()


def test_picker_classifies_undecided_on_load_and_splits_lists():
    eng = _engine()
    client = TestClient(_app(eng, classify=_classify_handler([True, False])))
    _signup(client)
    conn_id = _seed(eng, "a@test.com")
    _add_course(eng, conn_id, 1, "CSA250 Intro AI (22255)")     # -> real
    _add_course(eng, conn_id, 2, "Lunch Brunch")                # -> hidden
    resp = client.get(f"/ask?account={conn_id}")
    assert resp.status_code == 200
    body = resp.text
    assert "CSA250 Intro AI (22255)" in body
    # Lunch Brunch must be under the hidden disclosure, not the main list.
    head, _, tail = body.partition("Hidden courses")
    assert "Lunch Brunch" not in head
    assert "Lunch Brunch" in tail
    # And the classification persisted.
    with Session(eng) as s:
        by = {c.canvas_course_id: c for c in s.exec(select(Course)).all()}
        assert by[1].hidden is False and by[2].hidden is True


def test_picker_shows_account_dropdown_with_each_connection():
    eng = _engine()
    client = TestClient(_app(eng))
    _signup(client)
    a = _seed(eng, "a@test.com")
    with Session(eng) as s:
        u = s.exec(select(User).where(User.email == "a@test.com")).one()
        c2 = Connection(user_id=u.id, label="Marley", base_url="https://k12.test",
                        account_type="observer", access_token="tok")
        s.add(c2); s.commit()
    _add_course(eng, a, 1, "CSA250 (22255)", hidden=False)
    resp = client.get("/ask")
    assert "Mine" in resp.text and "Marley" in resp.text  # both accounts in dropdown


def test_picker_only_shows_selected_account_courses():
    eng = _engine()
    client = TestClient(_app(eng))
    _signup(client)
    a = _seed(eng, "a@test.com")
    with Session(eng) as s:
        u = s.exec(select(User).where(User.email == "a@test.com")).one()
        c2 = Connection(user_id=u.id, label="Other", base_url="https://k12.test",
                        account_type="observer", access_token="tok")
        s.add(c2); s.commit(); s.refresh(c2); b = c2.id
    _add_course(eng, a, 1, "CSA250 (22255)", hidden=False)
    _add_course(eng, b, 2, "BIO181 (22133)", hidden=False)
    resp = client.get(f"/ask?account={a}")
    assert "CSA250 (22255)" in resp.text
    assert "BIO181 (22133)" not in resp.text


def test_classify_failure_shows_all():
    def boom(request):
        raise httpx.ReadTimeout("slow", request=request)
    eng = _engine()
    client = TestClient(_app(eng, classify=boom))
    _signup(client)
    conn_id = _seed(eng, "a@test.com")
    _add_course(eng, conn_id, 1, "Lunch Brunch", hidden=None)
    resp = client.get(f"/ask?account={conn_id}")
    assert resp.status_code == 200
    assert "Lunch Brunch" in resp.text            # shown, not hidden
    with Session(eng) as s:
        assert s.exec(select(Course)).one().hidden is None  # stays undecided


def test_show_unhides_and_is_ownership_guarded():
    eng = _engine()
    client = TestClient(_app(eng))
    _signup(client, "owner@test.com")
    conn_id = _seed(eng, "owner@test.com")
    _add_course(eng, conn_id, 1, "Lunch Brunch", hidden=True)
    with Session(eng) as s:
        course_id = s.exec(select(Course)).one().id

    # Owner can show it.
    resp = client.post(f"/courses/{course_id}/show", follow_redirects=False)
    assert resp.status_code == 303
    with Session(eng) as s:
        assert s.get(Course, course_id).hidden is False

    # A different user cannot.
    client.cookies.clear()
    _signup(client, "intruder@test.com")
    resp = client.post(f"/courses/{course_id}/show", follow_redirects=False)
    assert resp.status_code == 404
