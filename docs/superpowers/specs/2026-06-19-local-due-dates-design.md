# Show Canvas due dates in the course's timezone — design (Layer 19)

Date: 2026-06-19

## Problem

Canvas returns assignment due dates as **UTC** timestamps (e.g.
`2026-06-20T06:59:59Z`). The app parses them to naive UTC and displays/buckets
them as-is, so a due date that Canvas shows you as **Jun 19, 11:59 PM** (Arizona)
appears in Canvas Daily as **2026-06-20 06:59:59** — the next day at 6:59. It also
mis-buckets: an assignment due tonight (local) can land in **Upcoming** instead of
**Due today**, because the date comparison happens in UTC.

## Root cause (verified)

- Stored due dates are UTC (`...06:59:59`), 7 hours ahead of Arizona (UTC−7).
- Canvas's own course objects include the zone: `time_zone: "America/Phoenix"`
  (confirmed on every course in this account). `zoneinfo` + the installed
  `tzdata` package resolve it correctly:
  `2026-06-20 06:59:59Z → 2026-06-19 23:59:59-07:00`.

So the timezone does not need to be configured by the user — it comes straight
from Canvas.

## Goal

Read each course's timezone from Canvas, store it on the assignment, and use it to
**display** and **bucket** due dates in local time. Keep storing the raw UTC
moment; convert only at the edges.

## Non-goals

- No user-facing timezone setting — the zone comes from Canvas.
- No change to how due dates are stored (still UTC).
- No new dependency (`zoneinfo` is stdlib; `tzdata` is already installed).

## Data — capture and store the zone

- `app/canvas.py` `fetch_courses` already returns `{id, name, code}`; add
  `"time_zone": c.get("time_zone")`.
- `app/models.py` `Assignment` gains `time_zone: str = ""`.
- `app/sync.py` threads the zone into `_upsert` alongside the course code
  (`_upsert(session, conn_id, parsed, course_code, time_zone)`).

## Conversion — one helper

`app/dates.py`:

```python
from zoneinfo import ZoneInfo

def to_local(dt, tz_name):
    """A naive-UTC datetime as an aware datetime in tz_name (falls back to UTC)."""
    if dt is None:
        return None
    try:
        zone = ZoneInfo(tz_name) if tz_name else ZoneInfo("UTC")
    except Exception:
        zone = ZoneInfo("UTC")
    return dt.replace(tzinfo=timezone.utc).astimezone(zone)
```

(`from datetime import timezone` is added alongside the existing import.)

## Display — model properties (`app/models.py`)

```python
@property
def due_local(self):
    """due_at as an aware datetime in the course's timezone (None if no due date)."""
    return to_local(self.due_at, self.time_zone)

@property
def due_display(self):
    """e.g. 'Jun 19, 2026 · 11:59 PM', or 'No due date'."""
    d = self.due_local
    if d is None:
        return "No due date"
    time_part = d.strftime("%I:%M %p").lstrip("0")
    return f"{d.strftime('%b')} {d.day}, {d.year} · {time_part}"
```

Templates render `due_display` instead of the raw `due_at`:

- `app/templates/detail.html` — the Due date metacard.
- `app/templates/report.html` — the card foot (`due {{ a.due_display }}`), in the
  shared `card` macro and the completed-section card.
- `app/mailer.py` — the email line uses `assignment.due_display`.

## Bucketing — compare in local time

`app/reports.py` converts both the due date and `now` into the assignment's zone
before classifying (so "Due today" is the local day). `classify_due` is unchanged
— it just receives aware local datetimes:

```python
tz = assignment.time_zone
buckets[classify_due(to_local(assignment.due_at, tz), to_local(now, tz))].append(assignment)
```

`app/dates.py` `group_by_week` also groups by the **local** week, so the weekly
sections in the Upcoming column match the displayed dates:

```python
monday_of = to_local(a.due_at, getattr(a, "time_zone", "")).date()
monday = monday_of - timedelta(days=monday_of.weekday())
```

For assignments with no stored zone (`time_zone == ""`), `to_local` falls back to
UTC — identical to today's behavior, so existing `dates`, `completed`, and
`upcomingweeks` layers stay green.

## Migration and backfill

- Add the column once (idempotent, dialect-agnostic), same approach as
  `course_code`:
  ```sql
  ALTER TABLE assignments ADD COLUMN time_zone VARCHAR NOT NULL DEFAULT '';
  ```
- `time_zone` is empty on existing rows until the next sync re-populates them; a
  re-sync fills it in. The plan includes the migration step and a re-sync.

## Components touched

- `app/canvas.py` — `fetch_courses` returns `time_zone`.
- `app/models.py` — `time_zone` field; `due_local` / `due_display` properties.
- `app/dates.py` — `to_local`; `group_by_week` uses local week.
- `app/sync.py` — store `time_zone` on upsert.
- `app/reports.py` — bucket in local time.
- `app/templates/detail.html`, `app/templates/report.html` — render `due_display`.
- `app/mailer.py` — email uses `due_display`.
- `tests/test_timezone.py` — new enforced layer (label `timezone`).
- `README.md` — new "Layer 19" test-evidence section.
- `docs/test-evidence/timezone-red.png`, `timezone-green.png`.

## Test plan — TDD, Layer 19

New file `tests/test_timezone.py` (label `timezone`). One red + one green for the
layer; red captured live before any implementation. Pure tests for the helper and
properties; Canvas mocked for fetch/sync; FastAPI TestClient + in-memory SQLite
for the page render and bucketing.

Tests:

- **Fetch:** `fetch_courses` includes `time_zone` from the course's `time_zone`.
- **Sync:** after `sync_connection` (Canvas mocked), the stored assignment has the
  course's `time_zone`.
- **`to_local`:** `2026-06-20 06:59:59` UTC with `America/Phoenix` →
  `2026-06-19 23:59:59-07:00`; empty/invalid zone → UTC.
- **`due_display`:** that assignment renders `"Jun 19, 2026 · 11:59 PM"`; no due
  date → `"No due date"`.
- **Bucketing:** an assignment due `2026-06-20 06:59:59` UTC with
  `America/Phoenix`, evaluated at a `now` that is mid-day June 19 Arizona, lands in
  **due_today** (not upcoming).
- **Display on page:** the detail page and a dashboard card show the formatted
  local time, not the raw UTC string.

TDD order (honoring CLAUDE.md test-evidence rules):

1. Write the failing tests first (the field, helper, properties, and display
   don't exist yet).
2. Capture **red live, before any code**: `timezone-red.png`.
3. Add the Layer 19 section to the README with the red screenshot.
4. Implement until green.
5. Capture **green**: `timezone-green.png`, add to README.
6. Verify images by eye, run `check_evidence`, commit with the pre-commit hook,
   push. Then run the column migration and a re-sync.
