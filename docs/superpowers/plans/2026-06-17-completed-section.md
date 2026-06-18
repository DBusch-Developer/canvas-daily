# Completed Work in Its Own Section — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Completed assignments (submitted, graded, or excused) leave the Past due / Due today / Upcoming buckets and live in a collapsible "Completed" section on the dashboard — read-only, no detail page, no AI breakdown — and drop out of the daily email.

**Architecture:** `report_for_user` checks a completion predicate before date-bucketing and routes done work into a new `completed` bucket. The dashboard renders a `<details>` disclosure for it; the email already renders only the three date sections, so it just needs its total fixed. `classify_due` (Layer 1) is untouched.

**Tech Stack:** SQLModel (Postgres/Neon), Jinja2 (`<details>` disclosure, no JS), pytest (TestClient + integration against the Neon test branch).

## Global Constraints

- TDD-first: write the failing test, observe RED live before implementation. Red captured live for evidence — never reenacted.
- Tokens never logged or rendered; the completed rows render only name/label/due/status/Canvas-link — never the token.
- Completed = `submitted_at is not None` OR `workflow_state == "graded"` OR `excused`. A **missing** item (past due, not submitted) is NOT completed and stays in Past due.
- Completed rows are read-only: **no `/assignments/{id}` link, no breakdown button.**
- Don't change `classify_due`. One code path for one connection and for four.
- New feature work is its own layer: `tests/test_completed.py` + its own README "Layer 11" section (description → red → green) with `completed-red.png` / `completed-green.png`.
- Intermediate tasks commit WIP with `git commit --no-verify` (the new test file makes the pre-commit evidence check demand `completed` screenshots before the layer is finished). Do NOT touch `docs/test-evidence/` or `README.md` except in the final evidence task. Stage files by explicit path — never `git add -A`. Do NOT push until the final task.
- Short, one-line commit messages, on `main`.
- Test runner: `.venv\Scripts\python.exe -m pytest` from repo root. `conftest.py` loads `.env` (`TEST_DATABASE_URL`, `TOKEN_ENCRYPTION_KEY`), so DB-backed tests run.

---

### Task 1: Completed bucket in `report_for_user`

**Files:**
- Modify: `app/reports.py`
- Create: `tests/test_completed.py` (scaffold + bucketing tests)

**Interfaces:**
- Consumes: `classify_due(due_at, now)`, `Assignment`, `Connection`.
- Produces: `report_for_user(session, user_id, now)` returns a dict with keys `past_due`, `due_today`, `upcoming`, **`completed`**. `_is_completed(assignment) -> bool`.

- [ ] **Step 1: Write the failing tests + scaffold**

Create `tests/test_completed.py`:

