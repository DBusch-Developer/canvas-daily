# Course Code On Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show each assignment's class (Canvas course code, e.g. "BIO 101") on the dashboard card, alongside the existing connection-label pill.

**Architecture:** `fetch_courses` already returns each course; add its `course_code`. The sync job threads that code into the assignment it stores via a new `Assignment.course_code` column. The card renders it as a small class line when present.

**Tech Stack:** SQLModel model + column, httpx Canvas fetch (mocked at transport boundary), `app/sync.py` upsert, Jinja2 `report.html`, pytest with FastAPI TestClient + in-memory SQLite.

## Global Constraints

- TDD-first, red before green. New behavior is its own layer `tests/test_coursecode.py` (label `coursecode`).
- Test evidence: `docs/test-evidence/coursecode-red.png` and `coursecode-green.png`, both referenced in README as **Layer 15**. Red captured live before any implementation. Both PNGs committed together in the final green commit.
- Layer 15 tests run on **in-memory SQLite** (no `TEST_DATABASE_URL` gate), so they execute everywhere — unlike the existing `sync` layer which is gated behind a Neon branch.
- No new Canvas call; the code comes from the existing `fetch_courses`.
- One code path for one connection and for many.
- Commit with the pre-commit hook (evidence check + full suite). Short commit messages. Commit on `main`, no branches.
- Run pytest via the venv: `.venv/Scripts/python.exe -m pytest ...`.
- **Migration:** the live DB needs the new column added once (Task 5) — `create_all` does not alter existing tables.

---

## File Structure

- `tests/test_coursecode.py` — **create.** Layer 15 tests: fetch captures code, sync stores it, card renders/omits it.
- `app/models.py` — **modify.** Add `course_code` to `Assignment`.
- `app/canvas.py` — **modify.** `fetch_courses` returns the course code.
- `app/sync.py` — **modify.** Thread the code into `_upsert`.
- `app/templates/report.html` — **modify.** Class line on active + completed cards.
- `app/static/app.css` — **modify.** `.card__class` style.
- `README.md` — **modify.** Add Layer 15 to the Test evidence list.

---

## Task 1: Write the failing Layer 15 tests and capture RED

**Files:**
- Create: `tests/test_coursecode.py`
- Capture: `docs/test-evidence/coursecode-red.png`
- Modify: `README.md` (add Layer 15 section with the red image)

**Interfaces:**
- Consumes (does not exist yet): `fetch_courses(...)[i]["code"]`; `Assignment.course_code`; the `card__class` markup.
- Produces: the enforced `coursecode` layer.

- [ ] **Step 1: Write `tests/test_coursecode.py`**

```python
"""Layer 15 - class (course code) on the dashboard card.

fetch_courses captures each course's code, sync stores it on the assignment, and
the dashboard card shows it as a small class line. Canvas is mocked at the httpx
transport boundary; the web test uses the FastAPI TestClient against in-memory
SQLite, so this layer runs without a Neon branch.
"""

from datetime import datetime

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.canvas import fetch_courses
from app.models import Assignment, Connection, User
from app.sync import sync_connection
from app.web import create_app, get_session

BASE = "https://school.test"


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def in_memory_engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


def assignment_json(canvas_id, name):
    return {"id": canvas_id, "name": name, "due_at": "2026-06-20T23:59:00Z",
            "points_possible": 100, "submission_types": ["online_text_entry"],
            "html_url": f"{BASE}/a/{canvas_id}", "description": "<p>Do it.</p>"}


def canvas_handler(courses):
    """courses: list of (course_id, course_code, [assignment_json, ...])."""
    def handler(request):
        path = request.url.path
        if path.endswith("/courses"):
            return httpx.Response(200, json=[
                {"id": cid, "name": f"Course {cid}", "course_code": code}
                for cid, code, _ in courses
            ])
        for cid, code, assignments in courses:
            if path.endswith(f"/courses/{cid}/assignments"):
                return httpx.Response(200, json=assignments)
        return httpx.Response(200, json=[])
    return handler


# ---- fetch_courses captures the code ----

def test_fetch_courses_includes_course_code():
    def handler(request):
        return httpx.Response(200, json=[
            {"id": 1, "name": "Biology", "course_code": "BIO 101"},
            {"id": 2, "name": "Untitled"},  # no course_code
        ])
    courses = fetch_courses(BASE, "tok", client_for(handler))
    assert courses[0]["code"] == "BIO 101"
    assert not courses[1]["code"]  # missing course_code -> falsy


# ---- sync stores the code on the assignment ----

def test_sync_stores_course_code():
    eng = in_memory_engine()
    with Session(eng) as s:
        user = User(email="cc@x.com", password_hash="h")
        s.add(user)
        s.commit()
        s.refresh(user)
        conn = Connection(user_id=user.id, label="Mine", base_url=BASE,
                          account_type="student", access_token="tok")
        s.add(conn)
        s.commit()
        s.refresh(conn)

        sync_connection(s, conn, client_for(canvas_handler(
            [(10, "BIO 101", [assignment_json(1, "Lab")])])))

        stored = s.exec(
            select(Assignment).where(Assignment.connection_id == conn.id)).one()
        assert stored.course_code == "BIO 101"


# ---- web: the card shows / omits the class line ----

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


def seed(client, engine, *, course_code, email="card@x.com"):
    client.post("/signup", data={"email": email, "password": "hunter2pw"},
                follow_redirects=False)
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=user.id, label="Yavapai", base_url=BASE,
                          account_type="student", access_token="tok")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        a = Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Lab",
                       due_at=datetime(2026, 6, 25, 12, 0), points_possible=20.0,
                       submission_types=["online_upload"], html_url=f"{BASE}/a/1",
                       workflow_state="unsubmitted", course_code=course_code)
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def test_card_shows_course_code(client, engine):
    seed(client, engine, course_code="BIO 101")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "card__class" in resp.text
    assert "BIO 101" in resp.text


def test_card_omits_class_when_no_code(client, engine):
    seed(client, engine, course_code="")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "card__class" not in resp.text
```

