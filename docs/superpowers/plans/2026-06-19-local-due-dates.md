# Local (Course-Timezone) Due Dates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show and bucket every assignment's due date in the course's own timezone (from Canvas), so `2026-06-20 06:59:59` UTC displays as `Jun 19, 2026 · 11:59 PM`.

**Architecture:** Keep storing UTC. Capture each course's `time_zone` from Canvas onto the assignment. A pure `to_local(dt, tz)` helper converts UTC→local; model properties `due_local`/`due_display` format it; reports and the weekly grouping compare local dates. Empty zone falls back to UTC so existing layers are unaffected.

**Tech Stack:** `zoneinfo` (stdlib) + `tzdata` (already installed), SQLModel, Jinja2, pytest.

## Global Constraints

- TDD-first, red before green. New behavior is its own layer `tests/test_timezone.py` (label `timezone`).
- Evidence: `docs/test-evidence/timezone-red.png` / `timezone-green.png`, both in README as **Layer 19**. Red captured live before any implementation. Both PNGs committed together at the end.
- Date format is exactly `Jun 19, 2026 · 11:59 PM` (middle dot U+00B7); no leading zero on the hour or day.
- Timezone comes from Canvas (`course.time_zone`), never user-configured.
- Storage stays UTC. Convert only at display and bucketing.
- Empty/invalid `time_zone` falls back to UTC — keeps `dates`, `completed`, and `upcomingweeks` layers green.
- No new dependency.
- Commit with the pre-commit hook. Short messages. Commit on `main`, no branches.
- Run pytest via the venv: `.venv/Scripts/python.exe -m pytest ...`.

---

## File Structure

- `tests/test_timezone.py` — **create.** Layer 19 tests: helper, properties, fetch, sync, bucketing, page render.
- `app/dates.py` — **modify.** Add `to_local`; make `group_by_week` use local weeks.
- `app/models.py` — **modify.** `time_zone` field; `due_local` / `due_display` properties.
- `app/canvas.py` — **modify.** `fetch_courses` returns `time_zone`.
- `app/sync.py` — **modify.** Store `time_zone` on upsert.
- `app/reports.py` — **modify.** Bucket in local time.
- `app/templates/detail.html`, `app/templates/report.html`, `app/mailer.py` — **modify.** Render `due_display`.
- `README.md` — **modify.** Add Layer 19.

---

## Task 1: Write the failing Layer 19 tests and capture RED

**Files:**
- Create: `tests/test_timezone.py`
- Capture: `docs/test-evidence/timezone-red.png`
- Modify: `README.md`

**Interfaces:**
- Consumes (do not exist yet): `app.dates.to_local`; `Assignment.time_zone`, `Assignment.due_local`, `Assignment.due_display`; `fetch_courses(...)[i]["time_zone"]`.
- Produces: the enforced `timezone` layer.

- [ ] **Step 1: Write `tests/test_timezone.py`**

