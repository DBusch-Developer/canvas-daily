# Auto-sync on Connection Add + Settings Accounts List — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adding a Canvas connection immediately pulls its assignments and the settings screen becomes a real accounts list (with add, last-synced/count, and remove).

**Architecture:** A new injectable `get_canvas_client` HTTP dependency lets `POST /connections` call the existing `sync_connection()` synchronously right after saving, mirroring how `get_groq_client` already works. A new `last_synced_at` column on `Connection` is stamped by `sync_connection` on success. `GET /connections` renders a new accounts-list template; `POST /connections/{id}/delete` removes one. The dashboard is untouched — it already aggregates all connections.

**Tech Stack:** FastAPI, SQLModel (Postgres/Neon), Jinja2, httpx (with `MockTransport` in tests), pytest.

## Global Constraints

- TDD-first: write the failing test, run it red **before** any implementation. (Red captured live for evidence — never reenacted.)
- Tokens are encrypted at rest; never store, print, or log a token in plaintext — not in warnings, errors, or logs.
- Base URL lives on the connection, never as global config.
- One code path for one connection and for four — no special-casing single-connection users.
- Detail pages read from storage, not live Canvas.
- Short, one-line git commit messages.
- Solo project: commit straight to `main`, never branch.
- Test runner: `.venv\Scripts\python.exe -m pytest`. E2E and sync tests require `TEST_DATABASE_URL` (a Neon test branch); they skip without it.

---

### Task 1: `last_synced_at` column + sync stamps it on success

**Files:**
- Modify: `app/models.py` (Connection model)
- Modify: `app/sync.py:24-32` (`sync_connection`)
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: `sync_connection(session, connection, client)`, `_now()` (both already in `app/sync.py`).
- Produces: `Connection.last_synced_at: datetime | None`, set to `_now()` after a successful sync pass.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_sync.py`:

```python
def test_sync_sets_last_synced_at(session):
    user = a_user(session, "stamp@x.com")
    conn = a_connection(session, user.id)
    assert conn.last_synced_at is None

    sync_connection(session, conn, client_for(canvas_handler([(10, [assignment_json(1, "A")])])))

    assert conn.last_synced_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_sync.py::test_sync_sets_last_synced_at -v`
Expected: FAIL — `AttributeError: 'Connection' object has no attribute 'last_synced_at'`.

- [ ] **Step 3: Add the column to the model**

In `app/models.py`, inside `class Connection`, add after `created_at`:

```python
    created_at: datetime = Field(default_factory=_utcnow)
    last_synced_at: datetime | None = None
```

- [ ] **Step 4: Stamp it on a successful sync**

In `app/sync.py`, update `sync_connection` so it records the timestamp after the fetch/store loop:

```python
def sync_connection(session, connection, client):
    """Fetch and store every assignment across one connection's courses."""
    for course in fetch_courses(connection.base_url, connection.access_token, client):
        parsed_list = fetch_assignments(
            connection.base_url, connection.access_token, course["id"], client
        )
        for parsed in parsed_list:
            _upsert(session, connection.id, parsed)
    connection.last_synced_at = _now()
    session.add(connection)
    session.flush()
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_sync.py -v`
Expected: PASS — the new test plus all existing sync tests green.

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/sync.py tests/test_sync.py
git commit -m "Stamp last_synced_at on successful sync"
```

---

### Task 2: One-off migration script for the live Neon DB

**Files:**
- Create: `tools/migrate_add_last_synced.py`

**Interfaces:**
- Consumes: `app.db.make_engine`, `DATABASE_URL` env var.
- Produces: nothing importable — a one-off composition root, run once by hand. Like `tools/init_db.py`, it has no unit test (it only issues a single idempotent DDL statement against a live DB).

- [ ] **Step 1: Write the script**

Create `tools/migrate_add_last_synced.py`:

