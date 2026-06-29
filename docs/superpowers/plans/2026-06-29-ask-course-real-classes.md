# Ask My Course — Real Classes + Per-Account Selector — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `/ask` picker show only real classes (AI-sorted, user-overridable) and disambiguate courses across accounts with a dropdown — without touching the daily email.

**Architecture:** A new `Course.hidden` three-valued flag (NULL=undecided / False=shown / True=hidden) and a `Course.has_assignments` boolean set at sync time. The AI classifier (`classify_courses`) sorts undecided courses on picker load (one batched Groq call, mocked in tests, show-all fallback on failure). The picker groups by a selected account and offers a "Hidden courses" disclosure with Show buttons. Prod schema changes ship via an idempotent `tools/migrate_*` script.

**Tech Stack:** FastAPI, SQLModel over Neon Postgres, Jinja2, Groq (Llama 3.3 70B) via OpenAI-compatible API mocked at the httpx transport boundary, pytest.

## Global Constraints

- **TDD-first**: failing test before code, every layer. Red captured live before implementation.
- **Two new enforced layers** = two new `tests/test_<label>.py` files, each with its own numbered README "Layer N" section and live `-red.png`/`-green.png` in `docs/test-evidence/`. Do not fold into existing layers.
- **Groq key** from `GROQ_API_KEY` env only; never logged, never in a commit. Mock Groq at the httpx transport boundary.
- **Handle AI failure cleanly**: timeout or any error during classification → show-all fallback, never a broken/empty page; affected courses stay NULL.
- **Base URL lives on the connection.** One code path for one connection and for many.
- **Detail/picker reads from storage**, no live Canvas call on picker load.
- **Scope is `/ask` only.** Do not change the daily report/email.
- **Commit on main**, short one-line messages. Pre-commit hook runs `check_evidence` + full suite; run commits in the foreground with no other pytest running.
- Run pytest via the venv: `.venv\Scripts\python.exe -m pytest`.

---

### Task 1: Add `hidden` and `has_assignments` to the Course model

**Files:**
- Modify: `app/models.py` (the `Course` class, ~line 126-139)
- Test: `tests/test_models.py` (existing models layer — this is a genuine extension of the model layer)

**Interfaces:**
- Produces: `Course.hidden: bool | None` (default `None`), `Course.has_assignments: bool` (default `False`).

- [ ] **Step 1: Write the failing test** — append to `tests/test_models.py`:

```python
def test_course_visibility_fields_default():
    from app.models import Course
    c = Course(connection_id=1, canvas_course_id=99, name="X")
    assert c.hidden is None          # undecided until classified
    assert c.has_assignments is False
```

- [ ] **Step 2: Run it, verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_models.py::test_course_visibility_fields_default -v`
Expected: FAIL — `AttributeError`/`TypeError` (fields don't exist yet).

- [ ] **Step 3: Implement** — in `app/models.py`, add the two fields to `Course` (after `last_content_synced_at`):

```python
    last_content_synced_at: datetime | None = None
    # Ask My Course visibility: NULL = not yet classified, False = shown
    # (real class), True = hidden (Canvas extra). Set by the AI classifier or
    # the user's Show action; user choices stick across re-syncs.
    hidden: bool | None = None
    # Whether the course had any assignments at last sync — a signal the AI
    # classifier uses alongside the name.
    has_assignments: bool = False
```

- [ ] **Step 4: Run it, verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_models.py::test_course_visibility_fields_default -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "Add hidden and has_assignments fields to Course"
```

---

### Task 2: Record `has_assignments` during sync

**Files:**
- Modify: `app/sync.py` — `sync_connection` (~line 25-37) and `_upsert_course` (~line 70-83)
- Test: `tests/test_sync.py` (existing sync layer — genuine extension: sync already fetches assignments per course)