- [ ] **Step 2: Run the layer to confirm it is RED**

Run: `.venv/Scripts/python.exe tools/run_to_html.py coursecode-red tests/test_coursecode.py`
Expected: `[RED ...]`. `test_sync_stores_course_code`, `test_card_shows_course_code`, and `test_card_omits_class_when_no_code` fail because `Assignment` has no `course_code` (the model rejects the kwarg / SQLite has no column / markup is absent); `test_fetch_courses_includes_course_code` fails on the missing `code` key.

- [ ] **Step 3: Screenshot the red page**

Serve (file:// is blocked): `.venv/Scripts/python.exe -m http.server 8731 --directory docs/test-evidence` (background). Navigate the browser to `http://127.0.0.1:8731/coursecode-red.html`, screenshot the `.frame` element to `coursecode-red.png`, move it into `docs/test-evidence/`. Stop the server. Verify by eye: red FAILED lines legible.

- [ ] **Step 4: Add the Layer 15 section to the README**

Insert after the Layer 14 block, before the closing "How these are made" paragraph:

```markdown
**Layer 15 — class (course code) on the card**

A dashboard card showed the connection label (the account name) but not which class the assignment was for. `fetch_courses` now also returns each course's `course_code`, the sync job stores it on the assignment as `course_code`, and the card shows it as a small class line under the title (e.g. **BIO 101**) — omitted when a course has no code. The connection-label pill is unchanged.

Red — `Assignment.course_code`, the fetch key, the sync write, and the card markup don't exist yet:

![Course-code tests failing — feature missing](docs/test-evidence/coursecode-red.png)

Green — after adding the column, the fetch key, the sync write, and the card line:

![Course-code tests passing](docs/test-evidence/coursecode-green.png)
```

(The green PNG is captured in Task 4; the link is added now and the file lands before commit.)

- [ ] **Step 5: Do NOT commit yet.** Red and green PNGs are committed together at the end (Task 4).

---

## Task 2: Data — model column, fetch, sync

**Files:**
- Modify: `app/models.py`, `app/canvas.py`, `app/sync.py`
- Test: `tests/test_coursecode.py` (fetch + sync tests)

**Interfaces:**
- Produces: `Assignment.course_code: str`; `fetch_courses(...)[i]["code"]`; `_upsert(session, connection_id, parsed, course_code="")`.

- [ ] **Step 1: Add the column to `Assignment` (`app/models.py`)**

After the `submission_types` field line, add:

```python
    course_code: str = ""
```

- [ ] **Step 2: Return the code from `fetch_courses` (`app/canvas.py`)**

Change:

```python
        courses.extend({"id": c.get("id"), "name": c.get("name")} for c in response.json())
```
to:
```python
        courses.extend(
            {"id": c.get("id"), "name": c.get("name"), "code": c.get("course_code")}
            for c in response.json()
        )
```

- [ ] **Step 3: Store the code during sync (`app/sync.py`)**

In `sync_connection`, change the upsert call:

```python
        for parsed in parsed_list:
            _upsert(session, connection.id, parsed)
```
to:
```python
        for parsed in parsed_list:
            _upsert(session, connection.id, parsed, course.get("code") or "")
```

Then change `_upsert` to accept and set the code:

```python
def _upsert(session, connection_id, parsed, course_code=""):
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
    target.fetched_at = _now()
    session.add(target)
```

- [ ] **Step 4: Run the fetch + sync tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_coursecode.py -k "fetch or sync" -v`
Expected: `test_fetch_courses_includes_course_code` and `test_sync_stores_course_code` PASS.

---

## Task 3: Card display — template + style

**Files:**
- Modify: `app/templates/report.html`, `app/static/app.css`
- Test: `tests/test_coursecode.py` (card tests)

**Interfaces:**
- Consumes: `Assignment.course_code`.

- [ ] **Step 1: Active-board card class line (`app/templates/report.html`)**

Change:

```html
                <h3 class="card__title">
                  <a href="/assignments/{{ a.id }}">{{ a.name }}</a>
                </h3>
```
to:
```html
                <h3 class="card__title">
                  <a href="/assignments/{{ a.id }}">{{ a.name }}</a>
                </h3>
                {% if a.course_code %}<p class="card__class">{{ a.course_code }}</p>{% endif %}
```

- [ ] **Step 2: Completed-section card class line (`app/templates/report.html`)**

Change:

```html
            <h3 class="card__title">{{ a.name }}</h3>
```
to:
```html
            <h3 class="card__title">{{ a.name }}</h3>
            {% if a.course_code %}<p class="card__class">{{ a.course_code }}</p>{% endif %}
```

- [ ] **Step 3: Style the class line (`app/static/app.css`)**

After the `.tag--quiz` block (added in Layer 14), add:

```css
.card__class {
  margin: .2rem 0 0;
  font: 600 .78rem/1.2 var(--sans);
  color: var(--muted);
}
```

- [ ] **Step 4: Run the full coursecode layer**

Run: `.venv/Scripts/python.exe -m pytest tests/test_coursecode.py -v`
Expected: all PASS (fetch + sync + both card tests).

---

## Task 4: Capture GREEN, verify, commit

**Files:**
- Capture: `docs/test-evidence/coursecode-green.png`

- [ ] **Step 1: Render the green page**

Run: `.venv/Scripts/python.exe tools/run_to_html.py coursecode-green tests/test_coursecode.py`
Expected: `[GREEN (all passed)]`.

- [ ] **Step 2: Screenshot the green page**

Serve: `.venv/Scripts/python.exe -m http.server 8731 --directory docs/test-evidence` (background). Navigate to `http://127.0.0.1:8731/coursecode-green.html`, screenshot the `.frame` element to `coursecode-green.png`, move into `docs/test-evidence/`. Stop the server. Verify by eye.

- [ ] **Step 3: Run the full suite and evidence check**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass/skip as before plus the new `coursecode` layer.
Run: `.venv/Scripts/python.exe tools/check_evidence.py`
Expected: `OK - ... coursecode ...`.

- [ ] **Step 4: Commit (full pre-commit hook) and push**

```bash
git add app/ tests/test_coursecode.py docs/test-evidence/coursecode-red.png docs/test-evidence/coursecode-green.png docs/test-evidence/coursecode-red.html docs/test-evidence/coursecode-green.html README.md
git commit -m "Show class (course code) on dashboard cards (Layer 15)"
git push origin main
```
Expected: `[pre-commit] ok`, full suite passes, commit lands on `main`, push succeeds.

---

## Task 5: Migrate the live database (operational, run once)

**Why:** `create_all` only creates missing tables, not new columns. The running app
queries `Assignment.course_code`, so the live `assignments` table needs the column
before the new code serves requests. Tests are unaffected (fresh in-memory DB).

- [ ] **Step 1: Add the column to the live DB (idempotent, dialect-agnostic)**

Run against the same `DATABASE_URL` the app uses:

```bash
.venv/Scripts/python.exe -c "
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import inspect, text
from app.db import make_engine
eng = make_engine()
cols = [c['name'] for c in inspect(eng).get_columns('assignments')]
if 'course_code' not in cols:
    with eng.begin() as conn:
        conn.execute(text(\"ALTER TABLE assignments ADD COLUMN course_code VARCHAR NOT NULL DEFAULT ''\"))
    print('added course_code')
else:
    print('course_code already present')
"
```
Expected: `added course_code` (or `already present` on re-run). The `ALTER ... ADD COLUMN ... DEFAULT ''` syntax works on both Postgres (Neon) and SQLite.

- [ ] **Step 2: Backfill note**

Existing rows now have `course_code = ''` and show no class line until the **next daily sync** re-populates them. To see it immediately, run a sync (the daily job entry point) or wait for the scheduled run.

---

## Self-Review

**Spec coverage:**
- `course_code` column → Task 2 Step 1.
- `fetch_courses` returns code → Task 2 Step 2; asserted by `test_fetch_courses_includes_course_code`.
- Sync stores code → Task 2 Step 3; asserted by `test_sync_stores_course_code`.
- Card shows/omits class line → Task 3; asserted by `test_card_shows_course_code`, `test_card_omits_class_when_no_code`.
- Connection pill unchanged → not modified in Task 3 (only the title line is added).
- Migration + backfill → Task 5.
- Evidence (red live, green) → Task 1 (red) + Task 4 (green); both PNGs committed in Task 4.

**Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected output.

**Type consistency:** `course_code` is `str` everywhere — model column, `_upsert` parameter (default `""`), template guard `{% if a.course_code %}`, and tests. `fetch_courses` emits the key `"code"`, which `sync_connection` reads as `course.get("code")`.

**Coupling to verify during execution:** The existing `sync` layer test `test_fetch_courses_follows_pagination` only asserts `[c["id"] ...]`, so the added `"code"` key does not break it. The seeded card assignment (`due_at` in 2026-06-25, unsubmitted, not missing) lands on the active board so the card renders. CSS uses `var(--sans)` / `var(--muted)`, both already defined.