```python
"""Layer 19 - show Canvas due dates in the course's timezone.

Canvas returns due dates in UTC and tells us each course's time_zone. We keep
storing UTC but convert to that zone for display and bucketing, so a due date
Canvas shows as 'Jun 19 by 11:59pm' (Arizona) no longer appears as the next
morning. Canvas is mocked at the transport boundary; the page/bucketing tests use
the FastAPI TestClient against in-memory SQLite.
"""

from datetime import datetime

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.canvas import fetch_courses
from app.dates import to_local
from app.models import Assignment, Connection, User
from app.reports import report_for_user
from app.sync import sync_connection
from app.web import create_app, get_session

BASE = "https://school.test"
PHX = "America/Phoenix"
# 11:59pm June 19 Arizona == 06:59:59 UTC June 20.
DUE_UTC = datetime(2026, 6, 20, 6, 59, 59)


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def in_memory_engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


# ---- to_local (pure) ----

def test_to_local_converts_utc_to_phoenix():
    local = to_local(DUE_UTC, PHX)
    assert (local.year, local.month, local.day) == (2026, 6, 19)
    assert (local.hour, local.minute) == (23, 59)
    assert local.utcoffset().total_seconds() == -7 * 3600


def test_to_local_falls_back_to_utc_for_empty_or_bad_zone():
    assert to_local(DUE_UTC, "").day == 20           # stays UTC (June 20)
    assert to_local(DUE_UTC, "Not/AZone").day == 20  # invalid -> UTC
    assert to_local(None, PHX) is None


# ---- model properties ----

def test_due_display_formats_local_time():
    a = Assignment(connection_id=1, canvas_assignment_id=1, name="x",
                   due_at=DUE_UTC, time_zone=PHX)
    assert a.due_display == "Jun 19, 2026 · 11:59 PM"


def test_due_display_no_due_date():
    a = Assignment(connection_id=1, canvas_assignment_id=2, name="x",
                   due_at=None, time_zone=PHX)
    assert a.due_display == "No due date"


# ---- fetch + sync ----

def test_fetch_courses_includes_time_zone():
    def handler(request):
        return httpx.Response(200, json=[
            {"id": 1, "name": "Bio", "course_code": "BIO 101", "time_zone": PHX}])
    courses = fetch_courses(BASE, "tok", client_for(handler))
    assert courses[0]["time_zone"] == PHX


def test_sync_stores_time_zone():
    eng = in_memory_engine()
    with Session(eng) as s:
        user = User(email="tz@x.com", password_hash="h")
        s.add(user)
        s.commit()
        s.refresh(user)
        conn = Connection(user_id=user.id, label="Mine", base_url=BASE,
                          account_type="student", access_token="tok")
        s.add(conn)
        s.commit()
        s.refresh(conn)

        def handler(request):
            path = request.url.path
            if path.endswith("/courses"):
                return httpx.Response(200, json=[
                    {"id": 10, "name": "Course 10", "course_code": "C10", "time_zone": PHX}])
            if path.endswith("/courses/10/assignments"):
                return httpx.Response(200, json=[{
                    "id": 1, "name": "Lab", "due_at": "2026-06-20T06:59:59Z",
                    "points_possible": 10, "submission_types": ["online_upload"],
                    "html_url": f"{BASE}/a/1", "description": ""}])
            return httpx.Response(200, json=[])

        sync_connection(s, conn, client_for(handler))
        stored = s.exec(select(Assignment).where(Assignment.connection_id == conn.id)).one()
        assert stored.time_zone == PHX


# ---- bucketing in local time ----

def test_due_tonight_local_buckets_as_due_today():
    eng = in_memory_engine()
    with Session(eng) as s:
        user = User(email="b@x.com", password_hash="h")
        s.add(user)
        s.commit()
        s.refresh(user)
        conn = Connection(user_id=user.id, label="Mine", base_url=BASE,
                          account_type="student", access_token="t")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        s.add(Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Tonight",
                         due_at=DUE_UTC, time_zone=PHX,
                         submission_types=["online_upload"], workflow_state="unsubmitted"))
        s.commit()
        # Mid-day June 19 Arizona == 19:00 UTC June 19.
        now = datetime(2026, 6, 19, 19, 0)
        buckets = report_for_user(s, user.id, now)

    assert [a.name for a in buckets["due_today"]] == ["Tonight"]
    assert buckets["upcoming"] == []


# ---- page render ----

@pytest.fixture
def engine():
    eng = in_memory_engine()
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture
def app(engine):
    application = create_app()

    def _get_session():
        with Session(engine) as s:
            yield s

    application.dependency_overrides[get_session] = _get_session
    return application


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


def seed(client, engine, email="pg@x.com"):
    client.post("/signup", data={"email": email, "password": "hunter2pw"},
                follow_redirects=False)
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=user.id, label="Yavapai", base_url=BASE,
                          account_type="student", access_token="t")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        a = Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Lab",
                       due_at=DUE_UTC, time_zone=PHX, points_possible=20.0,
                       submission_types=["online_upload"], html_url=f"{BASE}/a/1",
                       workflow_state="unsubmitted")
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def test_detail_page_shows_local_due(client, engine):
    aid = seed(client, engine)
    resp = client.get(f"/assignments/{aid}")
    assert resp.status_code == 200
    assert "Jun 19, 2026 · 11:59 PM" in resp.text
    assert "06:59:59" not in resp.text


def test_dashboard_card_shows_local_due(client, engine):
    seed(client, engine)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Jun 19, 2026 · 11:59 PM" in resp.text
    assert "06:59:59" not in resp.text
```