**Interfaces:**
- Consumes: `Course.has_assignments` from Task 1.
- Produces: after `sync_connection`, each `Course` row has `has_assignments = (that course had ≥1 assignment)`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_sync.py`. Follow the file's existing mocked-Canvas pattern; this asserts a course with assignments is flagged and one without is not. Use the existing helpers in that file for the engine and the mock client; the key assertion:

```python
def test_sync_records_has_assignments_per_course():
    # Two courses: one returns an assignment, the other returns none.
    # (Build `client`, `session`, `connection` with this file's existing helpers /
    #  mock transport; the course list returns ids 1 and 2, assignments only for 1.)
    sync_connection(session, connection, client)
    courses = session.exec(select(Course).where(Course.connection_id == connection.id)).all()
    by_cid = {c.canvas_course_id: c for c in courses}
    assert by_cid[1].has_assignments is True
    assert by_cid[2].has_assignments is False
```

> Implementer note: mirror the mock-Canvas setup already used by other tests in `tests/test_sync.py` (course list endpoint + per-course assignments endpoint). If that file lacks a reusable mock, copy the `MockTransport` handler pattern from `tests/test_connsync.py`.

- [ ] **Step 2: Run it, verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_sync.py::test_sync_records_has_assignments_per_course -v`
Expected: FAIL — `has_assignments` stays default `False` for course 1.

- [ ] **Step 3: Implement** — update `_upsert_course` to accept and set the flag, and pass it from `sync_connection`:

```python
def sync_connection(session, connection, client):
    """Fetch and store every assignment across one connection's courses."""
    for course in fetch_courses(connection.base_url, connection.access_token, client):
        parsed_list = fetch_assignments(
            connection.base_url, connection.access_token, course["id"], client
        )
        _upsert_course(session, connection.id, course, has_assignments=bool(parsed_list))
        for parsed in parsed_list:
            _upsert(session, connection.id, parsed,
                    course.get("code") or "", course.get("time_zone") or "")
    connection.last_synced_at = _now()
    session.add(connection)
    session.flush()


def _upsert_course(session, connection_id, course, has_assignments=False):
    """Insert or update a Course row keyed on (connection_id, canvas_course_id)."""
    existing = session.exec(
        select(Course).where(
            Course.connection_id == connection_id,
            Course.canvas_course_id == course["id"],
        )
    ).first()
    target = existing or Course(
        connection_id=connection_id,
        canvas_course_id=course["id"],
    )
    target.name = course["name"]
    target.has_assignments = has_assignments
    session.add(target)
```

- [ ] **Step 4: Run it, verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_sync.py -v`
Expected: PASS (new test + existing sync tests still green).

- [ ] **Step 5: Commit**

```bash
git add app/sync.py tests/test_sync.py
git commit -m "Record has_assignments per course during sync"
```

---

### Task 3 (LAYER): AI course classifier — `tests/test_courseclassify.py`

This is a **new enforced layer**. Capture red live before implementing.

**Files:**
- Create: `tests/test_courseclassify.py`
- Modify: `app/ai.py` (add `CLASSIFY_SYSTEM_PROMPT`, `classify_courses`)
- Evidence: `docs/test-evidence/courseclassify-red.png`, `docs/test-evidence/courseclassify-green.png`
- Docs: README "Test evidence" — new "Layer 38 — AI course classifier" section

**Interfaces:**
- Consumes: `app.ai._request_completion`, `AIError`, `AITimeoutError` (existing).
- Produces: `classify_courses(courses, client, api_key) -> list[bool]` where `courses` is a list of `{"name": str, "has_assignments": bool}` and the return is a parallel list of `is_real` booleans (True = real class). Raises `AIError`/`AITimeoutError` on failure or malformed response.

- [ ] **Step 1: Write the failing tests** — `tests/test_courseclassify.py`:

```python
"""Layer 38 — AI course classifier.

Groq is mocked at the httpx transport boundary. Given each course's name and
whether it has assignments, the model returns a parallel list of booleans
(True = a real class, False = a Canvas extra). Failures and malformed responses
raise a clean AIError so the caller can fall back to showing everything. The API
key rides in the Authorization header and is never logged.
"""
import json
import httpx
import pytest

