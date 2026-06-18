# Class Label (Short Code + Trimmed) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the class cleanly — the detail page header pill shows the short course code (`CSA250`), and the dashboard card shows the course string without its trailing `(22255)`.

**Architecture:** Two pure computed properties on `Assignment` derive from the stored `course_code`: `course_short` (leading token) and `course_trimmed` (strip trailing parenthetical). Templates read them. No model column, fetch, or sync change.

**Tech Stack:** SQLModel model properties, Jinja2 templates, pytest with FastAPI TestClient + in-memory SQLite.

## Global Constraints

- TDD-first, red before green. New behavior is its own layer `tests/test_classlabel.py` (label `classlabel`).
- Test evidence: `docs/test-evidence/classlabel-red.png` and `classlabel-green.png`, both referenced in README as **Layer 17**. Red captured live before any implementation. Both PNGs committed together in the final green commit.
- Derive from the existing `course_code` — no model column, fetch, or sync change.
- Layer 17 tests run on in-memory SQLite (no Neon gate).
- Commit with the pre-commit hook (evidence check + full suite). Short commit messages. Commit on `main`, no branches.
- Run pytest via the venv: `.venv/Scripts/python.exe -m pytest ...`.

---

## File Structure

- `tests/test_classlabel.py` — **create.** Layer 17 tests: both properties, detail header pill, card trimmed line.
- `app/models.py` — **modify.** Add `import re` and the `course_short` / `course_trimmed` properties.
- `app/templates/detail.html` — **modify.** Header pill shows `course_short` (fallback to connection label).
- `app/templates/report.html` — **modify.** Card class line shows `course_trimmed`.
- `README.md` — **modify.** Add Layer 17 to the Test evidence list.

---

## Task 1: Write the failing Layer 17 tests and capture RED

**Files:**
- Create: `tests/test_classlabel.py`
- Capture: `docs/test-evidence/classlabel-red.png`
- Modify: `README.md` (add Layer 17 section with the red image)

**Interfaces:**
- Consumes (does not exist yet): `Assignment.course_short`, `Assignment.course_trimmed`; the header pill / card markup using them.
- Produces: the enforced `classlabel` layer.

- [ ] **Step 1: Write `tests/test_classlabel.py`**

```python
"""Layer 17 - class label: short code on the detail page, trimmed on the card.

`course_short` is the leading token of course_code (e.g. 'CSA250'); the detail
page header pill shows it instead of the connection label. `course_trimmed` is
course_code without a trailing '(...)' section number; the dashboard card shows
it instead of the verbose full string. Both are pure properties; the page tests
use the FastAPI TestClient against in-memory SQLite.
"""

from datetime import datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Assignment, Connection, User
from app.web import create_app, get_session

VERBOSE = "CSA250 Intro Artificial Intelligence (22255)"


# ---- pure properties ----

def test_course_short_is_leading_token():
    a = Assignment(connection_id=1, canvas_assignment_id=1, name="x", course_code=VERBOSE)
    assert a.course_short == "CSA250"


def test_course_short_empty_when_no_code():
    a = Assignment(connection_id=1, canvas_assignment_id=2, name="x", course_code="")
    assert a.course_short == ""


def test_course_trimmed_strips_trailing_parenthetical():
    a = Assignment(connection_id=1, canvas_assignment_id=1, name="x", course_code=VERBOSE)
    assert a.course_trimmed == "CSA250 Intro Artificial Intelligence"


def test_course_trimmed_leaves_plain_value_and_empty():
    plain = Assignment(connection_id=1, canvas_assignment_id=2, name="x", course_code="BIO 101")
    empty = Assignment(connection_id=1, canvas_assignment_id=3, name="x", course_code="")
    assert plain.course_trimmed == "BIO 101"
    assert empty.course_trimmed == ""


# ---- web fixtures ----

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


def seed(client, engine, *, course_code, label="Diana", email="cl@x.com"):
    client.post("/signup", data={"email": email, "password": "hunter2pw"},
                follow_redirects=False)
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=user.id, label=label, base_url="https://school.test",
                          account_type="student", access_token="tok")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        a = Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Lab",
                       due_at=datetime(2026, 6, 25, 12, 0), points_possible=20.0,
                       submission_types=["online_upload"], html_url="https://school.test/a/1",
                       workflow_state="unsubmitted", course_code=course_code)
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


# ---- detail page header pill ----

def test_detail_header_pill_shows_short_code(client, engine):
    aid = seed(client, engine, course_code=VERBOSE, label="Diana")
    resp = client.get(f"/assignments/{aid}")
    assert resp.status_code == 200
    # The header pill renders the short code, not the connection label.
    assert 'course-pill course-pill--lg">CSA250<' in resp.text


def test_detail_header_pill_falls_back_to_connection(client, engine):
    aid = seed(client, engine, course_code="", label="Solo")
    resp = client.get(f"/assignments/{aid}")
    assert resp.status_code == 200
    assert 'course-pill course-pill--lg">Solo<' in resp.text


# ---- dashboard card ----

def test_card_shows_trimmed_class(client, engine):
    seed(client, engine, course_code=VERBOSE)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "CSA250 Intro Artificial Intelligence" in resp.text
    assert "(22255)" not in resp.text
```