- [ ] **Step 2: Run the layer to confirm it is RED**

Run: `.venv/Scripts/python.exe tools/run_to_html.py timezone-red tests/test_timezone.py`
Expected: `[RED ...]`. Import fails on `to_local` / the `time_zone` kwarg and `due_display` don't exist; page tests fail their assertions.

- [ ] **Step 3: Screenshot the red page**

Serve: `.venv/Scripts/python.exe -m http.server 8731 --directory docs/test-evidence` (background). Navigate to `http://127.0.0.1:8731/timezone-red.html`, screenshot `.frame` to `timezone-red.png`, move into `docs/test-evidence/`. Stop the server. Verify legible.

- [ ] **Step 4: Add the Layer 19 README section**

Insert after the Layer 18 block, before "How these are made":

```markdown
**Layer 19 — show due dates in the course's timezone**

Canvas returns due dates in UTC (`2026-06-20T06:59:59Z`), so an assignment Canvas shows as *Jun 19 by 11:59pm* (Arizona) appeared as the next morning at 6:59. Each Canvas course also carries its `time_zone`, so we store it on the assignment and convert: `to_local` turns the UTC moment into the course's zone, `due_display` formats it (`Jun 19, 2026 · 11:59 PM`), and the detail page, cards, email, and the Due-today/Upcoming bucketing all use local time. Storage stays UTC; an empty zone falls back to UTC.

Red — `to_local`, the `time_zone` field, and `due_display` don't exist yet:

![Timezone tests failing](docs/test-evidence/timezone-red.png)

Green — after converting UTC to the course timezone for display and bucketing:

![Timezone tests passing](docs/test-evidence/timezone-green.png)
```

- [ ] **Step 5: Do NOT commit yet.** Both PNGs commit together at the end (Task 6).

---

## Task 2: `to_local` helper and local weeks (`app/dates.py`)

**Files:**
- Modify: `app/dates.py`
- Test: `tests/test_timezone.py` (`to_local` tests)

**Interfaces:**
- Produces: `to_local(dt, tz_name) -> datetime | None` (aware, in tz_name; UTC fallback).

- [ ] **Step 1: Update imports**

Change `app/dates.py` line 1:

```python
from datetime import datetime
```
to:
```python
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
```
(If `timedelta` was already imported by the Layer 18 change, keep it; the line should end up importing `datetime, timedelta, timezone` plus the `zoneinfo` import.)

- [ ] **Step 2: Add `to_local`**

Add (e.g. after `classify_due`):

```python
def to_local(dt, tz_name):
    """A naive-UTC datetime as an aware datetime in tz_name (UTC fallback)."""
    if dt is None:
        return None
    try:
        zone = ZoneInfo(tz_name) if tz_name else ZoneInfo("UTC")
    except Exception:
        zone = ZoneInfo("UTC")
    return dt.replace(tzinfo=timezone.utc).astimezone(zone)
```

- [ ] **Step 3: Make `group_by_week` use the local week**

Replace the body of `group_by_week` so each assignment's week is its local week, and the "This week"/"Next week" reference is computed in that group's zone:

```python
def group_by_week(assignments, now):
    """Group upcoming assignments into Monday-start calendar weeks (in each
    assignment's local timezone). Ordered, empty weeks skipped."""
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
```

- [ ] **Step 4: Run the helper tests and the existing date/week layers**

Run: `.venv/Scripts/python.exe -m pytest tests/test_timezone.py -k to_local tests/test_dates.py tests/test_upcomingweeks.py -v`
Expected: the two `to_local` tests PASS, and the existing `dates` + `upcomingweeks` tests still PASS (empty `time_zone` falls back to UTC).

---

## Task 3: `time_zone` field and display properties (`app/models.py`)

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_timezone.py` (property tests)

**Interfaces:**
- Produces: `Assignment.time_zone: str`; `Assignment.due_local`; `Assignment.due_display: str`.

- [ ] **Step 1: Import `to_local`**

Add to `app/models.py` imports (with the other `from app...` imports):

```python
from app.dates import to_local
```

- [ ] **Step 2: Add the `time_zone` column**

In `class Assignment`, after the `course_code` field, add:

```python
    time_zone: str = ""
