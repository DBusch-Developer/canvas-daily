# Verify the Canvas token at entry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject an invalid Canvas access token the moment a connection is added, with a clear message, instead of silently storing it and failing in the background sync.

**Architecture:** A new `verify_token` function in `app/canvas.py` probes `GET /api/v1/users/self` and returns `"ok" | "invalid" | "unreachable"`. The `add_connection` handler calls it before saving; a non-`ok` result re-renders the form with a specific message and saves nothing. Documented as a new TDD Layer 23 with live red/green evidence.

**Tech Stack:** FastAPI, SQLModel, httpx (Canvas mocked at the transport boundary), pytest, Jinja2.

## Global Constraints

- TDD-first: failing test before implementation. One live **red** + one **green** screenshot for the whole layer, in `docs/test-evidence/`, referenced in the README. Red captured before `verify_token` exists.
- Never store, print, or log the token in plaintext — not in `verify_token`, not in the handler.
- Base URL lives on the connection (`base_url` arg), never global config.
- New behavior → its own `tests/test_verify.py` (label `verify`) and its own README "Layer 23" section. Do not fold into an existing layer.
- Probe endpoint is `GET {base_url}/api/v1/users/self`. `200` → `ok`; `401`/`403` → `invalid`; anything else / timeout / network error → `unreachable`.
- The success path of `add_connection` is unchanged (create, commit, schedule background sync, `303` to `/connections/{id}/setup`).

---

### Task 1: Layer 23 — verify the token at entry (single layer, single commit)

**Files:**
- Create: `tests/test_verify.py`
- Modify: `app/canvas.py` (add `import httpx` + `verify_token`)
- Modify: `app/web.py` (verify inside `add_connection` before saving)
- Modify: `app/templates/connection_new.html` (prefill values on error)
- Create: `docs/test-evidence/verify-red.png`, `docs/test-evidence/verify-green.png`
- Modify: `README.md` (new "Layer 23" section under Test evidence)

**Interfaces:**
- Produces: `verify_token(base_url: str, token: str, client: httpx.Client) -> str` returning `"ok" | "invalid" | "unreachable"`.
- Consumes: existing test patterns from `tests/test_setup.py` — `client_for(handler)`, `get_engine` / `get_canvas_client_factory` dependency overrides, `TEST_DATABASE_URL` skip guard.

- [ ] **Step 1: Write the failing tests** — `tests/test_verify.py`:

```python
"""Layer 23 — verify the Canvas token at entry (Canvas mocked; handler on Neon test branch)."""

import os

import httpx
import pytest
from sqlmodel import Session, SQLModel, select

from app.canvas import verify_token
from app.db import make_engine
from app.models import Connection, User
from app.web import get_canvas_client_factory

BASE = "https://school.test"


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- verify_token unit (Canvas mocked) ---------------------------------------

def test_verify_token_ok_on_200():
    def canvas(request):
        assert request.url.path.endswith("/api/v1/users/self")
        return httpx.Response(200, json={"id": 1, "name": "A Student"})
    assert verify_token(BASE, "good-token", client_for(canvas)) == "ok"


def test_verify_token_invalid_on_401():
    def canvas(request):
        return httpx.Response(401, json={"errors": [{"message": "Invalid access token."}]})
    assert verify_token(BASE, "bad", client_for(canvas)) == "invalid"


def test_verify_token_unreachable_on_network_error():
    def canvas(request):
        raise httpx.ConnectTimeout("canvas down")
    assert verify_token(BASE, "whatever", client_for(canvas)) == "unreachable"


# --- handler wiring (Neon test branch) ---------------------------------------

pg = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (handler tests need a Neon test branch)",
)


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


def make_app(engine, canvas_handler):
    from app.web import create_app, get_engine
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_canvas_client_factory] = lambda: (
        lambda: client_for(canvas_handler)
    )
    return app


def client_for_app(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


def signup(client, email):
    return client.post("/signup", data={"email": email, "password": "hunter2pw"},
                       follow_redirects=False)


def add_form(client):
    return client.post("/connections", data={
        "label": "Mine", "base_url": BASE,
        "account_type": "student", "access_token": "tok",
    }, follow_redirects=False)


@pg
def test_add_connection_rejects_bad_token_without_saving(engine):
    app = make_app(engine, lambda r: httpx.Response(401, json={"e": 1}))
    client = client_for_app(app)
    signup(client, "rej@x.com")

    resp = add_form(client)

    assert resp.status_code == 400
    assert "Canvas rejected this access token" in resp.text
    with Session(engine) as s:
        assert s.exec(select(Connection)).first() is None


@pg
def test_add_connection_saves_when_token_is_valid(engine):
    def canvas(request):
        path = request.url.path
        if path.endswith("/api/v1/users/self"):
            return httpx.Response(200, json={"id": 1})
        if path.endswith("/courses"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[])
    app = make_app(engine, canvas)
    client = client_for_app(app)
    signup(client, "ok@x.com")

    resp = add_form(client)

    assert resp.status_code in (302, 303)
    with Session(engine) as s:
        conn = s.exec(select(Connection)).first()
        assert conn is not None
        assert resp.headers["location"] == f"/connections/{conn.id}/setup"
```

