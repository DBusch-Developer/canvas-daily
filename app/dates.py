from datetime import datetime, timedelta


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


def group_by_week(assignments, now):
    """Group upcoming assignments into Monday-start calendar weeks.

    Returns an ordered list of {"label": str, "assignments": list}, one entry per
    week that has assignments, chronologically. Empty weeks are skipped. Labels are
    "This week" / "Next week" for the current and following week, otherwise
    "Week of <Mon date>".
    """
    this_monday = now.date() - timedelta(days=now.weekday())

    by_monday: dict = {}
    for a in assignments:
        monday = a.due_at.date() - timedelta(days=a.due_at.weekday())
        by_monday.setdefault(monday, []).append(a)

    groups = []
    for monday in sorted(by_monday):
        weeks_out = (monday - this_monday).days // 7
        if weeks_out == 0:
            label = "This week"
        elif weeks_out == 1:
            label = "Next week"
        else:
            label = f"Week of {monday:%b} {monday.day}"
        groups.append({"label": label, "assignments": by_monday[monday]})
    return groups