from app.ai import classify_courses, AIError, AITimeoutError


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _responder(bools):
    def handler(request):
        # never leak the key into assertions; just return the canned classification
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(bools)}}]
        })
    return handler


def test_classify_returns_parallel_booleans():
    courses = [
        {"name": "CSA250 Intro Artificial Intelligence (22255)", "has_assignments": True},
        {"name": "Lunch Brunch", "has_assignments": False},
        {"name": "English: Language Arts Companion", "has_assignments": True},
    ]
    out = classify_courses(courses, _client(_responder([True, False, True])), "k")
    assert out == [True, False, True]


def test_classify_empty_list_makes_no_call():
    def boom(request):  # must not be called
        raise AssertionError("no Groq call for an empty list")
    assert classify_courses([], _client(boom), "k") == []


def test_classify_timeout_raises_clean_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)
    with pytest.raises(AITimeoutError):
        classify_courses([{"name": "X", "has_assignments": True}], _client(handler), "k")


def test_classify_wrong_length_raises_aierror():
    courses = [{"name": "A", "has_assignments": True},
               {"name": "B", "has_assignments": False}]
    # model returns one bool for two courses → malformed
    with pytest.raises(AIError):
        classify_courses(courses, _client(_responder([True])), "k")


def test_classify_non_bool_payload_raises_aierror():
    with pytest.raises(AIError):
        classify_courses([{"name": "A", "has_assignments": True}],
                         _client(_responder(["yes"])), "k")
```

- [ ] **Step 2: Capture RED live (before implementation)**

```bash
.venv\Scripts\python.exe tools/run_to_html.py courseclassify-red tests/test_courseclassify.py
```
Confirm it reports RED (ImportError: cannot import name 'classify_courses'). Serve and screenshot:
```bash
python -m http.server 8731 --directory docs/test-evidence
```
Navigate to `http://127.0.0.1:8731/courseclassify-red.html`, screenshot the `.frame` element to `docs/test-evidence/courseclassify-red.png`. Stop the server.

- [ ] **Step 3: Implement `classify_courses`** in `app/ai.py` (after `generate_bullets`):

```python
CLASSIFY_SYSTEM_PROMPT = (
    "You decide which Canvas courses are real academic classes a student takes "
    "for a grade, versus extras Canvas exposes that are not real classes — clubs, "
    "honor societies, help desks, lunch/social spaces, orientations, and parent or "
    "student centers.\n"
    "You are given a numbered list of courses, each with its name and whether it "
    "currently has any graded assignments. Judge primarily from the name; the "
    "assignment signal is secondary.\n"
    "Respond with a single JSON array of booleans and nothing else — exactly one "
    "entry per course, in the same order: true if it is a real class, false if it "
    "is an extra."
)


def build_classify_messages(courses):
    """A numbered course list (name + assignment signal) for classification."""
    lines = []
    for i, c in enumerate(courses):
        has = "yes" if c.get("has_assignments") else "no"
        lines.append(f"{i}. {c.get('name', '')} (has assignments: {has})")
    return [
        {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


def classify_courses(courses, client, api_key):
    """Return a parallel list of is_real booleans for each course.

    `courses` is a list of {"name", "has_assignments"}. Raises AIError /
    AITimeoutError on failure or a malformed response so the caller can fall
    back to showing everything.
    """
    if not courses:
        return []
    content = _request_completion(client, api_key, {
        "model": GROQ_MODEL,
        "temperature": TEMPERATURE,
        "response_format": {"type": "json_object"},
        "messages": build_classify_messages(courses),
    })
    try:
        data = json.loads(content)
        # Accept a bare array, or an object wrapping one (json_object mode).
        if isinstance(data, dict):
            for value in data.values():
                if isinstance(value, list):
                    data = value
                    break
        if (not isinstance(data, list) or len(data) != len(courses)
                or not all(isinstance(x, bool) for x in data)):
            raise ValueError("expected a JSON array of booleans, one per course")
    except (ValueError, TypeError) as exc:
        logger.warning("Course classification returned invalid JSON")
        raise AIError("Course classification could not be completed.") from exc
    return data
```

