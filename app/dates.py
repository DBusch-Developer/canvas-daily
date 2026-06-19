from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def to_local(dt, tz_name):
    """A naive-UTC datetime as an aware datetime in tz_name (UTC fallback)."""
    if dt is None:
        return None
    try:
        zone = ZoneInfo(tz_name) if tz_name else ZoneInfo("UTC")
    except Exception:
        zone = ZoneInfo("UTC")
    return dt.replace(tzinfo=timezone.utc).astimezone(zone)


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
    """Group upcoming assignments into Monday-start calendar weeks (in each
    assignment's local timezone).

    Returns an ordered list of {"label": str, "assignments": list}, one entry per
    week that has assignments, chronologically. Empty weeks are skipped. Labels are
    "This week" / "Next week" for the current and following week, otherwise
    "Week of <Mon date>".
    """
    by_monday: dict = {}
    tz_for: dict = {}
    for a in assignments:
        tz = getattr(a, "time_zone", "")
        local = to_local(a.due_at, tz).date()
        monday = local - timedelta(days=local.weekday())
        by_monday.setdefault(monday, []).append(a)
        tz_for.setdefault(monday, tz)

    groups = []
    for monday in sorted(by_monday):
        now_local = to_local(now, tz_for[monday]).date()
        this_monday = now_local - timedelta(days=now_local.weekday())
        weeks_out = (monday - this_monday).days // 7
        if weeks_out == 0:
            label = "This week"
        elif weeks_out == 1:
            label = "Next week"
        else:
            label = f"Week of {monday:%b} {monday.day}"
        groups.append({"label": label, "assignments": by_monday[monday]})
    return groups