- [ ] **Step 2: Run the layer to confirm it is RED**

Run: `.venv/Scripts/python.exe tools/run_to_html.py classlabel-red tests/test_classlabel.py`
Expected: `[RED ...]`. The property tests fail with `AttributeError` on `course_short` / `course_trimmed`; the page tests fail their assertions (pill still shows the connection label, card still shows the verbose string with `(22255)`).

- [ ] **Step 3: Screenshot the red page**

Serve (file:// is blocked): `.venv/Scripts/python.exe -m http.server 8731 --directory docs/test-evidence` (background). Navigate the browser to `http://127.0.0.1:8731/classlabel-red.html`, screenshot the `.frame` element to `classlabel-red.png`, move it into `docs/test-evidence/`. Stop the server. Verify by eye: red FAILED lines legible.

- [ ] **Step 4: Add the Layer 17 section to the README**

Insert after the Layer 16 block, before the closing "How these are made" paragraph:

```markdown
**Layer 17 — class label: short code on detail, trimmed on card**

The stored `course_code` is verbose (`CSA250 Intro Artificial Intelligence (22255)`). Two pure properties clean it up: `course_short` (leading token, `CSA250`) and `course_trimmed` (drops the trailing `(22255)`). The detail page header pill now shows the short code instead of the redundant connection label (the Connection metacard stays), and the dashboard card shows the trimmed course string.

Red — `course_short` / `course_trimmed` and the markup that uses them don't exist yet:

![Class-label tests failing](docs/test-evidence/classlabel-red.png)

Green — after adding the properties and updating the pill and card:

![Class-label tests passing](docs/test-evidence/classlabel-green.png)
```

(The green PNG is captured in Task 4; the link is added now and the file lands before commit.)

- [ ] **Step 5: Do NOT commit yet.** Red and green PNGs are committed together at the end (Task 4).

---

## Task 2: Model properties

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_classlabel.py` (property tests)

**Interfaces:**
- Produces: `Assignment.course_short: str`, `Assignment.course_trimmed: str`.

- [ ] **Step 1: Add `import re`**

At the top of `app/models.py`, add `import re` above `from datetime import datetime, timezone`:

```python
import re
from datetime import datetime, timezone
```

- [ ] **Step 2: Add the two properties to `Assignment`**

After the existing `is_quiz` property, add:

```python
    @property
    def course_short(self) -> str:
        """Leading code token of the course, e.g. 'CSA250'. Empty when no code."""
        return self.course_code.split()[0] if self.course_code else ""

    @property
    def course_trimmed(self) -> str:
        """course_code without a trailing '(...)' section number."""
        return re.sub(r"\s*\([^)]*\)\s*$", "", self.course_code).strip()
```

- [ ] **Step 3: Run the property tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_classlabel.py -k "short or trimmed" -v`
Expected: the four property tests PASS.

---

## Task 3: Templates — detail header pill and card line

**Files:**
- Modify: `app/templates/detail.html`, `app/templates/report.html`
- Test: `tests/test_classlabel.py` (page tests)

**Interfaces:**
- Consumes: `Assignment.course_short`, `Assignment.course_trimmed`.

- [ ] **Step 1: Detail header pill (`app/templates/detail.html`)**

Change line 17:

```html
      <span class="course-pill course-pill--lg">{{ a.connection.label }}</span>
```
to:
```html
      <span class="course-pill course-pill--lg">{{ a.course_short or a.connection.label }}</span>
```

- [ ] **Step 2: Card class line — active board (`app/templates/report.html`)**

Change (around line 73):

```html
                {% if a.course_code %}<p class="card__class">{{ a.course_code }}</p>{% endif %}
```
to:
```html
                {% if a.course_code %}<p class="card__class">{{ a.course_trimmed }}</p>{% endif %}
```

- [ ] **Step 3: Card class line — completed section (`app/templates/report.html`)**

Change (around line 109):

```html
            {% if a.course_code %}<p class="card__class">{{ a.course_code }}</p>{% endif %}
```
to:
```html
            {% if a.course_code %}<p class="card__class">{{ a.course_trimmed }}</p>{% endif %}
```

- [ ] **Step 4: Run the full classlabel layer**

Run: `.venv/Scripts/python.exe -m pytest tests/test_classlabel.py -v`
Expected: all PASS (properties + detail header + card).

---

## Task 4: Capture GREEN, verify, commit

**Files:**
- Capture: `docs/test-evidence/classlabel-green.png`

- [ ] **Step 1: Render the green page**

Run: `.venv/Scripts/python.exe tools/run_to_html.py classlabel-green tests/test_classlabel.py`
Expected: `[GREEN (all passed)]`.

- [ ] **Step 2: Screenshot the green page**

Serve: `.venv/Scripts/python.exe -m http.server 8731 --directory docs/test-evidence` (background). Navigate to `http://127.0.0.1:8731/classlabel-green.html`, screenshot the `.frame` element to `classlabel-green.png`, move into `docs/test-evidence/`. Stop the server. Verify by eye.

- [ ] **Step 3: Run the full suite and evidence check**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass (existing layers — including the Layer 15 card test, whose `"BIO 101"` has no parenthetical to trim — plus the new `classlabel` layer).
Run: `.venv/Scripts/python.exe tools/check_evidence.py`
Expected: `OK - ... classlabel ...`.

- [ ] **Step 4: Commit (full pre-commit hook) and push**

```bash
git add app/ tests/test_classlabel.py docs/test-evidence/classlabel-red.png docs/test-evidence/classlabel-green.png docs/test-evidence/classlabel-red.html docs/test-evidence/classlabel-green.html README.md
git commit -m "Show short class code on detail page, trimmed class on card (Layer 17)"
git push origin main
```
Expected: `[pre-commit] ok`, full suite passes, commit lands on `main`, push succeeds.

---

## Self-Review

**Spec coverage:**
- `course_short` / `course_trimmed` → Task 2; asserted by the four property tests.
- Detail header pill shows short code → Task 3 Step 1; asserted by `test_detail_header_pill_shows_short_code`.
- Header fallback to connection label → Task 3 Step 1 (`or a.connection.label`); asserted by `test_detail_header_pill_falls_back_to_connection`.
- Connection metacard unchanged → not modified.
- Card shows trimmed → Task 3 Steps 2-3; asserted by `test_card_shows_trimmed_class`.
- Evidence (red live, green) → Task 1 (red) + Task 4 (green); both PNGs committed in Task 4.

**Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected output.

**Type consistency:** `course_short` and `course_trimmed` are `str`, read identically in templates and asserted as `str` in tests. The header-pill assertions key on the exact rendered markup `course-pill course-pill--lg">…<`.

**Coupling to verify during execution:** The existing Layer 15 test `test_card_shows_course_code` seeds `course_code="BIO 101"` and asserts `"BIO 101" in resp.text`; `course_trimmed("BIO 101")` is `"BIO 101"`, so switching the card to `course_trimmed` keeps that test green. The `test_card_omits_class_when_no_code` test seeds `""`; the `{% if a.course_code %}` guard is unchanged, so the line is still omitted. The detail-page tests assert on the header pill markup specifically, so the unchanged "Connection" metacard (which still shows the label) does not cause false matches.