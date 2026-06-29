"""Layer 36 — live, self-updating sync notice (no manual refresh).

When a course content sync runs in the background, the notice now polls
itself via HTMX and flips to "ready" the moment the sync finishes — the
user never has to refresh. While it works it shows animated dots, and the
course page carries a back link to the full course list.

Behavior under test:
  - GET /courses/{id}/sync-status?since=<baseline> returns a *still syncing*
    fragment (keeps polling) while last_content_synced_at has not advanced,
    and a *ready* fragment (no poll hook) once it has.
  - The syncing notice shows animated dots and never says "Refresh to see it".
  - The status endpoint is gated by the feature flag (404 when off).
  - The course page has a back link to /ask (all courses).

In-memory SQLite + StaticPool. No Neon, no live Canvas.
"""

from datetime import datetime, timezone

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Connection, Course, User
from app.web import create_app, get_session
from fastapi.testclient import TestClient

BASE = "https://school.test"
CANVAS_COURSE_ID = 42


def _make_sqlite_engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


def _make_app(engine):
    application = create_app()

    def _get_session():
        with Session(engine) as s:
            yield s

    application.dependency_overrides[get_session] = _get_session
    return application


def _signup(client, email, password="hunter2"):
    return client.post("/signup", data={"email": email, "password": password},
                       follow_redirects=False)


def _seed_course(engine, user_email, last_synced=None):
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == user_email)).one()
        conn = Connection(
            user_id=user.id, label="Conn", base_url=BASE,
            account_type="student", access_token="tok",
        )
        s.add(conn)
        s.commit()
        s.refresh(conn)
        course = Course(
            connection_id=conn.id,
            canvas_course_id=CANVAS_COURSE_ID,
            name="Test Course",
            last_content_synced_at=last_synced,
        )
        s.add(course)
        s.commit()
        s.refresh(course)
        return course.id


# ---------------------------------------------------------------------------
# Tests — each fails before the implementation exists
# ---------------------------------------------------------------------------

def test_sync_status_keeps_polling_while_unchanged(monkeypatch):
    """While last_content_synced_at has not moved past the baseline, the status
    endpoint returns a fragment that keeps polling (hx-get back to itself).

    Fails before impl: the /sync-status route does not exist (404).
    """
    monkeypatch.setenv("ASK_COURSE_ENABLED", "1")
    engine = _make_sqlite_engine()
    app = _make_app(engine)
    client = TestClient(app)

    _signup(client, "poll@test.com")
    t = datetime(2026, 6, 28, 12, 0, 0, tzinfo=timezone.utc)
    course_id = _seed_course(engine, "poll@test.com", last_synced=t)

    # baseline == current timestamp → sync still running
    resp = client.get(f"/courses/{course_id}/sync-status?since={t.timestamp()}")
    assert resp.status_code == 200
    assert f"/courses/{course_id}/sync-status" in resp.text, (
        "Still-syncing fragment must keep polling the status endpoint"
    )
    assert "Refresh to see it" not in resp.text, (
        "The manual-refresh instruction must be gone"
    )


def test_sync_status_reports_ready_when_timestamp_advances(monkeypatch):
    """Once last_content_synced_at moves past the baseline, the status endpoint
    returns a 'ready' fragment with NO further polling hook.

    Fails before impl: the /sync-status route does not exist (404).
    """
    monkeypatch.setenv("ASK_COURSE_ENABLED", "1")
    engine = _make_sqlite_engine()
    app = _make_app(engine)
    client = TestClient(app)

    _signup(client, "ready@test.com")
    newer = datetime(2026, 6, 28, 12, 5, 0, tzinfo=timezone.utc)
    course_id = _seed_course(engine, "ready@test.com", last_synced=newer)

    old_baseline = datetime(2026, 6, 28, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    resp = client.get(f"/courses/{course_id}/sync-status?since={old_baseline}")
    assert resp.status_code == 200
    assert "ready" in resp.text.lower(), "Done fragment must say it is ready"
    assert f"/courses/{course_id}/sync-status" not in resp.text, (
        "Done fragment must stop polling (no hx-get back to the status endpoint)"
    )


def test_sync_status_serves_with_flag_and_hides_without(monkeypatch):
    """The status endpoint serves (200) with the flag on and is hidden (404)
    with it off.

    Fails before impl: the route does not exist, so the flag-on case 404s
    instead of 200.
    """
    engine = _make_sqlite_engine()
    app = _make_app(engine)
    client = TestClient(app)

    _signup(client, "flag@test.com")
    course_id = _seed_course(engine, "flag@test.com")

    monkeypatch.setenv("ASK_COURSE_ENABLED", "1")
    on = client.get(f"/courses/{course_id}/sync-status?since=")
    assert on.status_code == 200, "Status endpoint must serve when the flag is on"

    monkeypatch.delenv("ASK_COURSE_ENABLED", raising=False)
    off = client.get(f"/courses/{course_id}/sync-status?since=")
    assert off.status_code == 404, "Status endpoint must 404 when the flag is off"


def test_syncing_notice_shows_animated_dots(monkeypatch):
    """The syncing notice on the course page shows animated dots and self-polls,
    and never tells the user to refresh.

    Fails before impl: the page still says 'Refresh to see it' with no dots.
    """
    monkeypatch.setenv("ASK_COURSE_ENABLED", "1")
    engine = _make_sqlite_engine()
    app = _make_app(engine)
    client = TestClient(app)

    _signup(client, "dots@test.com")
    course_id = _seed_course(engine, "dots@test.com")

    resp = client.get(f"/courses/{course_id}/ask?syncing=1")
    assert resp.status_code == 200
    assert "sync-dots" in resp.text, "Syncing notice must include the animated dots element"
    assert "Refresh to see it" not in resp.text, (
        "The manual-refresh instruction must be gone"
    )
    assert f"/courses/{course_id}/sync-status" in resp.text, (
        "Syncing notice must self-poll the status endpoint"
    )


def test_course_page_has_back_to_courses_link(monkeypatch):
    """The course page links back to the full course list at /ask.

    Fails before impl: the template has no back link.
    """
    monkeypatch.setenv("ASK_COURSE_ENABLED", "1")
    engine = _make_sqlite_engine()
    app = _make_app(engine)
    client = TestClient(app)

    _signup(client, "back@test.com")
    course_id = _seed_course(engine, "back@test.com")

    resp = client.get(f"/courses/{course_id}/ask")
    assert resp.status_code == 200
    assert "All courses" in resp.text, (
        "Course page must have a visible 'All courses' back link to /ask "
        "(distinct from the nav bar's 'Ask my course' link)"
    )