```python
"""Layer 11 — completed work in its own section (Neon test branch + TestClient)."""

import os
from datetime import datetime

import httpx
import pytest
from sqlmodel import Session, SQLModel, select

from app.db import make_engine
from app.mailer import build_report_email
from app.models import Assignment, Connection, User
from app.reports import report_for_user
from app.web import create_app, get_canvas_client_factory, get_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (completed-section tests need a Neon test branch)",
)

NOW = datetime(2026, 6, 17, 12, 0)
PAST = datetime(2026, 6, 10, 9, 0)        # clearly past, even vs the real clock
TODAY_LATER = datetime(2026, 6, 17, 18, 0)  # same calendar day as NOW, later
FUTURE = datetime(2030, 1, 1, 9, 0)


@pytest.fixture(scope="module")
def engine():
    eng = make_engine(os.environ["TEST_DATABASE_URL"])
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture(autouse=True)
def wipe(engine):
    yield
    with engine.begin() as conn:
        for table in reversed(SQLModel.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture
def app(engine):
    application = create_app()
    application.dependency_overrides[get_engine] = lambda: engine
    application.dependency_overrides[get_canvas_client_factory] = lambda: (
        lambda: httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[])))
    )
    return application


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


def signup(client, email="parent@x.com", password="hunter2pw"):
    return client.post("/signup", data={"email": email, "password": password},
                       follow_redirects=False)


def _user(session, email):
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        user = User(email=email, password_hash="h")
        session.add(user); session.commit(); session.refresh(user)
    return user


def seed(engine, email, *, cid=1, name="A", due_at=PAST, **fields):
    """Create (user if needed) + a fresh connection + one assignment. Return assignment id."""
    with Session(engine) as s:
        user = _user(s, email)
        conn = Connection(user_id=user.id, label="Mine", base_url="https://school.test",
                          account_type="student", access_token="tok")
        s.add(conn); s.commit(); s.refresh(conn)
        a = Assignment(connection_id=conn.id, canvas_assignment_id=cid, name=name,
                       due_at=due_at, submission_types=[], html_url="https://school.test/a/1",
                       description="", **fields)
        s.add(a); s.commit(); s.refresh(a)
        return a.id


def _buckets(engine, email):
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        return report_for_user(s, user.id, NOW)


def test_submitted_past_due_goes_to_completed(engine):
    seed(engine, "a@x.com", due_at=PAST, submitted_at=datetime(2026, 6, 9, 8, 0))
    b = _buckets(engine, "a@x.com")
    assert len(b["completed"]) == 1
    assert b["past_due"] == []


def test_graded_goes_to_completed(engine):
    seed(engine, "b@x.com", due_at=PAST, workflow_state="graded")
    b = _buckets(engine, "b@x.com")
    assert len(b["completed"]) == 1
    assert b["past_due"] == []


def test_excused_goes_to_completed(engine):
    seed(engine, "c@x.com", due_at=PAST, excused=True)
    b = _buckets(engine, "c@x.com")
    assert len(b["completed"]) == 1
    assert b["past_due"] == []


def test_missing_past_due_stays_in_past_due(engine):
    seed(engine, "d@x.com", due_at=PAST, missing=True)
    b = _buckets(engine, "d@x.com")
    assert len(b["past_due"]) == 1
    assert b["completed"] == []


def test_not_done_due_today_stays_in_due_today(engine):
    seed(engine, "e@x.com", due_at=TODAY_LATER)
    b = _buckets(engine, "e@x.com")
    assert len(b["due_today"]) == 1
    assert b["completed"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_completed.py -v`
Expected: FAIL — `report_for_user` has no `"completed"` key, so the completed-bucket assertions raise `KeyError: 'completed'` (and submitted/graded/excused items land in `past_due`).

- [ ] **Step 3: Add the predicate and the bucket**

Replace `app/reports.py` body with:

```python
"""Build a user's report: every assignment across all their connections.

Completed work (submitted, graded, or excused) goes to its own `completed`
bucket regardless of due date. Everything else is grouped Past due / Due today /
Upcoming via the Layer 1 date classifier — one classifier, not a second copy.
"""

from sqlmodel import select

from app.dates import classify_due
from app.models import Assignment, Connection


def _is_completed(assignment):
    """Done = turned in, graded, or excused. 'Missing' is not completed."""
    return (
        assignment.submitted_at is not None
        or assignment.workflow_state == "graded"
        or assignment.excused
    )


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
            buckets[classify_due(assignment.due_at, now)].append(assignment)
    return buckets
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_completed.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: PASS. The existing `test_e2e.py::test_report_groups_and_sorts_by_due_date` seeds assignments with no submission state (not completed), so it is unaffected.

- [ ] **Step 6: Commit (WIP)**

```bash
git add app/reports.py tests/test_completed.py
git commit --no-verify -m "Route completed work to its own report bucket"
```

---

### Task 2: Completed disclosure on the dashboard

**Files:**
- Modify: `app/templates/report.html`
- Test: `tests/test_completed.py`

**Interfaces:**
- Consumes: `buckets.completed` from Task 1; the existing `app`/`client`/`signup`/`seed` fixtures in `tests/test_completed.py`.
- Produces: a `<details>` "Completed (N)" disclosure on `GET /` rendering completed rows with no detail link and no breakdown.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_completed.py`:

