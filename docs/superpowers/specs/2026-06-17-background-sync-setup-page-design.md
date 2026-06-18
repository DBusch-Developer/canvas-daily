# Background sync + account setup page

Date: 2026-06-17

## Problem

Adding a Canvas connection runs the assignment sync **synchronously** and holds
the HTTP request open until every course is fetched. The user hits submit and
stares at a frozen form for several seconds, and a large account risks a gateway
timeout. We want immediate feedback (a real loading page) and no long-held
request.

Also: the settings empty state has a redundant "Add account" button directly
below the one already in the panel header.

## Decisions

- **Sync runs in the background**, via FastAPI `BackgroundTasks`. Adding a
  connection returns immediately and redirects to a setup page that polls for
  completion. No new job queue or framework.
- **A `sync_status` column** on `Connection` (`pending` | `ok` | `error`) makes
  the poll deterministic, so the setup page shows a real error instead of
  inferring one from a timeout. One-off idempotent migration, same pattern as
  `last_synced_at`, with a backfill for existing rows.
- **On failure: message + link to accounts.** No inline retry (keeps scope tight).
  The failure rule is unchanged — connection persists, token-free warning logged.
- This reworks the add flow, so it becomes its **own README layer (Layer 10)**.
  The two add-flow tests currently in the autosync layer move into it.

## Data model

`app/models.py` — add to `Connection`:
- `sync_status: str = Field(default="pending")` — `pending` | `ok` | `error`.

## Migration

`tools/migrate_add_sync_status.py` — same shape as `migrate_add_last_synced.py`
(load env, print host only, refuse SQLite). Runs:
- `ALTER TABLE connections ADD COLUMN IF NOT EXISTS sync_status TEXT DEFAULT 'pending'`
- backfill: `UPDATE connections SET sync_status='ok' WHERE last_synced_at IS NOT NULL`

Idempotent; safe to re-run. The user runs it once against Neon.

## Dependencies (injection)

The background task can't use the request-scoped Canvas client (closed after the
response), and needs an engine to open a fresh session. So:

- `get_engine()` — returns the app engine; `get_session` is refactored to depend
  on it (`def get_session(engine=Depends(get_engine))`). Tests override
  `get_engine` to return the test engine — a single override point for both the
  request session and the background task.
- `get_canvas_client_factory()` — returns a callable `() -> httpx.Client`
  (default `lambda: httpx.Client(timeout=30.0)`). Replaces the now-unused
  request-scoped `get_canvas_client`, which is removed. Tests override the
  factory to produce a `MockTransport` client.

## Background sync

`app/web.py`, module-level (testable by import):

```
def run_connection_sync(engine, connection_id, client_factory):
    """Pull one connection's assignments in the background and record the
    outcome. Opens its own session; never logs the token."""
    with Session(engine) as session:
        connection = session.get(Connection, connection_id)
        if connection is None:
            return
        client = client_factory()
        try:
            sync_connection(session, connection, client)   # stamps last_synced_at on success
            connection.sync_status = "ok"
        except Exception:
            connection.sync_status = "error"
            logger.warning("background sync failed for connection %s", connection_id)
        finally:
            client.close()
        session.add(connection)
        session.commit()
```

## Routes

- `POST /connections` — create the connection with `sync_status="pending"`,
  commit (so the background task's own session can load it), schedule
  `run_connection_sync(engine, connection.id, client_factory)` on
  `BackgroundTasks`, and redirect `303` to `/connections/{id}/setup`. No
  synchronous sync, no token in any log.
- `GET /connections/{id}/setup` — ownership-checked (404 otherwise; `/login`
  redirect if logged out). If `sync_status == "ok"`, redirect to `/connections`
  (handles refresh-after-done). Otherwise render `setup.html`.
- `GET /connections/{id}/status` — ownership-checked (404 otherwise). Returns
  JSON `{"status": "pending" | "ok" | "error"}`.

## Templates

- `app/templates/setup.html` — a centered "Setting up your account — pulling
  your assignments…" card with a CSS spinner and an inline vanilla-JS poller:
  fetch `/connections/{id}/status` every 1.5s; on `ok` → `location =
  "/connections"`; on `error` → reveal an error block with a "Go to your
  accounts" button; after ~30 tries (~45s) reveal a "taking longer than
  expected" block with the same button. No new framework (no added HTMX).
- `app/templates/settings.html` — remove the redundant "Add account" button in
  the empty state (keep the panel-header one). Show an `error` badge on a row
  when `sync_status == "error"`.

## Test plan (TDD — live red before each implementation)

**New Layer 10 — `tests/test_setup.py`** (label `setup`):
- `run_connection_sync` success → assignments stored, `last_synced_at` set,
  `sync_status == "ok"` (unit, mock Canvas).
- `run_connection_sync` failure (401) → connection kept, `sync_status ==
  "error"`, token never in logs (unit, caplog).
- `POST /connections` → redirects to `/connections/{id}/setup`; after the
  background task runs, the connection is `ok` and its assignment is on the
  dashboard (TestClient runs background tasks).
- `GET /status` reports `ok` after a successful sync and `error` after a failed
  one; `GET /setup` renders for the owner and redirects to `/connections` when
  already `ok`.
- Ownership: another user's `/setup` and `/status` return 404.

**Layer 9 — `tests/test_autosync.py`** narrows to accounts management: keep the
`last_synced_at` stamp unit, settings list, empty state, delete, and
ownership-delete (5 tests). The two add-flow tests move to Layer 10. Update its
`app` fixture to override `get_engine` + `get_canvas_client_factory`.

**`tests/test_e2e.py`** — update the `app` fixture the same way (override
`get_engine` + `get_canvas_client_factory`; drop the `get_canvas_client`
reference). Test outcomes unchanged (still 8 pass).

## Test evidence

- New `setup-red.png` / `setup-green.png` + a "Layer 10 — background sync +
  account setup" README section (description → red → green), red captured live.
- Re-capture Layer 9 (`autosync`) green (now 5 tests) and red; update its
  README description to drop the add-flow claims (now Layer 10).

## Out of scope

- No job queue / Celery / external worker — `BackgroundTasks` only.
- No inline retry on failure (link to accounts instead).
- No real-time progress percentage — binary pending → ok/error.
