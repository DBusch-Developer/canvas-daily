# Group Upcoming By Week Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Break the dashboard's Upcoming column into collapsible Monday-start week groups so the board stays short and the Completed section is reachable without a long scroll.

**Architecture:** A pure `group_by_week(assignments, now)` in `app/dates.py` returns ordered `{label, assignments}` week groups. The report route passes the grouped upcoming to the template, which renders each week as a `<details>` (first open). A `card` macro renders the shared card markup in both the flat columns and the weekly groups.

**Tech Stack:** pure Python date helper, Jinja2 (macro + `<details>`), FastAPI route, pytest.

## Global Constraints

- TDD-first, red before green. New behavior is its own layer `tests/test_upcomingweeks.py` (label `upcomingweeks`).
- Test evidence: `docs/test-evidence/upcomingweeks-red.png` and `upcomingweeks-green.png`, both referenced in README as **Layer 18**. Red captured live before any implementation. Both PNGs committed together in the final green commit.
- Weeks are Monday-start calendar weeks. Only weeks with assignments appear; empty weeks are skipped.
- Past due and Due today columns are unchanged in behavior. The card markup is extracted to a macro but renders identically (existing dashboard tests must stay green).
- **Naming note:** the week group's assignment list key is `assignments` (not `items`) to avoid colliding with Jinja's dict `.items()` method when the template writes `week.assignments`.
- Layer 18 tests run on in-memory SQLite / pure objects (no Neon gate).
- Commit with the pre-commit hook. Short commit messages. Commit on `main`, no branches.
- Run pytest via the venv: `.venv/Scripts/python.exe -m pytest ...`.

---

## File Structure

- `tests/test_upcomingweeks.py` — **create.** Layer 18 tests: `group_by_week` + dashboard weekly disclosures.
- `app/dates.py` — **modify.** Add `group_by_week` (+ `timedelta` import).
- `app/web.py` — **modify.** Report route passes `upcoming_weeks`.
- `app/templates/report.html` — **modify.** `card` macro; upcoming column renders weekly `<details>`.
- `app/static/app.css` — **modify.** `.weekgroup` styles.
- `README.md` — **modify.** Add Layer 18 to the Test evidence list.

---

## Task 1: Write the failing Layer 18 tests and capture RED

**Files:**
- Create: `tests/test_upcomingweeks.py`
- Capture: `docs/test-evidence/upcomingweeks-red.png`
- Modify: `README.md` (add Layer 18 section with the red image)

**Interfaces:**
- Consumes (does not exist yet): `app.dates.group_by_week(assignments, now) -> list[dict]`; the weekly `<details>` markup.
- Produces: the enforced `upcomingweeks` layer.

- [ ] **Step 1: Write `tests/test_upcomingweeks.py`**

