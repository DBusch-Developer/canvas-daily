"""Build a user's report: every assignment across all their connections,
grouped Past due / Due today / Upcoming and sorted by due date within each.
Reuses the Layer 1 bucketing helper — one classifier, not a second copy.
"""

from sqlmodel import select

from app.dates import classify_due
from app.models import Assignment, Connection


def report_for_user(session, user_id, now):
    statement = (
        select(Assignment)
        .join(Connection, Assignment.connection_id == Connection.id)
        .where(Connection.user_id == user_id)
        .where(Assignment.due_at.is_not(None))
        .order_by(Assignment.due_at)
    )

    buckets = {"past_due": [], "due_today": [], "upcoming": []}
    for assignment in session.exec(statement).all():
        buckets[classify_due(assignment.due_at, now)].append(assignment)
    return buckets
