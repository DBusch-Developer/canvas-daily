# Quiz Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect quiz assignments and label them clearly on the detail page, the dashboard, and the daily email.

**Architecture:** One computed property `Assignment.is_quiz` (`"online_quiz" in submission_types`) is the single source of truth; templates and the email all read it. No new Canvas fetch, no DB migration — `submission_types` is already stored.

**Tech Stack:** SQLModel model property, Jinja2 templates, plain-text email in `app/mailer.py`, pytest (FastAPI TestClient + in-memory SQLite, as in the existing htmx layer).

## Global Constraints

- TDD-first, red before green. New behavior is its own layer `tests/test_quiz.py` (label `quiz`).
- Test evidence: `docs/test-evidence/quiz-red.png` and `quiz-green.png`, both referenced in README as **Layer 14**. Red captured live before any implementation. Both PNGs committed together in the final green commit (matches the existing layer pattern).
- No new Canvas call; detection reads stored `submission_types`.
- Quizzes only — not discussions or other submission types.
- One code path for one connection and for many — `is_quiz` is per-assignment, no special-casing.
- Commit with the pre-commit hook (evidence check + full suite). Short commit messages. Commit on `main`, no branches.
- Run pytest via the venv: `.venv/Scripts/python.exe -m pytest ...`.

---

## File Structure

- `tests/test_quiz.py` — **create.** Layer 14 tests: the `is_quiz` property, detail page (pill + message), dashboard tag, email marker.
- `app/models.py` — **modify.** Add the `is_quiz` property to `Assignment`.
- `app/templates/detail.html` — **modify.** Quiz pill in `brief__tags`; quiz message in the Instructions panel.
- `app/templates/report.html` — **modify.** Quiz tag on cards (active board + completed section).
- `app/mailer.py` — **modify.** `(Quiz)` marker on quiz lines.
- `app/static/app.css` — **modify.** `.tag` / `.tag--quiz` styles.
- `README.md` — **modify.** Add Layer 14 to the Test evidence list.

---

## Task 1: Write the failing Layer 14 tests and capture RED

**Files:**
- Create: `tests/test_quiz.py`
- Capture: `docs/test-evidence/quiz-red.png`
- Modify: `README.md` (add Layer 14 section with the red image)

**Interfaces:**
- Consumes (does not exist yet): `Assignment.is_quiz -> bool`; the Quiz pill/tag markup and quiz Instructions message; the `(Quiz)` email marker.
- Produces: the enforced `quiz` layer.

- [ ] **Step 1: Write `tests/test_quiz.py`**