> Note: `response_format=json_object` makes Groq wrap output in an object. The parser unwraps the first list value, so both a bare array and `{"results": [...]}` work. The test mock returns a bare array string, which `json.loads` yields directly.

- [ ] **Step 4: Capture GREEN**

```bash
.venv\Scripts\python.exe tools/run_to_html.py courseclassify-green tests/test_courseclassify.py
```
Confirm GREEN. Serve, navigate to `courseclassify-green.html`, screenshot `.frame` to `docs/test-evidence/courseclassify-green.png`. Verify both images by eye (red shows red FAILED, green shows passed, all legible).

- [ ] **Step 5: Add README "Layer 38" section** under "Test evidence", in the same format as prior layers: a short TDD description, then the red image, then the green image:

```markdown
### Layer 38 — AI course classifier

The Ask My Course picker sorts real classes from Canvas extras (clubs, help
desks, parent centers). `classify_courses` sends each course's name and
whether it has assignments to Groq and gets back one boolean per course;
timeouts and malformed responses raise a clean `AIError` so the picker can
fall back to showing everything. Groq is mocked at the transport boundary.

![Layer 38 red](docs/test-evidence/courseclassify-red.png)
![Layer 38 green](docs/test-evidence/courseclassify-green.png)
```

- [ ] **Step 6: Commit** (stop the HTTP server first)

```bash
git add app/ai.py tests/test_courseclassify.py docs/test-evidence/courseclassify-red.png docs/test-evidence/courseclassify-green.png README.md
git commit -m "Layer 38: AI course classifier for Ask My Course"
```

---

### Task 4: Migration — add the two columns to the live `courses` table

**Files:**
- Create: `tools/migrate_add_course_visibility.py`

**Interfaces:**
- Consumes: `app.db.make_engine`, `DATABASE_URL`.
- Produces: `courses.hidden` (BOOLEAN, nullable) and `courses.has_assignments` (BOOLEAN DEFAULT FALSE) on the target DB. Idempotent.

- [ ] **Step 1: Create the migration script** (mirrors `tools/migrate_add_sync_status.py`):