- [ ] **Step 2: Confirm red (plain pytest)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_verify.py -v`
Expected: collection/import FAILS — `ImportError: cannot import name 'verify_token' from 'app.canvas'`.

- [ ] **Step 3: Capture red live (before any implementation)**

Run: `.venv\Scripts\python.exe tools/run_to_html.py verify-red tests/test_verify.py`
Expected: prints `[RED (pytest exit ...)]`. Then serve and screenshot:
- Run (background): `.venv\Scripts\python.exe -m http.server 8731 --directory docs/test-evidence`
- Browser → `http://127.0.0.1:8731/verify-red.html`, screenshot the `.frame` element → save `docs/test-evidence/verify-red.png`.
- Verify by eye: red `FAILED`/`ERROR` legible, no black-on-black.

- [ ] **Step 4: Implement `verify_token`** — add to `app/canvas.py` (add `import httpx` at top with the other imports):

```python
def verify_token(base_url, token, client):
    """Probe Canvas with the token. Returns "ok" | "invalid" | "unreachable".

    "ok"          -> Canvas accepted the token (200)
    "invalid"     -> Canvas rejected the token (401/403)
    "unreachable" -> any other status, timeout, or network error

    The lightest authenticated endpoint (one record, no pagination). The token
    is never logged.
    """
    url = f"{base_url.rstrip('/')}/api/v1/users/self"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = client.get(url, headers=headers)
    except httpx.HTTPError:
        return "unreachable"
    if response.status_code == 200:
        return "ok"
    if response.status_code in (401, 403):
        return "invalid"
    return "unreachable"
```

- [ ] **Step 5: Wire it into `add_connection`** — `app/web.py`. Add `verify_token` to the `from app.sync import ...` neighbours (import from `app.canvas`), and insert the verify branch immediately after `if user is None: ...` and before `connection = Connection(...)`:

```python
        client = client_factory()
        try:
            result = verify_token(base_url, access_token, client)
        finally:
            client.close()
        if result != "ok":
            message = (
                "Canvas rejected this access token. In Canvas, go to "
                "Account → Settings, generate a new access token, and paste it again."
                if result == "invalid" else
                "We couldn't reach Canvas to verify this connection. "
                "Double-check the base URL and try again."
            )
            return TEMPLATES.TemplateResponse(
                request, "connection_new.html",
                {"error": message, "label": label, "base_url": base_url,
                 "account_type": account_type},
                status_code=400,
            )
```

Add the import near the top of `app/web.py`:

```python
from app.canvas import verify_token
```

- [ ] **Step 6: Prefill the form on error** — `app/templates/connection_new.html`:
  - Label input → add `value="{{ label or '' }}"`.
  - Base URL input → add `value="{{ base_url or '' }}"`.
  - Account type select → make options:
    ```html
    <option value="student" {% if account_type == 'student' %}selected{% endif %}>Student</option>
    <option value="observer" {% if account_type == 'observer' %}selected{% endif %}>Observer</option>
    ```
  - Access token input → leave with no value (never re-displayed).

- [ ] **Step 7: Confirm green**

Run: `.venv\Scripts\python.exe -m pytest tests/test_verify.py -v`
Expected: all tests PASS (handler tests run because `TEST_DATABASE_URL` is set; otherwise they skip and the 3 unit tests pass).

- [ ] **Step 8: Run the full suite (no regressions)**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all pass.

- [ ] **Step 9: Capture green**

Run: `.venv\Scripts\python.exe tools/run_to_html.py verify-green tests/test_verify.py`
Expected: `[GREEN (all passed)]`. Screenshot `http://127.0.0.1:8731/verify-green.html` `.frame` → `docs/test-evidence/verify-green.png`. Verify by eye. Stop the HTTP server.

- [ ] **Step 10: Add the README "Layer 23" section** — under Test evidence, after Layer 22, matching the house format:

```markdown
**Layer 23 — verify the Canvas token at entry**

A connection used to accept any string as an access token, store it, and only discover a bad token in the background sync — the user just saw a spinner settle into a generic failure. Now `add_connection` probes Canvas (`GET /api/v1/users/self`) before saving: a rejected token (401/403) re-renders the form with "Canvas rejected this access token…" and saves nothing; an unreachable Canvas gets its own message; a good token saves and syncs exactly as before. `verify_token` returns `ok` / `invalid` / `unreachable`, and never logs the token.

Red — `verify_token` doesn't exist yet:

![Token verification tests failing](docs/test-evidence/verify-red.png)

Green — after adding `verify_token` and the entry-time check:

![Token verification tests passing](docs/test-evidence/verify-green.png)
```

- [ ] **Step 11: Commit** (pre-commit hook runs check_evidence + full suite)

```bash
git add tests/test_verify.py app/canvas.py app/web.py app/templates/connection_new.html docs/test-evidence/verify-red.png docs/test-evidence/verify-green.png README.md docs/superpowers/plans/2026-06-21-token-verification-at-entry.md
git commit -m "Verify the Canvas token at entry (Layer 23)"
```

---

## Self-Review

- **Spec coverage:** `verify_token` (3 outcomes) ✓ Step 4; `/users/self` probe ✓; handler rejects without saving ✓ Step 5 + tests; success path unchanged ✓; template prefill ✓ Step 6; Layer 23 evidence ✓ Steps 3/9/10. Out-of-scope items (later-breaking token, retry, format checks) intentionally absent.
- **Placeholder scan:** none — every code/command step is concrete.
- **Type consistency:** `verify_token(base_url, token, client) -> str` used identically in tests (Step 1), implementation (Step 4), and handler (Step 5). Message string `"Canvas rejected this access token"` matches between handler (Step 5) and assertion (Step 1).