```python
"""Layer 14 - quiz indicator.

Canvas marks quiz assignments with "online_quiz" in submission_types (already
fetched and stored). `Assignment.is_quiz` reads that flag, and every surface -
detail page, dashboard, daily email - labels quizzes from it. Quizzes usually
carry no assignment description, so the detail page shows a quiz-specific message
instead of the generic empty state. Groq/SMTP are not involved here; the web
tests use the FastAPI TestClient against in-memory SQLite, like the htmx layer.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.mailer import build_report_email
from app.models import Assignment, Connection, User
from app.web import create_app, get_session


# ---- is_quiz property (pure) ----

def test_is_quiz_true_for_online_quiz():
    a = Assignment(connection_id=1, canvas_assignment_id=1, name="Pop quiz",
                   submission_types=["online_quiz"])
    assert a.is_quiz is True


def test_is_quiz_false_for_non_quiz_and_empty():
    upload = Assignment(connection_id=1, canvas_assignment_id=2, name="Essay",
                        submission_types=["online_upload"])
    none = Assignment(connection_id=1, canvas_assignment_id=3, name="Reading",
                      submission_types=[])
    assert upload.is_quiz is False
    assert none.is_quiz is False


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


def seed_assignment(client, engine, *, submission_types, description="",
                    email="quiz@x.com", name="Midterm"):
    """Sign up (session cookie) and seed one owned assignment due tomorrow."""
    client.post("/signup", data={"email": email, "password": "hunter2pw"},
                follow_redirects=False)
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=user.id, label="Yavapai", base_url="https://school.test",
                          account_type="student", access_token="canvas-tok")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        a = Assignment(connection_id=conn.id, canvas_assignment_id=1, name=name,
                       description=description,
                       due_at=datetime(2026, 6, 25, 12, 0), points_possible=20.0,
                       submission_types=submission_types,
                       html_url="https://school.test/a/1", workflow_state="unsubmitted")
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


# ---- detail page ----

def test_quiz_detail_shows_pill_and_message(client, engine):
    aid = seed_assignment(client, engine, submission_types=["online_quiz"], description="")
    resp = client.get(f"/assignments/{aid}")
    assert resp.status_code == 200
    assert "Quiz" in resp.text
    assert "This is a quiz" in resp.text
    assert "open it in Canvas to take it" in resp.text
    # The generic empty state is replaced for quizzes.
    assert "No instructions provided" not in resp.text


def test_nonquiz_detail_has_no_quiz_markers(client, engine):
    aid = seed_assignment(client, engine, submission_types=["online_upload"], description="")
    resp = client.get(f"/assignments/{aid}")
    assert resp.status_code == 200
    assert "This is a quiz" not in resp.text
    assert "No instructions provided" in resp.text


def test_quiz_with_description_shows_description_not_message(client, engine):
    aid = seed_assignment(client, engine, submission_types=["online_quiz"],
                          description="<p>Covers chapters 1-3.</p>")
    resp = client.get(f"/assignments/{aid}")
    assert resp.status_code == 200
    assert "Covers chapters 1-3." in resp.text
    assert "This is a quiz" not in resp.text
    # Pill still shows even when a description exists.
    assert "Quiz" in resp.text


# ---- dashboard ----

def test_dashboard_card_tags_quiz(client, engine):
    seed_assignment(client, engine, submission_types=["online_quiz"], description="")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "tag--quiz" in resp.text


def test_dashboard_card_no_tag_for_nonquiz(client, engine):
    seed_assignment(client, engine, submission_types=["online_upload"], description="")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "tag--quiz" not in resp.text


# ---- email ----

def test_email_marks_quiz_line(engine):
    with Session(engine) as s:
        user = User(email="e@x.com", password_hash="x")
        s.add(user)
        s.commit()
        s.refresh(user)
        conn = Connection(user_id=user.id, label="Yavapai", base_url="https://school.test",
                          account_type="student", access_token="t")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        now = datetime(2026, 6, 24, 8, 0)
        s.add(Assignment(connection_id=conn.id, canvas_assignment_id=1, name="Quiz 1",
                         submission_types=["online_quiz"],
                         due_at=now + timedelta(days=1), workflow_state="unsubmitted"))
        s.add(Assignment(connection_id=conn.id, canvas_assignment_id=2, name="Essay 1",
                         submission_types=["online_upload"],
                         due_at=now + timedelta(days=1), workflow_state="unsubmitted"))
        s.commit()
        subject, body = build_report_email(s, user, now)

    assert "Quiz 1 (Quiz) — due" in body
    assert "Essay 1 — due" in body
    assert "Essay 1 (Quiz)" not in body
```

- [ ] **Step 2: Run the layer to confirm it is RED**

Run: `.venv/Scripts/python.exe tools/run_to_html.py quiz-red tests/test_quiz.py`
Expected: harness prints `[RED ...]` and writes `docs/test-evidence/quiz-red.html`. The property tests fail with `AttributeError: ... 'is_quiz'`; the render/email tests fail their assertions.

- [ ] **Step 3: Screenshot the red page**