```python
"""Add courses.hidden and courses.has_assignments to whatever DATABASE_URL points at.

    python tools/migrate_add_course_visibility.py

One-time migration for the live Neon branch. Idempotent (ADD COLUMN IF NOT
EXISTS) and safe to re-run. Prints the target host (no credentials). Fresh
databases get the columns via tools/init_db.py.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import text  # noqa: E402

from app.db import make_engine  # noqa: E402


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set. Refusing to run.")
        raise SystemExit(2)

    parsed = urlparse(url)
    where = parsed.hostname or url.split(":", 1)[0]
    print(f"About to alter courses table in: {where}")

    if url.startswith("sqlite"):
        print("This looks like a local SQLite file, not your Neon branch.")
        print("Point DATABASE_URL at Neon first, then re-run.")
        raise SystemExit(1)

    engine = make_engine(url)
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE courses ADD COLUMN IF NOT EXISTS hidden BOOLEAN"
        ))
        conn.execute(text(
            "ALTER TABLE courses ADD COLUMN IF NOT EXISTS has_assignments BOOLEAN DEFAULT FALSE"
        ))
    print("Columns hidden and has_assignments added (or already present). Done.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it imports cleanly** (do NOT run against prod yet):

Run: `.venv\Scripts\python.exe -c "import ast; ast.parse(open('tools/migrate_add_course_visibility.py').read()); print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add tools/migrate_add_course_visibility.py
git commit -m "Migration: add courses.hidden and courses.has_assignments"
```

> The migration is **run against Neon by Diana** (or with her go-ahead) after the
> feature merges — `python tools/migrate_add_course_visibility.py` with
> `DATABASE_URL` pointed at Neon. Local SQLite tests get the columns from the model
> via `create_all`, so the suite does not need the migration.

---

### Task 5 (LAYER): Picker — account dropdown, hidden section, classify-on-load, Show route — `tests/test_askpicker.py`

This is a **new enforced layer**. Capture red live before implementing.

**Files:**
- Create: `tests/test_askpicker.py`
- Modify: `app/web.py` — replace `ask_picker` (~line 424-436); add `course_show` route; import `classify_courses`.
- Modify: `app/templates/course_picker.html` (rework)
- Modify: `app/static/app.css` (small additions for the picker/dropdown/disclosure)
- Evidence: `docs/test-evidence/askpicker-red.png`, `docs/test-evidence/askpicker-green.png`
- Docs: README "Layer 39 — Ask My Course picker" section

**Interfaces:**
- Consumes: `classify_courses` (Task 3); `Course.hidden`, `Course.has_assignments` (Task 1); existing `_current_user`, `_ask_course_enabled`, `_owned_course_or_404`, `get_session`, `get_groq_client`, `get_api_key`.
- Produces: `GET /ask?account={connection_id}` and `POST /courses/{course_id}/show`.

- [ ] **Step 1: Write the failing tests** — `tests/test_askpicker.py`. Reuse the in-memory SQLite + dependency-override harness from `tests/test_connsync.py` (`_make_sqlite_engine`, `_make_app`, `_signup`, `_seed_connection`), overriding `get_groq_client` and `get_api_key` so no real Groq call is made. Tests:

```python
"""Layer 39 — Ask My Course picker: account selector + real-class filtering.

In-memory SQLite + StaticPool, Groq mocked at the transport boundary. The picker
classifies undecided courses on load (show-all on failure), groups by a selected
account, and offers a Hidden courses disclosure with a Show action.
"""
import json
import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Connection, Course, User
from app.web import (create_app, get_session, get_engine, get_groq_client,
                     get_api_key)
from app.ai import get_api_key as _real_get_api_key  # noqa: F401
from fastapi.testclient import TestClient

# ASK_COURSE must be enabled for these routes.
import os
os.environ["ASK_COURSE_ENABLED"] = "1"


def _engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


def _classify_handler(bools):
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(bools)}}]
        })
    return handler


def _app(engine, classify=None):
    application = create_app()

    def _get_session():
        with Session(engine) as s:
            yield s
    application.dependency_overrides[get_session] = _get_session
    application.dependency_overrides[get_engine] = lambda: engine

    def _groq():
        handler = classify or _classify_handler([])
        return httpx.Client(transport=httpx.MockTransport(handler))
    application.dependency_overrides[get_groq_client] = _groq
    application.dependency_overrides[get_api_key] = lambda: "k"
    return application


def _signup(client, email="a@test.com"):
    return client.post("/signup", data={"email": email, "password": "hunter2pw"},
                       follow_redirects=False)


def _seed(engine, email):
    with Session(engine) as s:
        u = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=u.id, label="Mine", base_url="https://school.test",
                          account_type="student", access_token="tok")
        s.add(conn); s.commit(); s.refresh(conn)
        return conn.id


def _add_course(engine, conn_id, cid, name, hidden=None, has=True):
    with Session(engine) as s:
        s.add(Course(connection_id=conn_id, canvas_course_id=cid, name=name,
                     hidden=hidden, has_assignments=has))
        s.commit()


def test_picker_classifies_undecided_on_load_and_splits_lists():
    eng = _engine()
    client = TestClient(_app(eng, classify=_classify_handler([True, False])))
    _signup(client)
    conn_id = _seed(eng, "a@test.com")
    _add_course(eng, conn_id, 1, "CSA250 Intro AI (22255)")     # -> real
    _add_course(eng, conn_id, 2, "Lunch Brunch")                # -> hidden
    resp = client.get(f"/ask?account={conn_id}")
    assert resp.status_code == 200
    body = resp.text
    assert "CSA250 Intro AI (22255)" in body
    # Lunch Brunch must be under the hidden disclosure, not the main list.
    head, _, tail = body.partition("Hidden courses")
    assert "Lunch Brunch" not in head
    assert "Lunch Brunch" in tail
    # And the classification persisted.
    with Session(eng) as s:
        by = {c.canvas_course_id: c for c in s.exec(select(Course)).all()}
        assert by[1].hidden is False and by[2].hidden is True