```python
"""Add the connections.last_synced_at column to whatever DATABASE_URL points at.

    python tools/migrate_add_last_synced.py

One-time migration for the live Neon branch. Idempotent — uses
ADD COLUMN IF NOT EXISTS, so it is safe to run again. Prints the target host
(no credentials) so you can confirm the target before anything happens. Fresh
databases get the column automatically via tools/init_db.py; this is only for an
already-created connections table.
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
    print(f"About to alter connections table in: {where}")

    if url.startswith("sqlite"):
        print("This looks like a local SQLite file, not your Neon branch.")
        print("Point DATABASE_URL at Neon first, then re-run.")
        raise SystemExit(1)

    engine = make_engine(url)
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE connections ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMP"
        ))
    print("Column last_synced_at added (or already present). Done.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it imports cleanly (no DB needed)**

Run: `.venv\Scripts\python.exe -c "import ast; ast.parse(open('tools/migrate_add_last_synced.py').read()); print('ok')"`
Expected: `ok` (syntax check; do not run it against a DB here — the user runs it once against Neon).

- [ ] **Step 3: Commit**

```bash
git add tools/migrate_add_last_synced.py
git commit -m "Add one-off migration for last_synced_at column"
```

---

### Task 3: Auto-sync on add (keep connection on failure)

**Files:**
- Modify: `app/web.py` (new `get_canvas_client` dependency; `add_connection` route)
- Test: `tests/test_e2e.py` (new default fixture override + two tests)

**Interfaces:**
- Consumes: `sync_connection(session, connection, client)` from `app.sync`; `get_session` (existing).
- Produces: `get_canvas_client()` dependency yielding an `httpx.Client`; `POST /connections` now syncs the new connection before redirecting and persists it even if the sync raises.

- [ ] **Step 1: Add a default canvas-client override to the test app fixture**

Auto-sync would otherwise make a real network call in every connection test. Give the `app` fixture in `tests/test_e2e.py` a default mock that returns empty courses (fast, no network). Update the `app` fixture and add the import:

```python
def test_add_connection_auto_syncs_assignments(client, app, engine):
    import httpx
    signup(client, email="auto@x.com")

    def handler(request):
        path = request.url.path
        if path.endswith("/courses"):
            return httpx.Response(200, json=[{"id": 10, "name": "Bio"}])
        if path.endswith("/courses/10/assignments"):
            return httpx.Response(200, json=[{
                "id": 1, "name": "Lab report", "due_at": "2026-06-20T23:59:00Z",
                "points_possible": 25, "submission_types": ["online_upload"],
                "html_url": "https://school.test/a/1", "description": "<p>Do it.</p>",
            }])
        return httpx.Response(200, json=[])

    app.dependency_overrides[get_canvas_client] = lambda: httpx.Client(
        transport=httpx.MockTransport(handler)
    )

    client.post("/connections", data={
        "label": "Mine", "base_url": "https://school.test",
        "account_type": "student", "access_token": "tok",
    }, follow_redirects=False)

    body = client.get("/").text
    assert "Lab report" in body
```

Also add this second test for failure-persistence:

```python
def test_add_connection_persists_even_when_sync_fails(client, app, engine):
    import httpx
    signup(client, email="failsync@x.com")

    def boom(request):
        return httpx.Response(401, json={"errors": ["bad token"]})

    app.dependency_overrides[get_canvas_client] = lambda: httpx.Client(
        transport=httpx.MockTransport(boom)
    )

    resp = client.post("/connections", data={
        "label": "Mine", "base_url": "https://school.test",
        "account_type": "student", "access_token": "tok",
    }, follow_redirects=False)
    assert resp.status_code in (302, 303)

    with Session(engine) as s:
        assert s.exec(select(Connection)).first() is not None
    # Dashboard still renders, no crash.
    assert client.get("/").status_code == 200
```

Update the `app` fixture to import `get_canvas_client` and register the default override:

```python
from app.web import create_app, get_canvas_client, get_groq_client, get_session
```

```python
@pytest.fixture
def app(engine):
    import httpx
    application = create_app()

    def _get_session():
        with Session(engine) as s:
            yield s

    def _empty_canvas():
        return httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[])
        ))

    application.dependency_overrides[get_session] = _get_session
    application.dependency_overrides[get_canvas_client] = _empty_canvas
    return application
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_e2e.py::test_add_connection_auto_syncs_assignments tests/test_e2e.py::test_add_connection_persists_even_when_sync_fails -v`
Expected: FAIL — `ImportError: cannot import name 'get_canvas_client' from 'app.web'`.

- [ ] **Step 3: Add the dependency and wire auto-sync**

In `app/web.py`, add the import near the top:

```python
from app.sync import sync_connection
```

Add a dependency next to `get_groq_client`:

```python
def get_canvas_client():
    client = httpx.Client(timeout=30.0)
    try:
        yield client
    finally:
        client.close()