```python
"""Layer 18 - group the Upcoming column by week.

`group_by_week` buckets upcoming assignments into Monday-start calendar weeks,
labelled 'This week' / 'Next week' / 'Week of <Mon date>', skipping empty weeks.
The dashboard renders each week as a collapsible <details> (first open). The
grouping tests are pure; the dashboard test uses the FastAPI TestClient against
in-memory SQLite.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.dates import group_by_week
from app.models import Assignment, Connection, User
from app.web import create_app, get_session

# 2026-06-17 is a Wednesday; its week's Monday is 2026-06-15.
NOW = datetime(2026, 6, 17, 9, 0)


def at(days):
    """A stand-in assignment due `days` from NOW (group_by_week only reads due_at)."""
    return SimpleNamespace(due_at=NOW + timedelta(days=days))


# ---- group_by_week (pure) ----

def test_empty_input_yields_no_groups():
    assert group_by_week([], NOW) == []


def test_this_next_and_later_labels():
    groups = group_by_week([at(1), at(7), at(20)], NOW)
    labels = [g["label"] for g in groups]
    assert labels[0] == "This week"
    assert labels[1] == "Next week"
    assert labels[2].startswith("Week of")


def test_skips_empty_middle_weeks():
    # This week and ~3 weeks out, nothing between -> exactly two groups.
    groups = group_by_week([at(1), at(21)], NOW)
    assert len(groups) == 2


def test_items_land_in_their_week_in_order():
    a1, a2, a3 = at(1), at(2), at(8)
    groups = group_by_week([a1, a2, a3], NOW)
    assert groups[0]["assignments"] == [a1, a2]
    assert groups[1]["assignments"] == [a3]


# ---- dashboard renders weekly disclosures ----

@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
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


def seed_two_upcoming(client, engine, email="wk@x.com"):
    client.post("/signup", data={"email": email, "password": "hunter2pw"},
                follow_redirects=False)
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=user.id, label="Yavapai", base_url="https://school.test",
                          account_type="student", access_token="tok")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        # Fixed far-future dates in different weeks -> always "upcoming".
        for cid, name, due in [
            (1, "Essay One", datetime(2030, 6, 4, 12, 0)),
            (2, "Essay Two", datetime(2030, 6, 18, 12, 0)),
        ]:
            s.add(Assignment(connection_id=conn.id, canvas_assignment_id=cid, name=name,
                             due_at=due, points_possible=10.0,
                             submission_types=["online_upload"],
                             html_url=f"https://school.test/a/{cid}",
                             workflow_state="unsubmitted"))
        s.commit()


def test_dashboard_renders_weekly_disclosures(client, engine):
    seed_two_upcoming(client, engine)
    resp = client.get("/")
    assert resp.status_code == 200
    # The upcoming column is split into week <details> groups, first one open.
    assert 'class="weekgroup"' in resp.text
    assert 'class="weekgroup" open' in resp.text
    assert "Essay One" in resp.text
    assert "Essay Two" in resp.text
```

- [ ] **Step 2: Run the layer to confirm it is RED**

Run: `.venv/Scripts/python.exe tools/run_to_html.py upcomingweeks-red tests/test_upcomingweeks.py`
Expected: `[RED ...]`. The pure tests fail with `ImportError: cannot import name 'group_by_week'`; the dashboard test fails its `weekgroup` assertion.

- [ ] **Step 3: Screenshot the red page**