def test_picker_shows_account_dropdown_with_each_connection():
    eng = _engine()
    client = TestClient(_app(eng))
    _signup(client)
    a = _seed(eng, "a@test.com")
    with Session(eng) as s:
        u = s.exec(select(User).where(User.email == "a@test.com")).one()
        c2 = Connection(user_id=u.id, label="Marley", base_url="https://k12.test",
                        account_type="observer", access_token="tok")
        s.add(c2); s.commit(); s.refresh(c2); b = c2.id
    _add_course(eng, a, 1, "CSA250 (22255)", hidden=False)
    resp = client.get("/ask")
    assert "Mine" in resp.text and "Marley" in resp.text  # both accounts in dropdown


def test_picker_only_shows_selected_account_courses():
    eng = _engine()
    client = TestClient(_app(eng))
    _signup(client)
    a = _seed(eng, "a@test.com")
    with Session(eng) as s:
        u = s.exec(select(User).where(User.email == "a@test.com")).one()
        c2 = Connection(user_id=u.id, label="Other", base_url="https://k12.test",
                        account_type="observer", access_token="tok")
        s.add(c2); s.commit(); s.refresh(c2); b = c2.id
    _add_course(eng, a, 1, "CSA250 (22255)", hidden=False)
    _add_course(eng, b, 2, "BIO181 (22133)", hidden=False)
    resp = client.get(f"/ask?account={a}")
    assert "CSA250 (22255)" in resp.text
    assert "BIO181 (22133)" not in resp.text


def test_classify_failure_shows_all():
    def boom(request):
        raise httpx.ReadTimeout("slow", request=request)
    eng = _engine()
    client = TestClient(_app(eng, classify=boom))
    _signup(client)
    conn_id = _seed(eng, "a@test.com")
    _add_course(eng, conn_id, 1, "Lunch Brunch", hidden=None)
    resp = client.get(f"/ask?account={conn_id}")
    assert resp.status_code == 200
    assert "Lunch Brunch" in resp.text            # shown, not hidden
    with Session(eng) as s:
        assert s.exec(select(Course)).one().hidden is None  # stays undecided


def test_show_unhides_and_is_ownership_guarded():
    eng = _engine()
    client = TestClient(_app(eng))
    _signup(client, "owner@test.com")
    conn_id = _seed(eng, "owner@test.com")
    _add_course(eng, conn_id, 1, "Lunch Brunch", hidden=True)
    with Session(eng) as s:
        course_id = s.exec(select(Course)).one().id

    # Owner can show it.
    resp = client.post(f"/courses/{course_id}/show", follow_redirects=False)
    assert resp.status_code == 303
    with Session(eng) as s:
        assert s.get(Course, course_id).hidden is False

    # A different user cannot.
    client.cookies.clear()
    _signup(client, "intruder@test.com")
    resp = client.post(f"/courses/{course_id}/show", follow_redirects=False)
    assert resp.status_code == 404