```python
def test_completed_item_is_separated_from_the_board(client, engine):
    signup(client, email="dash@x.com")
    todo_id = seed(engine, "dash@x.com", cid=1, name="Todo lab", due_at=PAST, missing=True)
    done_id = seed(engine, "dash@x.com", cid=2, name="Done lab", due_at=PAST,
                   submitted_at=datetime(2026, 6, 9, 8, 0))

    body = client.get("/").text

    # The not-done item is in the board with its detail link.
    assert f"/assignments/{todo_id}" in body
    assert "Todo lab" in body
    # The completed item shows in the Completed disclosure, with NO detail link.
    assert "Completed (1)" in body
    assert "Done lab" in body
    assert f"/assignments/{done_id}" not in body


def test_no_completed_disclosure_when_none_completed(client, engine):
    signup(client, email="nodone@x.com")
    seed(engine, "nodone@x.com", name="Todo only", due_at=PAST, missing=True)

    body = client.get("/").text
    assert "Todo only" in body
    assert "Completed (" not in body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_completed.py::test_completed_item_is_separated_from_the_board -v`
Expected: FAIL — the completed item is not yet rendered in a disclosure (`"Completed (1)"` absent), and "Done lab" / its detail link behavior doesn't match.

- [ ] **Step 3: Add the disclosure to the template**

In `app/templates/report.html`, insert this block immediately after the `</div>` that closes `<div class="board">` (currently line 93) and before `{% endblock %}`:

```html
  {% if buckets.completed %}
    <details class="board__completed">
      <summary>Completed ({{ buckets.completed | length }}) — tap to reflect</summary>
      <ul class="cards">
        {% for a in buckets.completed %}
          <li class="card">
            <div class="card__top">
              <span class="badge badge--done">{{ 'Excused' if a.excused else ('Graded' if a.workflow_state == 'graded' else 'Complete') }}</span>
              <span class="course-pill">{{ a.connection.label }}</span>
            </div>
            <h3 class="card__title">{{ a.name }}</h3>
            <div class="card__foot">
              <span class="due">due {{ a.due_at }}</span>
              {% if a.html_url %}
                <a class="card__open" href="{{ a.html_url }}" target="_blank" rel="noopener">Open in Canvas ↗</a>
              {% endif %}
            </div>
          </li>
        {% endfor %}
      </ul>
    </details>
  {% endif %}
```

Note: the completed card title is **plain text** — no `<a href="/assignments/...">` and no breakdown affordance. The board columns above are unchanged; they now simply contain no completed items.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_completed.py tests/test_e2e.py -q`
Expected: PASS (the two new dashboard tests plus the existing report test).

- [ ] **Step 5: Commit (WIP)**

```bash
git add app/templates/report.html tests/test_completed.py
git commit --no-verify -m "Render completed work in a collapsible dashboard section"
```

---

### Task 3: Keep completed work out of the daily email total

**Files:**
- Modify: `app/mailer.py:23`
- Test: `tests/test_completed.py`

**Interfaces:**
- Consumes: `build_report_email(session, user, now)`; `report_for_user` (now with a `completed` bucket).
- Produces: the email subject's total counts only the three date sections; the body already excludes completed.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_completed.py`:

```python
def test_email_excludes_completed_from_body_and_total(engine):
    seed(engine, "mail@x.com", cid=1, name="Todo", due_at=PAST, missing=True)
    seed(engine, "mail@x.com", cid=2, name="Done", due_at=PAST,
         submitted_at=datetime(2026, 6, 9, 8, 0))

    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == "mail@x.com")).one()
        subject, body = build_report_email(s, user, NOW)

    assert "Todo" in body
    assert "Done" not in body
    assert "— 1 assignment" in subject   # only the not-done item is counted
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_completed.py::test_email_excludes_completed_from_body_and_total -v`
Expected: FAIL — `build_report_email` sums all buckets, so the total is 2 → subject reads "— 2 assignments", failing the `"— 1 assignment"` assertion. (The body already excludes completed.)

- [ ] **Step 3: Fix the total**

In `app/mailer.py`, change the `total` line inside `build_report_email` (currently `total = sum(len(items) for items in buckets.values())`) to:

```python
    total = sum(len(buckets[key]) for key, _ in _SECTIONS)
```

- [ ] **Step 4: Run the tests to verify green**

Run: `.venv\Scripts\python.exe -m pytest tests/test_completed.py tests/test_mailer.py -q`
Expected: PASS (the new email test plus the existing mailer suite).

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: PASS — `test_completed.py` 8 tests, everything else green.

- [ ] **Step 6: Commit (WIP)**

```bash
git add app/mailer.py tests/test_completed.py
git commit --no-verify -m "Exclude completed work from the daily email total"
```

---

### Task 4: Test evidence + README (Layer 11)

**Files:**
- Create: `docs/test-evidence/completed-red.png`, `docs/test-evidence/completed-green.png`
- Modify: `README.md`

**Interfaces:**
- Consumes: `tools/run_to_html.py`, the browser screenshot flow from CLAUDE.md.
- Produces: a "Layer 11 — completed work in its own section" README block (description → red → green) with the two PNGs; `check_evidence.py` passing.

> Controller-driven (needs the browser). The feature baseline is the commit just before Task 1. Capture the red by reverting the feature implementation (`app/reports.py`, `app/templates/report.html`, `app/mailer.py`) to that baseline in the working tree (keep `tests/test_completed.py`), rendering, then restoring.

- [ ] **Step 1: Capture green**

Run: `.venv\Scripts\python.exe tools/run_to_html.py completed-green tests/test_completed.py` → confirm GREEN (8 tests).

- [ ] **Step 2: Capture red (feature absent)**

Revert `app/reports.py`, `app/templates/report.html`, `app/mailer.py` to the feature baseline in the working tree; run `.venv\Scripts\python.exe tools/run_to_html.py completed-red tests/test_completed.py` → confirm RED (the `completed`-bucket assertions fail / `KeyError`); then restore (`git checkout HEAD -- ...`).

- [ ] **Step 3: Screenshot both pages**

Serve and screenshot per CLAUDE.md:

```bash
.venv\Scripts\python.exe -m http.server 8735 --directory docs/test-evidence
```

Navigate to `completed-red.html` and `completed-green.html`; screenshot each to the matching PNG in `docs/test-evidence/`. Verify by eye (red shows red FAILED, green shows green passes).

- [ ] **Step 4: Add the README block**

In `README.md`, add a "**Layer 11 — completed work in its own section (Neon test branch + TestClient)**" section right after Layer 10's images and before the "How these are made" paragraph: a TDD description (completed bucket, dashboard disclosure, email exclusion), then the red image (`docs/test-evidence/completed-red.png`) and green image (`docs/test-evidence/completed-green.png`) with one-line captions.

- [ ] **Step 5: Verify and commit**

Run: `.venv\Scripts\python.exe -m pytest` then `.venv\Scripts\python.exe tools/check_evidence.py` (expect OK including `completed`). Stop the HTTP server.

```bash
git add docs/test-evidence README.md
git commit -m "Document completed-section as Layer 11"
```

---

## Self-Review

**Spec coverage:**
- Completion predicate (submitted/graded/excused), missing stays past_due → Task 1. ✓
- Completion checked before date bucketing; `completed` bucket added → Task 1. ✓
- `classify_due` unchanged → confirmed (Task 1 imports and reuses it). ✓
- Collapsible `<details>` disclosure, read-only rows, no detail link / no breakdown → Task 2. ✓
- Optional Canvas link, status chip (Complete/Graded/Excused) → Task 2. ✓
- Email excludes completed + total fixed → Task 3. ✓
- New Layer 11 evidence + README → Task 4. ✓

**Placeholder scan:** No TBD/"handle errors"/"similar to" — every code step is complete. ✓

**Type consistency:** `report_for_user` returns the same 4-key dict consumed by `report.html` (`buckets.completed`), `mailer` (`_SECTIONS` keys only), and the tests; `_is_completed` defined once in Task 1; `seed(...)` helper signature consistent across Tasks 1–3; the dashboard assertions match the template (`"Completed (1)"`, plain-text title so `/assignments/{done_id}` absent). ✓
