from datetime import datetime


def classify_due(due_at: datetime, now: datetime) -> str:
    """Bucket an assignment by its due date relative to a fixed "now".

    Returns one of: "past_due", "due_today", "upcoming".
    A due date strictly before now is past due. Anything still to come
    that falls on today's calendar date is due today. Everything later
    is upcoming.
    """
    if due_at < now:
        return "past_due"
    if due_at.date() == now.date():
        return "due_today"
    return "upcoming"
