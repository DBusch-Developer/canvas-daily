"""The daily pre-fetch: for every connection, pull each course's assignments
from Canvas and store full detail, so detail pages later read from storage with
no live call. Re-runs upsert by (connection, Canvas assignment id) — never
duplicate. The caller owns the transaction (commits once the run succeeds).
"""

from datetime import datetime, timezone

from sqlmodel import select

from app.canvas import fetch_assignments, fetch_courses
from app.models import Assignment, Connection

_FIELDS = (
    "name", "description", "due_at", "points_possible", "submission_types",
    "html_url", "workflow_state", "score", "submitted_at", "late", "missing", "excused",
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sync_connection(session, connection, client):
    """Fetch and store every assignment across one connection's courses."""
    for course in fetch_courses(connection.base_url, connection.access_token, client):
        parsed_list = fetch_assignments(
            connection.base_url, connection.access_token, course["id"], client
        )
        for parsed in parsed_list:
            _upsert(session, connection.id, parsed,
                    course.get("code") or "", course.get("time_zone") or "")
    connection.last_synced_at = _now()
    session.add(connection)
    session.flush()


def run_daily_sync(session, client):
    """Sync every connection. One path for one connection and for four."""
    for connection in session.exec(select(Connection)).all():
        sync_connection(session, connection, client)
    session.flush()


def _upsert(session, connection_id, parsed, course_code="", time_zone=""):
    existing = session.exec(
        select(Assignment).where(
            Assignment.connection_id == connection_id,
            Assignment.canvas_assignment_id == parsed["canvas_assignment_id"],
        )
    ).first()
    target = existing or Assignment(
        connection_id=connection_id,
        canvas_assignment_id=parsed["canvas_assignment_id"],
    )
    for field in _FIELDS:
        setattr(target, field, parsed[field])
    target.course_code = course_code
    target.time_zone = time_zone
    target.fetched_at = _now()
    session.add(target)
