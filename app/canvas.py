"""Fetch and parse assignment detail for one Canvas connection.

Canvas is mocked in tests at the httpx transport boundary, so the client is
injected rather than created here. One call per page, following the `Link`
header until there is no `next` — never just page one.
"""

from datetime import datetime, timezone

import httpx
import nh3


def fetch_assignments(base_url, token, course_id, client):
    """Return parsed assignments for a course, across all pages.

    `include[]=submission` folds the token-user's own submission state into the
    same response, so each connection reports the right person without a second
    loop. The raw HTML description is sanitized before it leaves this function.
    """
    url = f"{base_url}/api/v1/courses/{course_id}/assignments"
    params = {"include[]": "submission", "per_page": 100}
    headers = {"Authorization": f"Bearer {token}"}

    assignments = []
    while url:
        response = client.get(url, params=params, headers=headers)
        response.raise_for_status()
        assignments.extend(_parse(raw) for raw in response.json())
        url = _next_page(response)
        params = None  # the next URL already carries its own query string

    return assignments


def verify_token(base_url, token, client):
    """Probe Canvas with the token. Returns "ok" | "invalid" | "unreachable".

    "ok"          -> Canvas accepted the token (200)
    "invalid"     -> Canvas rejected the token (401/403)
    "unreachable" -> any other status, timeout, or network error

    `/users/self` is the lightest authenticated endpoint (one record, no
    pagination), so this checks the token without pulling any course data. The
    token is never logged.
    """
    url = f"{base_url.rstrip('/')}/api/v1/users/self"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = client.get(url, headers=headers)
    except httpx.HTTPError:
        return "unreachable"
    if response.status_code == 200:
        return "ok"
    if response.status_code in (401, 403):
        return "invalid"
    return "unreachable"


def fetch_courses(base_url, token, client):
    """Return the token-user's active courses (id + name), across all pages.

    The daily job walks these to fetch assignments per course — there is no
    single Canvas endpoint for every assignment with submission state.
    """
    url = f"{base_url}/api/v1/courses"
    params = {"enrollment_state": "active", "per_page": 100}
    headers = {"Authorization": f"Bearer {token}"}

    courses = []
    while url:
        response = client.get(url, params=params, headers=headers)
        response.raise_for_status()
        courses.extend(
            {"id": c.get("id"), "name": c.get("name"), "code": c.get("course_code"),
             "time_zone": c.get("time_zone")}
            for c in response.json()
        )
        url = _next_page(response)
        params = None

    return courses


def _parse(raw):
    submission = raw.get("submission") or {}
    return {
        "canvas_assignment_id": raw.get("id"),
        "name": raw.get("name"),
        "description": nh3.clean(raw.get("description") or ""),
        "due_at": _parse_dt(raw.get("due_at")),
        "points_possible": raw.get("points_possible"),
        "submission_types": raw.get("submission_types", []),
        "html_url": raw.get("html_url"),
        "workflow_state": submission.get("workflow_state"),
        "score": submission.get("score"),  # null until graded — kept as None
        "submitted_at": _parse_dt(submission.get("submitted_at")),
        # No submission — or an explicit null flag — means not late / missing /
        # excused. `or False` coerces both a missing key and a present null to
        # False, so these NOT NULL columns never receive None.
        "late": submission.get("late") or False,
        "missing": submission.get("missing") or False,
        "excused": submission.get("excused") or False,
    }


def _parse_dt(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    # Canvas "...Z" timestamps parse as tz-aware; normalize to naive UTC so they
    # line up with the date bucketing and the stored (naive) timestamp columns.
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _next_page(response):
    """The URL of the `rel="next"` link, or None when on the last page."""
    link = response.headers.get("Link")
    if not link:
        return None
    for part in link.split(","):
        segments = [s.strip() for s in part.split(";")]
        if len(segments) >= 2 and segments[1] == 'rel="next"':
            return segments[0].strip("<>")
    return None
