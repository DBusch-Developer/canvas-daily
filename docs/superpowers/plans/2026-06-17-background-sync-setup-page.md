# Background Sync + Account Setup Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adding a connection returns immediately to a "Setting up…" page that polls until the background sync finishes, instead of freezing on a synchronous request.

**Architecture:** `POST /connections` persists the connection (`sync_status="pending"`), schedules the sync on FastAPI `BackgroundTasks`, and redirects to a setup page. The page polls a JSON status endpoint until `ok`/`error`. The background task opens its own DB session (the request's is gone) and uses an injectable client factory so tests can mock Canvas. A new `sync_status` column makes the poll deterministic.

**Tech Stack:** FastAPI (`BackgroundTasks`, `JSONResponse`), SQLModel (Postgres/Neon), Jinja2, httpx (`MockTransport` in tests), pytest, vanilla JS poller.

## Global Constraints

- TDD-first: write the failing test, observe RED live before implementation. Red captured live for evidence — never reenacted.
- Tokens encrypted at rest; never store, print, log, or render a Canvas token in plaintext — not in a warning, error, log line, or template.
- Base URL lives on the connection, never global config.
- One code path for one connection and for four — no special-casing.
- Don't add new frameworks; vanilla JS for the poller, no added HTMX.
- New feature work is its own layer: dedicated `tests/test_setup.py` + its own numbered README "Layer 10" section (description → red → green) with `setup-red.png` / `setup-green.png`.
- Short, one-line commit messages. Commit straight to `main` (solo project, no branches).
- Test runner: `.venv\Scripts\python.exe -m pytest` from repo root. `conftest.py` auto-loads `.env` (provides `TEST_DATABASE_URL`, `TOKEN_ENCRYPTION_KEY`), so DB-backed tests run, not skip.

---

### Task 1: `sync_status` column + migration

**Files:**
- Modify: `app/models.py` (Connection model)
- Create: `tools/migrate_add_sync_status.py`
- Create: `tests/test_setup.py` (scaffold + first test)

**Interfaces:**
- Consumes: `Connection`, `User`, `make_engine`.
- Produces: `Connection.sync_status: str` defaulting to `"pending"` (values `pending`|`ok`|`error`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_setup.py`:

```python
"""Layer 10 — background sync + account setup page (Canvas mocked, Neon test branch)."""

import logging
import os
from datetime import datetime

import httpx
import pytest
from sqlmodel import Session, SQLModel, select

from app.db import make_engine
from app.models import Assignment, Connection, User

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (setup-flow tests need a Neon test branch)",
)

BASE = "https://school.test"


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


def a_user_and_connection(engine, email="d@x.com", token="tok"):
    """Create a user + one connection; return the connection id."""
    with Session(engine) as s:
        user = User(email=email, password_hash="h")
        s.add(user); s.commit(); s.refresh(user)
        conn = Connection(user_id=user.id, label="Mine", base_url=BASE,
                          account_type="student", access_token=token)
        s.add(conn); s.commit(); s.refresh(conn)
        return conn.id


def test_new_connection_defaults_to_pending(engine):
    conn_id = a_user_and_connection(engine)
    with Session(engine) as s:
        assert s.get(Connection, conn_id).sync_status == "pending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_setup.py::test_new_connection_defaults_to_pending -v`
Expected: FAIL — `AttributeError: 'Connection' object has no attribute 'sync_status'`.

- [ ] **Step 3: Add the column**

In `app/models.py`, inside `class Connection`, add after `last_synced_at`:

```python
    last_synced_at: datetime | None = None
    sync_status: str = Field(default="pending")  # pending | ok | error
```

- [ ] **Step 4: Create the migration script**

Create `tools/migrate_add_sync_status.py`:

```python
"""Add the connections.sync_status column to whatever DATABASE_URL points at.

    python tools/migrate_add_sync_status.py

One-time migration for the live Neon branch. Idempotent (ADD COLUMN IF NOT
EXISTS) and safe to re-run. Backfills already-synced rows to 'ok'. Prints the
target host (no credentials). Fresh databases get the column via tools/init_db.py.
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
            "ALTER TABLE connections ADD COLUMN IF NOT EXISTS sync_status TEXT DEFAULT 'pending'"
        ))
        conn.execute(text(
            "UPDATE connections SET sync_status='ok' WHERE last_synced_at IS NOT NULL"
        ))
    print("Column sync_status added (or already present) and backfilled. Done.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests to verify green**

Run: `.venv\Scripts\python.exe -m pytest tests/test_setup.py -v`
Expected: PASS. Also syntax-check the script:
`.venv\Scripts\python.exe -c "import ast; ast.parse(open('tools/migrate_add_sync_status.py').read()); print('ok')"` → `ok` (do NOT run it against a DB).

- [ ] **Step 6: Commit**

```bash
git add app/models.py tools/migrate_add_sync_status.py tests/test_setup.py
git commit -m "Add sync_status column and migration"
```

---

### Task 2: Background sync function

**Files:**
- Modify: `app/web.py` (module-level function)
- Test: `tests/test_setup.py`

**Interfaces:**
- Consumes: `sync_connection(session, connection, client)`, `Connection`, `Session`, `logger`.
- Produces: `run_connection_sync(engine, connection_id, client_factory)` — opens its own session, runs the sync, sets `sync_status` to `"ok"` or `"error"`, commits. `client_factory` is a zero-arg callable returning an `httpx.Client`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_setup.py` (helpers first, then tests):

```python
def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def canvas_ok(request):
    path = request.url.path
    if path.endswith("/courses"):
        return httpx.Response(200, json=[{"id": 10, "name": "Bio"}])
    if path.endswith("/courses/10/assignments"):
        return httpx.Response(200, json=[{
            "id": 1, "name": "Lab report", "due_at": "2026-06-20T23:59:00Z",
            "points_possible": 25, "submission_types": ["online_upload"],
            "html_url": f"{BASE}/a/1", "description": "<p>Do it.</p>",
        }])
    return httpx.Response(200, json=[])


def test_background_sync_stores_and_marks_ok(engine):
    from app.web import run_connection_sync
    conn_id = a_user_and_connection(engine)

    run_connection_sync(engine, conn_id, lambda: client_for(canvas_ok))

    with Session(engine) as s:
        conn = s.get(Connection, conn_id)
        assert conn.sync_status == "ok"
        assert conn.last_synced_at is not None
        stored = s.exec(select(Assignment).where(Assignment.connection_id == conn_id)).all()
        assert len(stored) == 1


def test_background_sync_keeps_connection_and_marks_error(engine, caplog):
    from app.web import run_connection_sync
    conn_id = a_user_and_connection(engine, token="secret-token")

    def boom(request):
        return httpx.Response(401, json={"errors": ["bad token"]})

    with caplog.at_level(logging.WARNING):
        run_connection_sync(engine, conn_id, lambda: client_for(boom))

    with Session(engine) as s:
        conn = s.get(Connection, conn_id)
        assert conn is not None
        assert conn.sync_status == "error"
    assert "secret-token" not in caplog.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_setup.py::test_background_sync_stores_and_marks_ok tests/test_setup.py::test_background_sync_keeps_connection_and_marks_error -v`
Expected: FAIL — `ImportError: cannot import name 'run_connection_sync' from 'app.web'`.

- [ ] **Step 3: Implement the function**

In `app/web.py`, add at module level (after `_owned_assignment_or_404`, before `create_app`):

```python
def run_connection_sync(engine, connection_id, client_factory):
    """Pull one connection's assignments in the background and record the
    outcome. Opens its own session (the request's is gone). Never logs the token."""
    with Session(engine) as session:
        connection = session.get(Connection, connection_id)
        if connection is None:
            return
        client = client_factory()
        try:
            sync_connection(session, connection, client)
            connection.sync_status = "ok"
        except Exception:
            connection.sync_status = "error"
            logger.warning("background sync failed for connection %s", connection_id)
        finally:
            client.close()
        session.add(connection)
        session.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_setup.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/web.py tests/test_setup.py
git commit -m "Add background connection sync with status tracking"
```

---

### Task 3: Rework POST /connections to background + setup redirect

**Files:**
- Modify: `app/web.py` (dependencies + `add_connection` route)
- Modify: `tests/test_e2e.py` (`app` fixture)
- Modify: `tests/test_autosync.py` (`app` fixture; remove two add-flow tests)
- Test: `tests/test_setup.py` (app/client fixtures + three tests)

**Interfaces:**
- Consumes: `run_connection_sync`, `Connection`, `BackgroundTasks`.
- Produces: `get_engine()` dependency; `get_session(engine=Depends(get_engine))`; `get_canvas_client_factory()` returning `() -> httpx.Client`. `POST /connections` now redirects to `/connections/{id}/setup` and schedules the background sync. `get_canvas_client` is removed.

- [ ] **Step 1: Add the app/client fixtures and tests to `tests/test_setup.py`**

Append fixtures and tests:

```python
@pytest.fixture
def app(engine):
    from app.web import create_app, get_engine, get_canvas_client_factory
    application = create_app()
    application.dependency_overrides[get_engine] = lambda: engine
    application.dependency_overrides[get_canvas_client_factory] = lambda: (
        lambda: client_for(canvas_ok)
    )
    return application


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


def signup(client, email="parent@x.com", password="hunter2pw"):
    return client.post("/signup", data={"email": email, "password": password},
                       follow_redirects=False)


def add_form(client, label="Mine"):
    return client.post("/connections", data={
        "label": label, "base_url": BASE,
        "account_type": "student", "access_token": "tok",
    }, follow_redirects=False)


def test_add_connection_redirects_to_setup(client, engine):
    signup(client, email="setup@x.com")
    resp = add_form(client)
    assert resp.status_code in (302, 303)
    with Session(engine) as s:
        conn_id = s.exec(select(Connection)).first().id
    assert resp.headers["location"] == f"/connections/{conn_id}/setup"


def test_assignments_appear_after_background_sync(client, engine):
    signup(client, email="bg@x.com")
    add_form(client)  # TestClient runs the background task after the response
    body = client.get("/").text
    assert "Lab report" in body


def test_add_connection_failure_marks_error_and_keeps_it(client, app, engine):
    signup(client, email="bgfail@x.com")
    app.dependency_overrides[
        __import__("app.web", fromlist=["get_canvas_client_factory"]).get_canvas_client_factory
    ] = lambda: (lambda: client_for(lambda r: httpx.Response(401, json={"e": 1})))
    add_form(client)
    with Session(engine) as s:
        conn = s.exec(select(Connection)).first()
        assert conn is not None
        assert conn.sync_status == "error"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_setup.py::test_add_connection_redirects_to_setup -v`
Expected: FAIL — the POST still redirects to `/connections` (and `get_canvas_client_factory` import fails), so the location assertion fails.

- [ ] **Step 3: Add the dependencies and rework the route**

In `app/web.py`:

Add the import (top, with the other fastapi imports):

```python
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
```

Replace `get_session` and remove `get_canvas_client`, adding `get_engine` and `get_canvas_client_factory`:

```python
def get_engine():
    return _get_engine()


def get_session(engine=Depends(get_engine)):
    with Session(engine) as session:
        yield session


def get_groq_client():
    client = httpx.Client(timeout=30.0)
    try:
        yield client
    finally:
        client.close()


def get_canvas_client_factory():
    return lambda: httpx.Client(timeout=30.0)
```

(Delete the old `get_canvas_client` function entirely.)

Replace the `add_connection` route:

```python
    @app.post("/connections")
    def add_connection(request: Request, background_tasks: BackgroundTasks,
                       label: str = Form(), base_url: str = Form(),
                       account_type: str = Form(), access_token: str = Form(),
                       session: Session = Depends(get_session),
                       engine=Depends(get_engine),
                       client_factory=Depends(get_canvas_client_factory)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        connection = Connection(
            user_id=user.id, label=label, base_url=base_url,
            account_type=account_type, access_token=access_token,
        )
        session.add(connection)
        session.commit()
        session.refresh(connection)
        background_tasks.add_task(
            run_connection_sync, engine, connection.id, client_factory)
        return RedirectResponse(
            f"/connections/{connection.id}/setup", status_code=303)
```

- [ ] **Step 4: Update the existing test fixtures**

In `tests/test_e2e.py`, change the import line and the `app` fixture. Replace:

```python
from app.web import create_app, get_canvas_client, get_groq_client, get_session
```
with:
```python
from app.web import create_app, get_canvas_client_factory, get_engine, get_groq_client
```

Replace the `app` fixture body:

```python
@pytest.fixture
def app(engine):
    import httpx
    application = create_app()
    application.dependency_overrides[get_engine] = lambda: engine
    application.dependency_overrides[get_canvas_client_factory] = lambda: (
        lambda: httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[])))
    )
    return application
```

In `tests/test_autosync.py`, do the same: change the import from `get_canvas_client, get_session` to `get_canvas_client_factory, get_engine`, and replace the `app` fixture body with the same as above. Then **remove** the two add-flow tests `test_add_connection_auto_syncs_assignments` and `test_add_connection_persists_even_when_sync_fails` (they now live in `tests/test_setup.py`). Also remove the now-unused `import logging` from `tests/test_autosync.py` if nothing else uses it.

- [ ] **Step 5: Run the full suite to verify green**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: PASS. `test_setup.py` has 6 tests; `test_autosync.py` now has 5; `test_e2e.py` still 8.

- [ ] **Step 6: Commit**

```bash
git add app/web.py tests/test_setup.py tests/test_e2e.py tests/test_autosync.py
git commit -m "Run connection sync in background, redirect to setup page"
```

---

### Task 4: Setup page + status endpoint

**Files:**
- Modify: `app/web.py` (ownership helper + two routes)
- Create: `app/templates/setup.html`
- Test: `tests/test_setup.py`

**Interfaces:**
- Consumes: `_current_user`, `Connection`, `JSONResponse`.
- Produces: `_owned_connection_or_404(session, connection_id, user)`; `GET /connections/{id}/setup` (renders `setup.html`, redirects to `/connections` when `sync_status=="ok"`, 404 for non-owner); `GET /connections/{id}/status` (JSON `{"status": ...}`, 404 for non-owner, 401 logged out).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup.py`:

```python
def test_status_reports_ok_after_sync(client, engine):
    signup(client, email="st@x.com")
    add_form(client)
    with Session(engine) as s:
        conn_id = s.exec(select(Connection)).first().id
    body = client.get(f"/connections/{conn_id}/status").json()
    assert body["status"] == "ok"


def test_setup_page_renders_then_redirects_when_done(client, engine):
    signup(client, email="sp@x.com")
    # Pre-create a still-pending connection directly (no background run).
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == "sp@x.com")).one()
        conn = Connection(user_id=user.id, label="Mine", base_url=BASE,
                          account_type="student", access_token="tok",
                          sync_status="pending")
        s.add(conn); s.commit(); s.refresh(conn)
        conn_id = conn.id
    page = client.get(f"/connections/{conn_id}/setup")
    assert page.status_code == 200
    assert "Setting up" in page.text
    # Once marked ok, the setup page bounces to the accounts list.
    with Session(engine) as s:
        c = s.get(Connection, conn_id); c.sync_status = "ok"; s.add(c); s.commit()
    done = client.get(f"/connections/{conn_id}/setup", follow_redirects=False)
    assert done.status_code in (302, 303)
    assert done.headers["location"] == "/connections"