Serve (file:// is blocked): `.venv/Scripts/python.exe -m http.server 8731 --directory docs/test-evidence` (background). Navigate the browser to `http://127.0.0.1:8731/upcomingweeks-red.html`, screenshot the `.frame` element to `upcomingweeks-red.png`, move it into `docs/test-evidence/`. Stop the server. Verify by eye.

- [ ] **Step 4: Add the Layer 18 section to the README**

Insert after the Layer 17 block, before the closing "How these are made" paragraph:

```markdown
**Layer 18 — group the Upcoming column by week**

The Upcoming column could hold many weeks of assignments, making the board tall and pushing the Completed section far down. `group_by_week` buckets upcoming work into Monday-start calendar weeks ("This week" / "Next week" / "Week of Jun 30"), skipping empty weeks, and the dashboard renders each week as a collapsible disclosure (first open) — so the column stays short and Completed is reachable.

Red — `group_by_week` and the weekly markup don't exist yet:

![Upcoming-by-week tests failing](docs/test-evidence/upcomingweeks-red.png)

Green — after adding the grouping function and the weekly disclosures:

![Upcoming-by-week tests passing](docs/test-evidence/upcomingweeks-green.png)
```

(The green PNG is captured in Task 5; the link is added now and the file lands before commit.)

- [ ] **Step 5: Do NOT commit yet.** Red and green PNGs are committed together at the end (Task 5).

---

## Task 2: `group_by_week` in `app/dates.py`

**Files:**
- Modify: `app/dates.py`
- Test: `tests/test_upcomingweeks.py` (pure tests)

**Interfaces:**
- Produces: `group_by_week(assignments, now) -> list[{"label": str, "assignments": list}]`.

- [ ] **Step 1: Add `timedelta` to the import**

Change `app/dates.py` line 1:

```python
from datetime import datetime
```
to:
```python
from datetime import datetime, timedelta
```

- [ ] **Step 2: Add `group_by_week`**

Append to `app/dates.py`:

```python
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
```

- [ ] **Step 3: Run the pure tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_upcomingweeks.py -k "not dashboard" -v`
Expected: the four `group_by_week` tests PASS.

---

## Task 3: Pass `upcoming_weeks` from the report route

**Files:**
- Modify: `app/web.py` (report route ~160-165, and the `app.dates` import)

**Interfaces:**
- Consumes: `group_by_week`. Produces: template context key `upcoming_weeks`.

- [ ] **Step 1: Import `group_by_week`**

In `app/web.py`, add `group_by_week` to the dates import. If there is no `app.dates` import yet, add:

```python
from app.dates import group_by_week
```
(near the other `from app....` imports, e.g. by `from app.reports import report_for_user`).

- [ ] **Step 2: Compute and pass `upcoming_weeks`**

Change the report route body:

```python
        buckets = report_for_user(session, user.id, _now())
        return TEMPLATES.TemplateResponse(request, "report.html", {"buckets": buckets})
```
to:
```python
        buckets = report_for_user(session, user.id, _now())
        upcoming_weeks = group_by_week(buckets["upcoming"], _now())
        return TEMPLATES.TemplateResponse(
            request, "report.html",
            {"buckets": buckets, "upcoming_weeks": upcoming_weeks})
```

---

## Task 4: Render the upcoming column as weekly disclosures

**Files:**
- Modify: `app/templates/report.html`, `app/static/app.css`
- Test: `tests/test_upcomingweeks.py` (dashboard test)

**Interfaces:**
- Consumes: `upcoming_weeks` (list of `{label, assignments}`).

- [ ] **Step 1: Add a `card` macro at the top of `report.html`**

Immediately after the `{% block body %}` line, insert the macro (identical markup to today's active-board card):

```html
{% macro card(a, mod, title) %}
  {% set tone = 'danger' if (a.missing or a.late)
                else ('done' if (a.workflow_state == 'graded' or a.submitted_at) else mod) %}
  {% set status = 'Missing' if a.missing
                  else ('Late' if a.late
                  else ('Complete' if (a.workflow_state == 'graded' or a.submitted_at) else title)) %}
  <li class="card">
    <div class="card__top">
      <span class="badge badge--{{ tone }}">{{ status }}</span>
      {% if a.is_quiz %}<span class="tag tag--quiz">Quiz</span>{% endif %}
      <span class="course-pill">{{ a.connection.label }}</span>
    </div>
    <h3 class="card__title">
      <a href="/assignments/{{ a.id }}">{{ a.name }}</a>
    </h3>
    {% if a.course_code %}<p class="card__class">{{ a.course_trimmed }}</p>{% endif %}
    <div class="card__foot">
      <span class="due">due {{ a.due_at }}</span>
      {% if a.html_url %}
        <a class="card__open" href="{{ a.html_url }}" target="_blank" rel="noopener">Open ↗</a>
      {% endif %}
    </div>
  </li>
{% endmacro %}
```

- [ ] **Step 2: Replace the column body to branch on the upcoming column**

Replace the existing body of the column (the `{% if buckets[key] %}` … `{% endif %}` block, currently lines ~56-92) with:

```html
        {% if key == 'upcoming' %}
          {% if upcoming_weeks %}
            {% for week in upcoming_weeks %}
              <details class="weekgroup"{% if loop.first %} open{% endif %}>
                <summary class="weekgroup__summary">
                  {{ week.label }} <span class="weekgroup__count">{{ week.assignments | length }}</span>
                </summary>
                <ul class="cards">
                  {% for a in week.assignments %}{{ card(a, mod, title) }}{% endfor %}
                </ul>
              </details>
            {% endfor %}
          {% else %}
            <div class="empty">
              <p class="empty__title">All clear</p>
              <p class="empty__sub">No upcoming work on the radar yet.</p>
            </div>
          {% endif %}
        {% elif buckets[key] %}
          <ul class="cards">
            {% for a in buckets[key] %}{{ card(a, mod, title) }}{% endfor %}
          </ul>
        {% else %}
          <div class="empty">
            <p class="empty__title">All clear</p>
            <p class="empty__sub">
              {% if mod == 'past' %}Nothing overdue. Nice work staying ahead.
              {% else %}Nothing due today.{% endif %}
            </p>
          </div>
        {% endif %}
```

(The `column__count` at the top still shows `buckets[key] | length`, the total for the column.)

- [ ] **Step 3: Style the week disclosures (`app/static/app.css`)**

After the `.card__class` rule (added in Layer 15/17), add:

```css
.weekgroup { border-top: 1px solid var(--border); }
.weekgroup:first-of-type { border-top: 0; }
.weekgroup__summary {
  cursor: pointer;
  list-style: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: .6rem;
  padding: .6rem 0;
  font: 600 .9rem/1 var(--sans);
  color: var(--navy);
}
.weekgroup__summary::-webkit-details-marker { display: none; }
.weekgroup__count { color: var(--muted); font-size: .82rem; }
.weekgroup .cards { margin: 0 0 .6rem; }
```

- [ ] **Step 4: Run the full layer**

Run: `.venv/Scripts/python.exe -m pytest tests/test_upcomingweeks.py -v`
Expected: all PASS (pure + dashboard).

---

## Task 5: Capture GREEN, verify, commit

**Files:**
- Capture: `docs/test-evidence/upcomingweeks-green.png`

- [ ] **Step 1: Render the green page**

Run: `.venv/Scripts/python.exe tools/run_to_html.py upcomingweeks-green tests/test_upcomingweeks.py`
Expected: `[GREEN (all passed)]`.

- [ ] **Step 2: Screenshot the green page**

Serve: `.venv/Scripts/python.exe -m http.server 8731 --directory docs/test-evidence` (background). Navigate to `http://127.0.0.1:8731/upcomingweeks-green.html`, screenshot the `.frame` element to `upcomingweeks-green.png`, move into `docs/test-evidence/`. Stop the server. Verify by eye.

- [ ] **Step 3: Run the full suite and evidence check**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass — including the existing dashboard tests (the `card` macro renders identical markup, so Layer 11/14/15/17 dashboard assertions still hold).
Run: `.venv/Scripts/python.exe tools/check_evidence.py`
Expected: `OK - ... upcomingweeks ...`.

- [ ] **Step 4: Commit (full pre-commit hook) and push**

```bash
git add app/ tests/test_upcomingweeks.py docs/test-evidence/upcomingweeks-red.png docs/test-evidence/upcomingweeks-green.png docs/test-evidence/upcomingweeks-red.html docs/test-evidence/upcomingweeks-green.html README.md
git commit -m "Group the Upcoming column into collapsible week sections (Layer 18)"
git push origin main
```
Expected: `[pre-commit] ok`, full suite passes, commit lands on `main`, push succeeds.

---

## Self-Review

**Spec coverage:**
- `group_by_week` (Monday weeks, labels, skip empty, order) → Task 2; asserted by the four pure tests.
- Route passes grouped upcoming → Task 3.
- Upcoming column as weekly `<details>`, first open → Task 4 Step 2; asserted by `test_dashboard_renders_weekly_disclosures`.
- Past due / Due today unchanged, empty-state preserved → Task 4 Step 2 (`elif buckets[key]` / `else`).
- Styling → Task 4 Step 3.
- Evidence (red live, green) → Task 1 (red) + Task 5 (green); both PNGs committed in Task 5.

**Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected output.

**Type consistency:** `group_by_week` returns `list[{"label": str, "assignments": list}]`; the template reads `week.label` and `week.assignments` (the latter named to avoid Jinja's `.items()` collision). The `card` macro signature `card(a, mod, title)` matches every call site.

**Coupling to verify during execution:** The `card` macro reproduces the current active-board card markup exactly, so Layer 11/14/15/17 dashboard tests (which assert on `tag--quiz`, `card__class`, trimmed values, completed disclosure) stay green; the completed section keeps its own separate markup and is not macro-ified. The pure tests pin `NOW = 2026-06-17` (a Wednesday, week-Monday 2026-06-15) so the week-label math is deterministic; the dashboard test uses fixed 2030 due dates so the assignments are always "upcoming" regardless of when the suite runs.