```

- [ ] **Step 2: Capture RED live (before implementation)**

```bash
.venv\Scripts\python.exe tools/run_to_html.py askpicker-red tests/test_askpicker.py
```
Confirm RED (the new routes/behavior don't exist; old `/ask` ignores `account`, no hidden disclosure, no `/show`). Serve and screenshot `.frame` of `askpicker-red.html` → `docs/test-evidence/askpicker-red.png`. Stop the server.

- [ ] **Step 3: Implement the route + Show action** in `app/web.py`. Add the import near the top (line 21 area):

```python
from app.ai import AIError, AITimeoutError, classify_courses, generate_bullets
from app.ai import get_api_key
```

Replace the existing `ask_picker` (lines ~424-436) with:

```python
    @app.get("/ask")
    def ask_picker(request: Request, account: int | None = None,
                   session: Session = Depends(get_session),
                   client: httpx.Client = Depends(get_groq_client),
                   api_key: str = Depends(get_api_key)):
        if not _ask_course_enabled():
            raise HTTPException(status_code=404)
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)

        connections = session.exec(
            select(Connection).where(Connection.user_id == user.id)
            .order_by(Connection.id)
        ).all()

        all_courses = session.exec(
            select(Course).join(Connection, Course.connection_id == Connection.id)
            .where(Connection.user_id == user.id)
        ).all()

        # Classify any undecided courses once; show-all on AI failure (stay NULL).
        undecided = [c for c in all_courses if c.hidden is None]
        if undecided:
            try:
                verdicts = classify_courses(
                    [{"name": c.name, "has_assignments": c.has_assignments}
                     for c in undecided],
                    client, api_key,
                )
                for course, is_real in zip(undecided, verdicts):
                    course.hidden = not is_real
                    session.add(course)
                session.commit()
            except AIError:
                pass  # leave undecided NULL → treated as shown below

        # Selected account: requested (if owned) else the first connection.
        owned_ids = {c.id for c in connections}
        selected_id = account if account in owned_ids else (
            connections[0].id if connections else None)

        account_courses = [c for c in all_courses if c.connection_id == selected_id]
        shown = [c for c in account_courses if not c.hidden]      # NULL or False
        hidden = [c for c in account_courses if c.hidden]         # True

        return TEMPLATES.TemplateResponse(request, "course_picker.html", {
            "connections": connections,
            "selected_id": selected_id,
            "shown": shown,
            "hidden": hidden,
        })

    @app.post("/courses/{course_id}/show")
    def course_show(request: Request, course_id: int,
                    session: Session = Depends(get_session)):
        if not _ask_course_enabled():
            raise HTTPException(status_code=404)
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        course = _owned_course_or_404(session, course_id, user)
        course.hidden = False
        session.add(course)
        session.commit()
        return RedirectResponse(f"/ask?account={course.connection_id}",
                                status_code=303)
```

- [ ] **Step 4: Rework the template** `app/templates/course_picker.html`:

```html
{% extends "base.html" %}
{% block body %}
<h1>Ask my course</h1>

{% if not connections %}
  <p>No accounts yet. Add a Canvas connection and run a sync first.</p>
{% else %}
<form class="account-picker" method="get" action="/ask">
  <label for="account">Account</label>
  <select id="account" name="account" onchange="this.form.submit()">
    {% for c in connections %}
      <option value="{{ c.id }}" {% if c.id == selected_id %}selected{% endif %}>
        {{ c.label }} — {{ c.base_url }}
      </option>
    {% endfor %}
  </select>
</form>

{% if shown %}
<ul class="course-list">
  {% for c in shown %}
    <li><a href="/courses/{{ c.id }}/ask">{{ c.name }}</a></li>
  {% endfor %}
</ul>
{% else %}
<p>No classes here yet. Add a Canvas connection and run a sync first.</p>
{% endif %}

{% if hidden %}
<details class="hidden-courses">
  <summary>Hidden courses ({{ hidden|length }})</summary>
  <ul class="course-list course-list--hidden">
    {% for c in hidden %}
      <li>
        <span>{{ c.name }}</span>
        <form method="post" action="/courses/{{ c.id }}/show">
          <button class="btn btn--ghost btn--sm" type="submit">Show</button>
        </form>
      </li>
    {% endfor %}
  </ul>
</details>
{% endif %}
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Add picker CSS** to `app/static/app.css` (near the other component styles):

```css
/* Ask My Course picker: account selector + hidden-courses disclosure. */
.account-picker { display: grid; gap: .35rem; justify-items: start; margin: 0 0 1.2rem; max-width: 28rem; }
.hidden-courses { margin-top: 1.5rem; }
.hidden-courses summary { cursor: pointer; font-weight: 600; color: var(--muted); }
.course-list--hidden li { display: flex; align-items: center; justify-content: space-between; gap: .75rem; }
.course-list--hidden form { margin: 0; }
```