def test_cannot_view_another_users_setup_or_status(client, app, engine):
    signup(client, email="ownersetup@x.com")
    add_form(client)
    with Session(engine) as s:
        conn_id = s.exec(select(Connection)).first().id

    from fastapi.testclient import TestClient
    intruder = TestClient(app)
    signup(intruder, email="intrudersetup@x.com")

    assert intruder.get(f"/connections/{conn_id}/setup").status_code == 404
    assert intruder.get(f"/connections/{conn_id}/status").status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_setup.py::test_status_reports_ok_after_sync tests/test_setup.py::test_setup_page_renders_then_redirects_when_done tests/test_setup.py::test_cannot_view_another_users_setup_or_status -v`
Expected: FAIL — the `/setup` and `/status` routes don't exist (404 for the owner too / template missing).

- [ ] **Step 3: Add the ownership helper and routes**

In `app/web.py`, add the import:

```python
from fastapi.responses import JSONResponse, RedirectResponse
```

Add the helper next to `_owned_assignment_or_404`:

```python
def _owned_connection_or_404(session, connection_id, user):
    connection = session.get(Connection, connection_id)
    if connection is None or connection.user_id != user.id:
        raise HTTPException(status_code=404)
    return connection
```

Refactor `delete_connection` to use it (replace its inline check):

```python
        connection = _owned_connection_or_404(session, connection_id, user)
        session.delete(connection)