```

- [ ] **Step 3: Add the display properties**

After the `course_trimmed` property, add:

```python
    @property
    def due_local(self):
        """due_at as an aware datetime in the course's timezone, or None."""
        return to_local(self.due_at, self.time_zone)

    @property
    def due_display(self) -> str:
        """e.g. 'Jun 19, 2026 · 11:59 PM', or 'No due date'."""
        d = self.due_local
        if d is None:
            return "No due date"
        time_part = d.strftime("%I:%M %p").lstrip("0")
        return f"{d.strftime('%b')} {d.day}, {d.year} · {time_part}"
```

- [ ] **Step 4: Run the property tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_timezone.py -k "due_display" -v`
Expected: `test_due_display_formats_local_time` and `test_due_display_no_due_date` PASS.

---

## Task 4: Capture `time_zone` from Canvas and store it

**Files:**
- Modify: `app/canvas.py`, `app/sync.py`
- Test: `tests/test_timezone.py` (fetch + sync tests)

**Interfaces:**
- Consumes: `fetch_courses` course dicts. Produces: `course["time_zone"]`; `_upsert(..., course_code="", time_zone="")` stores it.

- [ ] **Step 1: Return `time_zone` from `fetch_courses` (`app/canvas.py`)**

Change:

```python
        courses.extend(
            {"id": c.get("id"), "name": c.get("name"), "code": c.get("course_code")}
            for c in response.json()
        )
```
to:
```python
        courses.extend(
            {"id": c.get("id"), "name": c.get("name"), "code": c.get("course_code"),
             "time_zone": c.get("time_zone")}
            for c in response.json()
        )
```

- [ ] **Step 2: Store `time_zone` during sync (`app/sync.py`)**

In `sync_connection`, change the upsert call:

```python
            _upsert(session, connection.id, parsed, course.get("code") or "")
```
to:
```python
            _upsert(session, connection.id, parsed,
                    course.get("code") or "", course.get("time_zone") or "")
```

Change `_upsert` to accept and set it:

```python
def _upsert(session, connection_id, parsed, course_code="", time_zone=""):
```
and, after `target.course_code = course_code`, add:

```python
    target.time_zone = time_zone
```

- [ ] **Step 3: Run the fetch + sync tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_timezone.py -k "fetch or sync" -v`
Expected: `test_fetch_courses_includes_time_zone` and `test_sync_stores_time_zone` PASS.

---

## Task 5: Bucket and display in local time

**Files:**
- Modify: `app/reports.py`, `app/templates/detail.html`, `app/templates/report.html`, `app/mailer.py`
- Test: `tests/test_timezone.py` (bucketing + page tests)

**Interfaces:**
- Consumes: `to_local`, `Assignment.due_display`.

- [ ] **Step 1: Bucket in local time (`app/reports.py`)**

Add the import:

```python
from app.dates import classify_due, to_local
```
(replace the existing `from app.dates import classify_due`).

Change the bucketing loop:

```python
        else:
            buckets[classify_due(assignment.due_at, now)].append(assignment)
```
to:
```python
        else:
            tz = assignment.time_zone
            buckets[classify_due(to_local(assignment.due_at, tz),
                                 to_local(now, tz))].append(assignment)
```

- [ ] **Step 2: Detail page (`app/templates/detail.html`)**

Change line 41:

```html
      <span class="metacard__value">{% if a.due_at %}{{ a.due_at }}{% else %}No due date{% endif %}</span>
```
to:
```html
      <span class="metacard__value">{{ a.due_display }}</span>
```

- [ ] **Step 3: Cards (`app/templates/report.html`)**

Replace both occurrences of the due line (the `card` macro and the completed-section card):

```html
      <span class="due">due {{ a.due_at }}</span>
```
with:
```html
      <span class="due">due {{ a.due_display }}</span>
```

- [ ] **Step 4: Email (`app/mailer.py`)**

Change:

```python
            lines.append(f"  - [{label}] {assignment.name}{quiz} — due {assignment.due_at}")
```
to:
```python
            lines.append(f"  - [{label}] {assignment.name}{quiz} — due {assignment.due_display}")
