"""Layer 24 — email the user when a connection's token breaks.

Daily sync survives a single connection failing, marks each ok/error, and
returns the connections that newly broke on a Canvas token rejection (401/403).
build_token_error_email renders the branded alert. In-memory SQLite; no Neon.
"""

import httpx
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.mailer import build_token_error_email
from app.models import Assignment, Connection, User
from app.sync import run_daily_sync

GOOD = "https://good.test"
BAD = "https://bad.test"
ERR = "https://err.test"


def make_session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return Session(eng)


def a_connection(s, user, *, label, base_url, status="pending", token="tok"):
    conn = Connection(user_id=user.id, label=label, base_url=base_url,
                      account_type="student", access_token=token, sync_status=status)
    s.add(conn); s.commit(); s.refresh(conn)
    return conn


def a_user(s, email="p@x.com"):
    user = User(email=email, password_hash="h")
    s.add(user); s.commit(); s.refresh(user)
    return user


def canvas(request):
    """200 with one course + assignment for GOOD; 401 for BAD; 500 for ERR."""
    host = request.url.host
    if host == "bad.test":
        return httpx.Response(401, json={"errors": [{"message": "Invalid access token."}]})
    if host == "err.test":
        return httpx.Response(500, json={"errors": ["boom"]})
    path = request.url.path
    if path.endswith("/courses"):
        return httpx.Response(200, json=[{"id": 10, "name": "Bio"}])
    if path.endswith("/courses/10/assignments"):
        return httpx.Response(200, json=[{
            "id": 1, "name": "Lab", "due_at": "2026-06-20T23:59:00Z",
            "points_possible": 25, "submission_types": ["online_upload"],
            "html_url": f"{GOOD}/a/1", "description": "<p>Do it.</p>"}])
    return httpx.Response(200, json=[])


def client():
    return httpx.Client(transport=httpx.MockTransport(canvas))


def test_daily_sync_continues_past_failure_marking_each():
    s = make_session()
    user = a_user(s)
    good = a_connection(s, user, label="Good", base_url=GOOD)
    bad = a_connection(s, user, label="Bad", base_url=BAD)

    run_daily_sync(s, client())  # must not raise

    assert s.get(Connection, good.id).sync_status == "ok"
    assert s.get(Connection, bad.id).sync_status == "error"
    stored = s.exec(select(Assignment).where(Assignment.connection_id == good.id)).all()
    assert len(stored) == 1


def test_returns_newly_broken_token_rejection():
    s = make_session()
    user = a_user(s)
    bad = a_connection(s, user, label="Bad", base_url=BAD)

    broken = run_daily_sync(s, client())

    assert [c.id for c in broken] == [bad.id]


def test_excludes_already_error_connection():
    s = make_session()
    user = a_user(s)
    a_connection(s, user, label="Bad", base_url=BAD, status="error")

    broken = run_daily_sync(s, client())

    assert broken == []


def test_non_token_failure_marked_error_not_returned():
    s = make_session()
    user = a_user(s)
    err = a_connection(s, user, label="Err", base_url=ERR)

    broken = run_daily_sync(s, client())

    assert s.get(Connection, err.id).sync_status == "error"
    assert broken == []


def test_build_token_error_email_branded_no_token():
    s = make_session()
    user = a_user(s)
    conn = a_connection(s, user, label="Yavapai College", base_url=GOOD,
                        token="super-secret-token-value")

    subject, text_body, html = build_token_error_email(conn, "https://cd.test")

    assert "Yavapai College" in subject
    assert "Yavapai College" in html
    assert "New Access Token" in html
    assert "https://cd.test/static/logo.png" in html
    assert "https://cd.test/connections" in html
    for blob in (subject, text_body, html):
        assert "super-secret-token-value" not in blob