```

Add the two routes (after `delete_connection`):

```python
    @app.get("/connections/{connection_id}/setup")
    def connection_setup(request: Request, connection_id: int,
                         session: Session = Depends(get_session)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        connection = _owned_connection_or_404(session, connection_id, user)
        if connection.sync_status == "ok":
            return RedirectResponse("/connections", status_code=303)
        return TEMPLATES.TemplateResponse(
            request, "setup.html", {"connection": connection})

    @app.get("/connections/{connection_id}/status")
    def connection_status(request: Request, connection_id: int,
                          session: Session = Depends(get_session)):
        user = _current_user(request, session)
        if user is None:
            raise HTTPException(status_code=401)
        connection = _owned_connection_or_404(session, connection_id, user)
        return JSONResponse({"status": connection.sync_status})
```

- [ ] **Step 4: Create the setup template**

Create `app/templates/setup.html`:

```html
{% extends "base.html" %}
{% block title %}Setting up · Canvas Daily{% endblock %}
{% block body %}
  <section class="brief brief--tight" style="text-align:center;">
    <p class="eyebrow">Account command center</p>
    <h1 class="brief__title">Setting up {{ connection.label }}</h1>
    <p class="hero__sub" id="setup-msg">Pulling your assignments from Canvas — this can take a few seconds…</p>

    <div id="setup-spinner" class="spinner" aria-hidden="true"></div>

    <div id="setup-actions" hidden>
      <a class="btn btn--primary" href="/connections">Go to your accounts</a>
    </div>
  </section>

  <style>
    .spinner { width:42px; height:42px; margin:28px auto; border:4px solid #2a2a2a;
               border-top-color:#7c5cff; border-radius:50%; animation:spin .9s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>

  <script>
    (function () {
      var statusUrl = "/connections/{{ connection.id }}/status";
      var msg = document.getElementById("setup-msg");
      var spinner = document.getElementById("setup-spinner");
      var actions = document.getElementById("setup-actions");
      var tries = 0, maxTries = 30;
      function settle(text) { msg.textContent = text; spinner.hidden = true; actions.hidden = false; }
      function poll() {
        tries += 1;
        fetch(statusUrl, { headers: { "Accept": "application/json" } })
          .then(function (r) { return r.ok ? r.json() : { status: "pending" }; })
          .then(function (d) {
            if (d.status === "ok") { window.location = "/connections"; return; }
            if (d.status === "error") { settle("We couldn't finish pulling your assignments. Head to your accounts and try removing and re-adding it."); return; }
            if (tries >= maxTries) { settle("This is taking longer than expected. Head to your accounts — it may finish shortly."); return; }
            setTimeout(poll, 1500);
          })
          .catch(function () {
            if (tries >= maxTries) { settle("This is taking longer than expected. Head to your accounts — it may finish shortly."); return; }
            setTimeout(poll, 1500);
          });
      }
      poll();
    })();
  </script>
{% endblock %}
```

- [ ] **Step 5: Run the tests to verify green**

Run: `.venv\Scripts\python.exe -m pytest tests/test_setup.py tests/test_e2e.py -q`
Expected: PASS (setup tests green; delete still works via the helper).

- [ ] **Step 6: Commit**

```bash
git add app/web.py app/templates/setup.html tests/test_setup.py
git commit -m "Add account setup page and status endpoint"
```

---

### Task 5: Settings cleanup — drop redundant button, show error state

**Files:**
- Modify: `app/templates/settings.html`
- Test: `tests/test_autosync.py`

**Interfaces:**
- Consumes: `Connection.sync_status`, the existing settings list route.
- Produces: settings rows show a "Sync failed" badge when `sync_status=="error"`; the empty state no longer duplicates the "Add account" button.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_autosync.py`:

```python
def test_settings_shows_error_badge_on_failed_sync(client, engine):
    signup(client, email="errbadge@x.com")
    seed_assignment(engine, "errbadge@x.com")
    with Session(engine) as s:
        conn = s.exec(select(Connection)).first()
        conn.sync_status = "error"
        conn.last_synced_at = None
        s.add(conn); s.commit()

    body = client.get("/connections").text
    assert "Sync failed" in body
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_autosync.py::test_settings_shows_error_badge_on_failed_sync -v`
Expected: FAIL — "Sync failed" is not in the template.

- [ ] **Step 3: Update the template**

In `app/templates/settings.html`, replace the badge block:

```html
            {% if c.last_synced_at %}
              <span class="badge badge--done">Synced {{ c.last_synced_at.strftime('%b %d, %H:%M') }} UTC</span>
            {% elif c.sync_status == 'error' %}
              <span class="badge badge--danger">Sync failed</span>
            {% else %}
              <span class="badge badge--danger">Not synced yet</span>
            {% endif %}
```

And remove the redundant button from the empty state — delete this line:

```html
      <a class="btn btn--primary" href="/connections/new">+ Add account</a>
```

(the one inside `<div class="empty">`; keep the one in `.panel__head`).

- [ ] **Step 4: Run the tests to verify green**

Run: `.venv\Scripts\python.exe -m pytest tests/test_autosync.py -q`
Expected: PASS (6 tests, including the new badge test; empty-state test still green via the header button).

- [ ] **Step 5: Commit**

```bash
git add app/templates/settings.html tests/test_autosync.py
git commit -m "Show sync-failed badge and drop redundant add button"
```

---

### Task 6: Test evidence + README

**Files:**
- Create: `docs/test-evidence/setup-red.png`, `docs/test-evidence/setup-green.png`
- Modify: `docs/test-evidence/autosync-red.png`, `docs/test-evidence/autosync-green.png` (recaptured — autosync narrowed to 5/6 tests)
- Modify: `README.md`

**Interfaces:**
- Consumes: `tools/run_to_html.py`, the browser screenshot flow from CLAUDE.md.
- Produces: a new "Layer 10 — background sync + account setup" README section (description → red → green) with `setup-red.png`/`setup-green.png`; an updated Layer 9 description (drops the add-flow claims now in Layer 10) with re-captured `autosync` evidence; `check_evidence.py` passing.

> This task is controller-driven (needs the browser). The feature baseline is the commit just before Task 1. Capture each red by reverting the feature implementation in the working tree (keep the tests), rendering, then restoring — exactly the procedure used for earlier layers.

- [ ] **Step 1: Capture the setup layer green**

Run: `.venv\Scripts\python.exe tools/run_to_html.py setup-green tests/test_setup.py` → confirm GREEN.

- [ ] **Step 2: Capture the setup layer red (feature absent)**

Revert `app/web.py`, `app/models.py`, `app/templates/setup.html` (delete), `app/templates/settings.html` to the feature baseline in the working tree; run `.venv\Scripts\python.exe tools/run_to_html.py setup-red tests/test_setup.py` → confirm RED (import/collection error: `run_connection_sync`/`get_canvas_client_factory` absent); then restore all files (`git checkout HEAD -- ...`).

- [ ] **Step 3: Re-capture the autosync layer (now narrowed)**

Run `.venv\Scripts\python.exe tools/run_to_html.py autosync-green tests/test_autosync.py` → GREEN (6 tests). For red, revert the autosync-feature implementation to its baseline and render `autosync-red` (collection error), then restore.

- [ ] **Step 4: Screenshot all four pages**

Serve and screenshot per CLAUDE.md:

```bash
.venv\Scripts\python.exe -m http.server 8733 --directory docs/test-evidence
```

Navigate to `setup-red.html`, `setup-green.html`, `autosync-red.html`, `autosync-green.html`; screenshot each to the matching PNG in `docs/test-evidence/`. Verify by eye (legible; red shows red ERROR/FAILED; green shows passes).

- [ ] **Step 5: Update the README**

- Add a "**Layer 10 — background sync + account setup (Canvas mocked, Neon test branch)**" section after Layer 9's images and before the "How these are made" paragraph: a TDD description (background sync, setup page, status polling, ownership), then the red image (`docs/test-evidence/setup-red.png`) and green image (`docs/test-evidence/setup-green.png`) with one-line captions.
- Update the Layer 9 description to drop the "auto-sync on add" claims (now Layer 10) and the green caption to "six green passes". Keep its red caption consistent with the recaptured image.

- [ ] **Step 6: Verify and commit**

Run: `.venv\Scripts\python.exe -m pytest` then `.venv\Scripts\python.exe tools/check_evidence.py` (expect OK including `setup`). Stop the HTTP server.

```bash
git add docs/test-evidence README.md
git commit -m "Document background sync + setup as Layer 10"
```

---

## Self-Review

**Spec coverage:**
- `sync_status` column + idempotent migration with backfill → Task 1. ✓
- `get_engine` + `get_session` refactor; `get_canvas_client_factory`; remove `get_canvas_client` → Task 3. ✓
- `run_connection_sync` (own session, ok/error, token-free log) → Task 2. ✓
- `POST /connections` background + redirect to setup → Task 3. ✓
- `GET /setup` (render, redirect-when-ok, 404) and `GET /status` (JSON, 404/401) → Task 4. ✓
- `setup.html` spinner + vanilla-JS poller, error/timeout messaging → Task 4. ✓
- `settings.html` remove redundant button + error badge → Task 5. ✓
- Move two add-flow tests into Layer 10; update e2e/autosync fixtures → Task 3. ✓
- New Layer 10 evidence + README; re-capture Layer 9 → Task 6. ✓
- Migration run by user (`python tools/migrate_add_sync_status.py`) — documented; not executed by the plan. ✓

**Placeholder scan:** No TBD/"handle errors"/"similar to" — every code step is complete. ✓

**Type consistency:** `run_connection_sync(engine, connection_id, client_factory)` identical across Tasks 2/3; `get_engine`/`get_canvas_client_factory`/`get_session` names match across web.py and all three test fixtures; `_owned_connection_or_404` used by delete/setup/status (Task 4); `sync_status` values `pending`/`ok`/`error` consistent across model, function, routes, template, and tests; setup route path `/connections/{connection_id}/setup` matches the redirect target and the tests. ✓