- [ ] **Step 6: Run the layer tests, verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_askpicker.py -v`
Expected: PASS (all five).

- [ ] **Step 7: Capture GREEN**

```bash
.venv\Scripts\python.exe tools/run_to_html.py askpicker-green tests/test_askpicker.py
```
Confirm GREEN. Serve, screenshot `.frame` of `askpicker-green.html` → `docs/test-evidence/askpicker-green.png`. Verify both images by eye.

- [ ] **Step 8: Add README "Layer 39" section**:

```markdown
### Layer 39 — Ask My Course picker: real classes + account selector

The picker classifies undecided courses on load (Layer 38, mocked here),
groups them under an account dropdown, and tucks Canvas extras into a
"Hidden courses" disclosure with a Show button to pull any back. AI failure
falls back to showing everything; the Show action is login- and
ownership-guarded.

![Layer 39 red](docs/test-evidence/askpicker-red.png)
![Layer 39 green](docs/test-evidence/askpicker-green.png)
```

- [ ] **Step 9: Commit** (stop the HTTP server first)

```bash
git add app/web.py app/templates/course_picker.html app/static/app.css tests/test_askpicker.py docs/test-evidence/askpicker-red.png docs/test-evidence/askpicker-green.png README.md
git commit -m "Layer 39: Ask My Course real-class picker with account selector"
```

---

### Task 6: Full-suite verification + evidence check + push

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite alone** (no other pytest running):

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all pass (existing + the new layers).

- [ ] **Step 2: Run the evidence check**

Run: `.venv\Scripts\python.exe tools/check_evidence.py`
Expected: OK — lists `courseclassify` and `askpicker` among documented layers.

- [ ] **Step 3: Push**

```bash
git push
```

- [ ] **Step 4: After deploy, Diana runs the migration against Neon** and confirms the picker live:

```bash
python tools/migrate_add_course_visibility.py   # DATABASE_URL → Neon
```
Verify the deploy landed (public asset marker) and the `/ask` picker shows the dropdown + hidden section.

---

## Self-Review

**Spec coverage:**
- Real-class filtering via AI → Task 3 (classifier) + Task 5 (applied on load). ✓
- Name + has_assignments signal → Task 2 (record at sync) + Task 3 (uses both). ✓
- Three-valued hidden / user override sticks / new courses classified once → Task 1 (field) + Task 5 (only NULL classified; Show sets False). ✓
- AI failure → show-all, stays NULL → Task 5 `test_classify_failure_shows_all`. ✓
- Account dropdown, one account at a time, default first → Task 5 dropdown tests. ✓
- Hidden courses disclosure + Show, no Hide on real classes → Task 5 template + `test_show_*`. ✓
- Daily email untouched → no report/email/mailer files modified anywhere. ✓
- Prod schema change → Task 4 migration. ✓
- Two new TDD layers with live red/green + README → Tasks 3 and 5. ✓

**Placeholder scan:** Task 2's test references this file's existing mock helpers rather than inlining a full Canvas mock — flagged with an implementer note pointing to the concrete pattern in `tests/test_connsync.py`. All other steps contain complete code.

**Type consistency:** `classify_courses(courses, client, api_key) -> list[bool]` defined in Task 3 and consumed with that exact signature in Task 5. `Course.hidden` (None/False/True) and `Course.has_assignments` (bool) consistent across Tasks 1, 2, 5. `_upsert_course(..., has_assignments=False)` defined and called consistently in Task 2.

**Note on layering:** Tasks 1, 2, and 4 are supporting changes (model field, sync extension, migration) that ride with the two genuinely-new behavior layers (Tasks 3 and 5). The `has_assignments` recording is a true extension of the existing `sync` layer, so it adds a test to `tests/test_sync.py` rather than a new layer — consistent with the rule that extensions of a layer's own behavior belong in that layer.