```

Replace the `add_connection` route body so it syncs after saving and keeps the connection on failure:

```python
    @app.post("/connections")
    def add_connection(request: Request, label: str = Form(), base_url: str = Form(),
                       account_type: str = Form(), access_token: str = Form(),
                       session: Session = Depends(get_session),
                       canvas: httpx.Client = Depends(get_canvas_client)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        connection = Connection(
            user_id=user.id, label=label, base_url=base_url,
            account_type=account_type, access_token=access_token,
        )
        session.add(connection)
        session.flush()  # assign connection.id before syncing
        try:
            sync_connection(session, connection, canvas)
        except Exception:
            # Keep the connection; a bad token or Canvas outage must not lose it.
            # Never surface or log the token. last_synced_at stays None as the flag.
            pass
        session.commit()
        return RedirectResponse("/", status_code=303)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_e2e.py -v`
Expected: PASS — the two new tests plus every existing e2e test green (existing `test_add_connection_encrypts_token` now runs through the empty-canvas override, no network).

- [ ] **Step 5: Commit**

```bash
git add app/web.py tests/test_e2e.py
git commit -m "Auto-sync assignments when a connection is added"
```

---

### Task 4: Settings accounts-list page

**Files:**
- Create: `app/templates/settings.html`
- Modify: `app/web.py` (new `GET /connections` route; change `POST /connections` redirect target)
- Modify: `app/templates/base.html:21` (nav "Account" link target)
- Test: `tests/test_e2e.py` (two tests)

**Interfaces:**
- Consumes: `_current_user`, `get_session`, `Connection` (all existing).
- Produces: `GET /connections` renders `settings.html` with `connections` (a list of the user's `Connection`s, each exposing `label`, `base_url`, `account_type`, `last_synced_at`, and `assignments`). `POST /connections` now redirects to `/connections`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_e2e.py`:

```python
def test_settings_lists_connections(client, engine):
    signup(client, email="settings@x.com")
    seed_assignment(engine, "settings@x.com")  # creates a "Mine" connection

    body = client.get("/connections").text
    assert "Mine" in body
    assert "https://school.test" in body
    assert "Add account" in body


def test_settings_shows_empty_state(client):
    signup(client, email="noaccts@x.com")

    body = client.get("/connections").text
    assert "No accounts yet" in body
    assert "Add account" in body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_e2e.py::test_settings_lists_connections tests/test_e2e.py::test_settings_shows_empty_state -v`
Expected: FAIL — `GET /connections` is not defined (404), so `"No accounts yet"` / `"Mine"` are absent.

- [ ] **Step 3: Add the settings route**

In `app/web.py`, add this route (place it just above `GET /connections/new`) and import `select` is already present:

```python
    @app.get("/connections")
    def connections_list(request: Request, session: Session = Depends(get_session)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        connections = session.exec(
            select(Connection)
            .where(Connection.user_id == user.id)
            .order_by(Connection.created_at)
        ).all()
        return TEMPLATES.TemplateResponse(
            request, "settings.html", {"connections": connections})
```

Change the redirect at the end of `add_connection` from `"/"` to `"/connections"`:

```python
        session.commit()
        return RedirectResponse("/connections", status_code=303)
```

- [ ] **Step 4: Create the template**

Create `app/templates/settings.html`:

```html
{% extends "base.html" %}
{% block title %}Accounts · Canvas Daily{% endblock %}
{% block body %}
  <a class="back-link" href="/">← Back to dashboard</a>

  <section class="brief brief--tight">
    <p class="eyebrow">Account command center</p>
    <h1 class="brief__title">Connected accounts</h1>
    <p class="hero__sub">The Canvas accounts feeding your daily report.</p>
  </section>

  <div class="panel__head" style="display:flex;justify-content:space-between;align-items:center;">
    <h2 class="panel__title">Your accounts</h2>
    <a class="btn btn--primary" href="/connections/new">+ Add account</a>
  </div>

  {% if connections %}
    <ul class="cards">
      {% for c in connections %}
        <li class="card">
          <div class="card__top">
            <span class="course-pill">{{ c.account_type }}</span>
            {% if c.last_synced_at %}
              <span class="badge badge--done">Synced {{ c.last_synced_at.strftime('%b %d, %H:%M') }} UTC</span>
            {% else %}
              <span class="badge badge--danger">Not synced yet</span>
            {% endif %}
          </div>
          <h3 class="card__title">{{ c.label }}</h3>
          <div class="card__foot">
            <span class="due">{{ c.base_url }}</span>
            <span>{{ c.assignments | length }} assignment{{ '' if c.assignments | length == 1 else 's' }}</span>
          </div>
          {% if not c.last_synced_at %}
            <p class="field__hint">We couldn't pull assignments yet — they'll appear after the next daily sync.</p>
          {% endif %}
          <form method="post" action="/connections/{{ c.id }}/delete"
                onsubmit="return confirm('Remove this account and its assignments?');">
            <button class="btn btn--ghost btn--sm" type="submit">Remove</button>
          </form>
        </li>
      {% endfor %}
    </ul>
  {% else %}
    <div class="empty">
      <p class="empty__title">No accounts yet</p>
      <p class="empty__sub">Add a Canvas connection to start pulling assignments into your report.</p>
      <a class="btn btn--primary" href="/connections/new">+ Add account</a>
    </div>
  {% endif %}
{% endblock %}
```

- [ ] **Step 5: Point the nav "Account" link at the list**

In `app/templates/base.html`, change the nav link:

```html
        <a class="navlink" href="/connections">Account</a>
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_e2e.py -v`
Expected: PASS — both new tests plus all existing e2e tests green.

- [ ] **Step 7: Commit**

```bash
git add app/web.py app/templates/settings.html app/templates/base.html tests/test_e2e.py
git commit -m "Settings page lists connected accounts"
```

---

### Task 5: Remove a connection

**Files:**
- Modify: `app/web.py` (new `POST /connections/{id}/delete` route)
- Test: `tests/test_e2e.py` (two tests)

**Interfaces:**
- Consumes: `_current_user`, `get_session`, `Connection`, `Assignment` (all existing). The `Connection` → `Assignment` relationship already cascades delete-orphan, so deleting a connection removes its assignments.
- Produces: `POST /connections/{connection_id}/delete` — ownership-checked (404 if not the user's), deletes the connection, redirects to `/connections`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_e2e.py`:

```python
def test_delete_connection_removes_it_and_its_assignments(client, engine):
    signup(client, email="del@x.com")
    seed_assignment(engine, "del@x.com")
    with Session(engine) as s:
        conn_id = s.exec(select(Connection)).first().id

    resp = client.post(f"/connections/{conn_id}/delete", follow_redirects=False)
    assert resp.status_code in (302, 303)

    with Session(engine) as s:
        assert s.exec(select(Connection)).first() is None
        assert s.exec(select(Assignment)).first() is None


def test_cannot_delete_another_users_connection(client, app, engine):
    signup(client, email="owner2@x.com")
    seed_assignment(engine, "owner2@x.com")
    with Session(engine) as s:
        conn_id = s.exec(select(Connection)).first().id

    from fastapi.testclient import TestClient
    intruder = TestClient(app)
    signup(intruder, email="intruder2@x.com")

    resp = intruder.post(f"/connections/{conn_id}/delete", follow_redirects=False)
    assert resp.status_code == 404
    with Session(engine) as s:
        assert s.exec(select(Connection)).first() is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_e2e.py::test_delete_connection_removes_it_and_its_assignments tests/test_e2e.py::test_cannot_delete_another_users_connection -v`
Expected: FAIL — the delete route is undefined (404 for the owner too, so the redirect assertion fails).

- [ ] **Step 3: Add the delete route**

In `app/web.py`, add below `add_connection`:

```python
    @app.post("/connections/{connection_id}/delete")
    def delete_connection(request: Request, connection_id: int,
                          session: Session = Depends(get_session)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        connection = session.get(Connection, connection_id)
        if connection is None or connection.user_id != user.id:
            raise HTTPException(status_code=404)
        session.delete(connection)
        session.commit()
        return RedirectResponse("/connections", status_code=303)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_e2e.py -v`
Expected: PASS — both new tests plus all existing e2e tests green.

- [ ] **Step 5: Commit**

```bash
git add app/web.py tests/test_e2e.py
git commit -m "Allow removing a connected account"
```

---

### Task 6: Test evidence + README

**Files:**
- Create: `docs/test-evidence/sync-red.png`, `docs/test-evidence/sync-green.png`, `docs/test-evidence/e2e-red.png`, `docs/test-evidence/e2e-green.png` (recaptured)
- Modify: `README.md` (Test evidence section captions, if wording needs updating)

**Interfaces:**
- Consumes: `tools/run_to_html.py`, the browser screenshot flow from CLAUDE.md.
- Produces: refreshed red/green evidence for the `sync` and `e2e` labels referenced in the README so `tools/check_evidence.py` and the pre-commit hook pass.

> Note: the *live red* for each layer was captured during Tasks 1, 3, 4, and 5 (Step 2 of each). This task records the canonical per-label red/green pair required by the evidence rule. If a clean live-red PNG was already saved during those steps, reuse it; otherwise re-run the red command against the pre-implementation test on a stash.

- [ ] **Step 1: Capture the sync green**

Run: `.venv\Scripts\python.exe tools/run_to_html.py sync-green tests/test_sync.py`
Expected: terminal HTML reports GREEN.

- [ ] **Step 2: Capture the e2e green**

Run: `.venv\Scripts\python.exe tools/run_to_html.py e2e-green tests/test_e2e.py`
Expected: terminal HTML reports GREEN.

- [ ] **Step 3: Screenshot the pages**

Serve and screenshot each HTML page per the CLAUDE.md procedure:

```bash
.venv\Scripts\python.exe -m http.server 8731 --directory docs/test-evidence
```

Navigate the browser to `http://127.0.0.1:8731/sync-red.html`, `sync-green.html`, `e2e-red.html`, `e2e-green.html`; screenshot the `.frame` element to the matching PNG in `docs/test-evidence/`. Verify each by eye: every line legible, red shows red FAILED, green shows green passed.

- [ ] **Step 4: Reference the images in the README**

Ensure the README "Test evidence" section links all four PNGs with one-line captions (sync: last_synced_at stamping; e2e: auto-sync on add + accounts list + remove). Stop the HTTP server.

- [ ] **Step 5: Run the full suite + evidence check**

Run: `.venv\Scripts\python.exe -m pytest` then `.venv\Scripts\python.exe tools/check_evidence.py`
Expected: all tests pass; evidence check reports OK for every label.

- [ ] **Step 6: Commit**

```bash
git add docs/test-evidence README.md
git commit -m "Refresh test evidence for autosync and settings"
```

---

## Self-Review

**Spec coverage:**
- Auto-sync on add (sync-then-redirect, keep-on-failure) → Task 3. ✓
- `last_synced_at` real column + set on success → Task 1. ✓
- One-off ALTER migration in the `tools/` idiom → Task 2. ✓
- Settings list: label · URL · type · last synced · count + Add account + empty state → Task 4. ✓
- Remove button + ownership-checked delete route → Task 5. ✓
- Nav "Account" → `/connections`; POST redirect → `/connections` → Task 4. ✓
- Dashboard unchanged (already aggregates) → no task needed. ✓
- Test evidence for `sync` + `e2e` labels, README → Task 6. ✓
- No token in warnings/logs → Task 3 swallows the exception without touching the token; settings warning text (Task 4) is token-free. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to" — every code step shows full code. ✓

**Type consistency:** `get_canvas_client` defined in Task 3 and imported in the Task 3 fixture; `sync_connection` signature matches `app/sync.py`; `last_synced_at` named identically in model (Task 1), template (Task 4), and warning logic; `delete_connection` route path `/connections/{connection_id}/delete` matches the template form action `/connections/{{ c.id }}/delete` (Task 4) and the tests (Task 5). ✓
