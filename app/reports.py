"""Build a user's report: every assignment across all their connections.

Completed work (submitted, graded, or excused) goes to its own `completed`
bucket regardless of due date. Everything else is grouped Past due / Due today /
Upcoming via the Layer 1 date classifier — one classifier, not a second copy.
"""

from sqlmodel import select

from app.dates import classify_due, to_local
from app.models import Assignment, Connection


def _is_completed(assignment):
    """Done = turned in, graded, or excused. 'Missing' is not completed."""
    return (
        assignment.submitted_at is not None
        or assignment.workflow_state == "graded"
        or assignment.excused
    )


def excuse_assignment(session, assignment_id):
    """Mark one assignment excused so it leaves Past due for Completed."""
    assignment = session.get(Assignment, assignment_id)
    assignment.excused = True
    session.add(assignment)
    session.commit()
    return assignment


def report_for_user(session, user_id, now):
    statement = (
        select(Assignment)
        .join(Connection, Assignment.connection_id == Connection.id)
        .where(Connection.user_id == user_id)
        .where(Assignment.due_at.is_not(None))
        .order_by(Assignment.due_at)
    )

    buckets = {"past_due": [], "due_today": [], "upcoming": [], "completed": []}
    for assignment in session.exec(statement).all():
        if _is_completed(assignment):
            buckets["completed"].append(assignment)
        else:
            tz = assignment.time_zone
            buckets[classify_due(to_local(assignment.due_at, tz),
                                 to_local(now, tz))].append(assignment)
    return buckets