```

- [ ] **Step 5: Run the full layer**

Run: `.venv/Scripts/python.exe -m pytest tests/test_timezone.py -v`
Expected: all PASS (helper, properties, fetch, sync, bucketing, page render).

---

## Task 6: Capture GREEN, verify, commit

**Files:**
- Capture: `docs/test-evidence/timezone-green.png`

- [ ] **Step 1: Render the green page**

Run: `.venv/Scripts/python.exe tools/run_to_html.py timezone-green tests/test_timezone.py`
Expected: `[GREEN (all passed)]`.

- [ ] **Step 2: Screenshot the green page**

Serve, navigate to `http://127.0.0.1:8731/timezone-green.html`, screenshot `.frame` to `timezone-green.png`, move into `docs/test-evidence/`. Stop the server. Verify legible.

- [ ] **Step 3: Run the full suite and evidence check**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass — including `dates`, `completed`, `mailer`, `upcomingweeks` (empty `time_zone` → UTC fallback keeps them identical).
Run: `.venv/Scripts/python.exe tools/check_evidence.py`
Expected: `OK - ... timezone ...`.

- [ ] **Step 4: Commit and push**

```bash
git add app/ tests/test_timezone.py docs/test-evidence/timezone-red.png docs/test-evidence/timezone-green.png docs/test-evidence/timezone-red.html docs/test-evidence/timezone-green.html README.md
git commit -m "Show and bucket due dates in the course's timezone (Layer 19)"
git push origin main
```
Expected: `[pre-commit] ok`, full suite passes, push succeeds.

---

## Task 7: Migrate the live database and re-sync (operational)

**Files:** none (operational).

- [ ] **Step 1: Add the `time_zone` column (idempotent)**

```bash
.venv/Scripts/python.exe -c "
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import inspect, text
from app.db import make_engine
eng = make_engine()
cols = [c['name'] for c in inspect(eng).get_columns('assignments')]
if 'time_zone' not in cols:
    with eng.begin() as conn:
        conn.execute(text(\"ALTER TABLE assignments ADD COLUMN time_zone VARCHAR NOT NULL DEFAULT ''\"))
    print('added time_zone')
else:
    print('time_zone already present')
"
```
Expected: `added time_zone`.

- [ ] **Step 2: Re-sync to backfill the zone**

Run: `.venv/Scripts/python.exe -m app.jobs sync`
Then verify dates now read local:
```bash
.venv/Scripts/python.exe -c "
from dotenv import load_dotenv; load_dotenv()
from sqlmodel import Session, select
from app.db import make_engine
from app.models import Assignment
with Session(make_engine()) as s:
    for a in s.exec(select(Assignment)).all()[:5]:
        print(a.due_display, '|', a.time_zone, '|', a.name[:30])
"
```
Expected: due dates print as `Jun 19, 2026 · 11:59 PM` with `time_zone = America/Phoenix`.

---

## Self-Review

**Spec coverage:**
- Capture/store `time_zone` → Tasks 3-4; `test_fetch_courses_includes_time_zone`, `test_sync_stores_time_zone`.
- `to_local` → Task 2; `test_to_local_*`.
- `due_local`/`due_display` → Task 3; `test_due_display_*`.
- Display on detail/cards/email → Task 5 Steps 2-4.
- Local bucketing → Task 5 Step 1; `test_due_tonight_local_buckets_as_due_today`.
- Local weeks → Task 2 Step 3.
- Migration + backfill → Task 7.
- Evidence (red live, green) → Task 1 (red) + Task 6 (green); both committed in Task 6.

**Placeholder scan:** No TBD/TODO; full code in every step; commands have expected output.

**Type consistency:** `to_local(dt, tz_name) -> datetime|None` used identically in `models.due_local`, `reports`, and `group_by_week`. `time_zone` is `str` everywhere. `due_display -> str` read in three templates and the mailer. `_upsert(..., course_code="", time_zone="")` matches its single call site in `sync_connection`.

**Coupling to verify during execution:** `models.py` importing `app.dates` introduces no cycle (`dates` imports only stdlib). Empty `time_zone` makes `to_local` return the UTC instant, so `dates`/`completed`/`upcomingweeks`/`mailer` tests (which seed no zone) render and bucket exactly as before. The `·` in `due_display` is U+00B7; the test file must be UTF-8. The format strips a leading zero from the hour (`01:05 PM`→`1:05 PM`) and uses `d.day` (no leading zero on the day).