Serve the folder (file:// is blocked): `.venv/Scripts/python.exe -m http.server 8731 --directory docs/test-evidence` (run in background). Navigate the browser to `http://127.0.0.1:8731/quiz-red.html`, screenshot the `.frame` element to `quiz-red.png`, move it into `docs/test-evidence/`. Stop the server. Verify by eye: red FAILED lines legible, no black-on-black.

- [ ] **Step 4: Add the Layer 14 section to the README (red image now, green link added too)**

Insert after the Layer 13 block in the "Test evidence" list, before the closing "How these are made" paragraph:

```markdown
**Layer 14 — quiz indicator**

Some Canvas assignments are quizzes, and Canvas usually leaves the assignment description blank (the questions live on a separate quiz object), so quizzes showed an empty Instructions panel with nothing to identify them. `Assignment.is_quiz` reads the stored `online_quiz` submission type, and every surface labels quizzes from it: a **Quiz** pill and a quiz-specific message on the detail page, a **Quiz** tag on dashboard cards, and a `(Quiz)` marker in the daily email.

Red — `Assignment.is_quiz` and the quiz markup/markers don't exist yet:

![Quiz indicator tests failing — feature missing](docs/test-evidence/quiz-red.png)

Green — after adding the property and the detail/dashboard/email labels:

![Quiz indicator tests passing](docs/test-evidence/quiz-green.png)
```

(The green PNG is captured in Task 3; the link is added now and the file lands before commit.)

- [ ] **Step 5: Do NOT commit yet.** Red and green PNGs are committed together at the end (Task 4).

---

## Task 2: Detection — `Assignment.is_quiz`

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_quiz.py` (the two property tests)

**Interfaces:**
- Produces: `Assignment.is_quiz -> bool`.

- [ ] **Step 1: Add the property to `Assignment`**

In `app/models.py`, inside `class Assignment`, after the `connection` relationship line, add:

```python
    @property
    def is_quiz(self) -> bool:
        """True when Canvas marks this assignment as a quiz."""
        return "online_quiz" in self.submission_types
```

- [ ] **Step 2: Run the property tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_quiz.py -k is_quiz -v`
Expected: `test_is_quiz_true_for_online_quiz` and `test_is_quiz_false_for_non_quiz_and_empty` PASS.

---

## Task 3: Surfaces — detail page, dashboard, email, styles

**Files:**
- Modify: `app/templates/detail.html`, `app/templates/report.html`, `app/mailer.py`, `app/static/app.css`
- Test: `tests/test_quiz.py` (detail, dashboard, email tests)

**Interfaces:**
- Consumes: `Assignment.is_quiz`.

- [ ] **Step 1: Detail page pill + message (`app/templates/detail.html`)**

Add the Quiz pill in the header tags. Change:

```html
    <div class="brief__tags">
      <span class="course-pill course-pill--lg">{{ a.connection.label }}</span>
      <span class="badge badge--{{ tone }}">{{ status }}</span>
    </div>
```
to:
```html
    <div class="brief__tags">
      <span class="course-pill course-pill--lg">{{ a.connection.label }}</span>
      <span class="badge badge--{{ tone }}">{{ status }}</span>
      {% if a.is_quiz %}<span class="tag tag--quiz">Quiz</span>{% endif %}
    </div>
```

Then replace the Instructions empty-state branch. Change:

```html
    {% if a.description and a.description | trim %}
      {# Description was sanitized on fetch (nh3), so it is safe to render. #}
      <div class="prose">{{ a.description | safe }}</div>
    {% else %}
      <div class="empty">
        <p class="empty__title">No instructions provided</p>
        <p class="empty__sub">Your instructor didn't add details in Canvas. Open it in Canvas for the latest.</p>
      </div>
    {% endif %}
```
to:
```html
    {% if a.description and a.description | trim %}
      {# Description was sanitized on fetch (nh3), so it is safe to render. #}
      <div class="prose">{{ a.description | safe }}</div>
    {% elif a.is_quiz %}
      <div class="empty">
        <p class="empty__title">This is a quiz</p>
        <p class="empty__sub">The questions aren't shown here — open it in Canvas to take it.</p>
      </div>
    {% else %}
      <div class="empty">
        <p class="empty__title">No instructions provided</p>
        <p class="empty__sub">Your instructor didn't add details in Canvas. Open it in Canvas for the latest.</p>
      </div>
    {% endif %}
```

- [ ] **Step 2: Dashboard tag (`app/templates/report.html`)**

In the active-board card, change:

```html
                <div class="card__top">
                  <span class="badge badge--{{ tone }}">{{ status }}</span>
                  <span class="course-pill">{{ a.connection.label }}</span>
                </div>
```
to:
```html
                <div class="card__top">
                  <span class="badge badge--{{ tone }}">{{ status }}</span>
                  {% if a.is_quiz %}<span class="tag tag--quiz">Quiz</span>{% endif %}
                  <span class="course-pill">{{ a.connection.label }}</span>
                </div>
```

In the completed-section card, change:

```html
            <div class="card__top">
              <span class="badge badge--done">{{ 'Excused' if a.excused else ('Graded' if a.workflow_state == 'graded' else 'Complete') }}</span>
              <span class="course-pill">{{ a.connection.label }}</span>
            </div>
```
to:
```html
            <div class="card__top">
              <span class="badge badge--done">{{ 'Excused' if a.excused else ('Graded' if a.workflow_state == 'graded' else 'Complete') }}</span>
              {% if a.is_quiz %}<span class="tag tag--quiz">Quiz</span>{% endif %}
              <span class="course-pill">{{ a.connection.label }}</span>
            </div>
```

- [ ] **Step 3: Email marker (`app/mailer.py`)**

In `build_report_email`, change:

```python
        for assignment in items:
            label = assignment.connection.label
            lines.append(f"  - [{label}] {assignment.name} — due {assignment.due_at}")
```
to:
```python
        for assignment in items:
            label = assignment.connection.label
            quiz = " (Quiz)" if assignment.is_quiz else ""
            lines.append(f"  - [{label}] {assignment.name}{quiz} — due {assignment.due_at}")
```

- [ ] **Step 4: Quiz tag styles (`app/static/app.css`)**

After the `.course-pill--lg` line (around line 335), add:

```css
.tag {
  font: 600 .72rem/1 var(--sans);
  border-radius: 999px;
  padding: .3rem .55rem;
  letter-spacing: .02em;
}
.tag--quiz {
  color: var(--coral);
  background: rgba(239, 107, 52, .12);
  border: 1px solid rgba(239, 107, 52, .35);
}
```

(If `--coral` is not defined in `:root`, substitute the nearest accent token — check the top of `app.css`.)

- [ ] **Step 5: Run the full quiz layer**

Run: `.venv/Scripts/python.exe -m pytest tests/test_quiz.py -v`
Expected: all tests PASS (property + detail + dashboard + email).

---

## Task 4: Capture GREEN, verify, commit

**Files:**
- Capture: `docs/test-evidence/quiz-green.png`

- [ ] **Step 1: Render the green page**

Run: `.venv/Scripts/python.exe tools/run_to_html.py quiz-green tests/test_quiz.py`
Expected: `[GREEN (all passed)]` and `docs/test-evidence/quiz-green.html` written.

- [ ] **Step 2: Screenshot the green page**

Serve: `.venv/Scripts/python.exe -m http.server 8731 --directory docs/test-evidence` (background). Navigate to `http://127.0.0.1:8731/quiz-green.html`, screenshot the `.frame` element to `quiz-green.png`, move into `docs/test-evidence/`. Stop the server. Verify by eye: all green PASSED, every line legible. (README already links `quiz-green.png` from Task 1.)

- [ ] **Step 3: Run the full suite and evidence check**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass (existing layers + the new `quiz` layer).
Run: `.venv/Scripts/python.exe tools/check_evidence.py`
Expected: `OK - evidence present and documented for: ... quiz ...`.

- [ ] **Step 4: Commit (full pre-commit hook) and push**

```bash
git add app/ tests/test_quiz.py docs/test-evidence/quiz-red.png docs/test-evidence/quiz-green.png docs/test-evidence/quiz-red.html docs/test-evidence/quiz-green.html README.md
git commit -m "Label quizzes on detail page, dashboard, and email (Layer 14)"
git push origin main
```
Expected: `[pre-commit] ok`, full suite passes, commit lands on `main`, push succeeds.

---

## Self-Review

**Spec coverage:**
- Detection (`is_quiz`) → Task 2; asserted by the two property tests.
- Detail page pill + message → Task 3 Step 1; asserted by `test_quiz_detail_shows_pill_and_message`, `test_quiz_with_description_shows_description_not_message`, `test_nonquiz_detail_has_no_quiz_markers`.
- Dashboard tag → Task 3 Step 2; asserted by `test_dashboard_card_tags_quiz`, `test_dashboard_card_no_tag_for_nonquiz`.
- Email marker → Task 3 Step 3; asserted by `test_email_marks_quiz_line`.
- Styles → Task 3 Step 4 (visual; not under pytest, but `tag--quiz` presence is asserted via the dashboard test).
- Evidence (red live, green) → Task 1 (red) + Task 4 (green); both PNGs committed in Task 4.

**Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected output.

**Type consistency:** `is_quiz` returns `bool` and is read identically in `detail.html`, `report.html`, and `mailer.py`. The dashboard test keys on the class name `tag--quiz`, which is exactly the class emitted by the templates and styled in CSS.

**Coupling to verify during execution:** The dashboard tests rely on the seeded assignment landing in a visible bucket — `due_at` is set in the future and the item is unsubmitted/not-missing, so `report_for_user` places it on the active board. The CSS uses `var(--coral)` and `var(--sans)`; both are used elsewhere in `app.css`. If either is absent, substitute the nearest existing token (noted in Task 3 Step 4).
