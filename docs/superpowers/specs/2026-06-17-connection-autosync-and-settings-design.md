# Auto-sync on connection add + Settings accounts list

Date: 2026-06-17

## Problem

Adding a Canvas connection saves the connection but does **not** pull any
assignments. The user had to run `python -m app.jobs sync` by hand before the
dashboard showed anything. For a live web app used by classmates that is
unacceptable — adding a connection must populate the dashboard immediately.

Separately, the "settings" screen is just an add-connection form. There is no
list of connected accounts, no empty state, and no way to remove one. The page
should be an accounts list first, with the form reached via an "Add account"
button.

## Decisions

- **Auto-sync runs synchronously on add**, then redirects. No background tasks,
  no new frameworks — matches the server-rendered Jinja2 ethos. A few seconds of
  wait on submit is acceptable.
- **A failed sync keeps the connection.** Bad token / Canvas outage must not lose
  the connection. The connection persists; the settings page flags the account so
  the user can retry or wait for the daily job. No token ever appears in a
  warning, error, or log.
- **`last_synced_at` is a real column** on `Connection` (not derived). Added to the
  model so fresh DBs and the test branch get it via `create_all`. The live Neon
  branch is altered once by a one-off script in the `tools/` idiom — the repo has
  no migration framework and is not adopting one for a single column.
- **Account count is derived** from the connection's stored assignments.

## Changes

### Data model (`app/models.py`)
- Add `last_synced_at: datetime | None = None` to `Connection`.

### Migration (`tools/migrate_add_last_synced.py`)
- Same shape as `tools/init_db.py`: load env, show target host (no credentials),
  refuse on SQLite, run idempotent
  `ALTER TABLE connections ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMP`.
  Safe to re-run. The user runs it once against Neon.

### Sync (`app/sync.py`)
- `sync_connection` sets `connection.last_synced_at = _now()` after a successful
  fetch/store pass (before the flush). `run_daily_sync` is unchanged in behavior
  (it calls `sync_connection`, so it gets the stamp for free).

### Web (`app/web.py`)
- `GET /connections` → render new `settings.html` with the current user's
  connections and, per connection, its assignment count. (Replaces today's
  redirect-less form-only flow.)
- `GET /connections/new` → unchanged add form.
- `POST /connections` → save connection, build an httpx client, call
  `sync_connection` for **just the new connection**, commit. Wrap the sync in a
  try/except: on failure the connection is still committed; never surface or log
  the token. Redirect to `/connections` (was `/`).
- `POST /connections/{id}/delete` → ownership-checked (404 if not the user's),
  delete the connection (cascade removes its assignments), redirect to
  `/connections`.

### Templates
- New `app/templates/settings.html`: accounts list. Each row shows label, Canvas
  base URL, account type, last synced (`last_synced_at` or "Never"), assignment
  count, and a Remove button (small POST form). Empty state: "No accounts yet"
  copy + a prominent "Add account" button linking to `/connections/new`. Non-empty
  state also shows an "Add account" button.
- `app/templates/base.html`: nav "Account" link points to `/connections`.

### Dashboard
- No change. `report_for_user` already aggregates every connection; once auto-sync
  has run the assignments are simply present.

## Test plan (TDD — live red before each implementation)

- **`tests/test_sync.py`** (existing evidence label): `sync_connection` sets
  `last_synced_at` on a successful run; a connection with no courses still does not
  crash.
- **`tests/test_e2e.py`** (existing evidence label):
  - Adding a connection (Canvas mocked) lands its assignments on the dashboard with
    no manual job run.
  - When the sync raises, the connection persists and neither the settings page nor
    the dashboard errors.
  - `GET /connections` lists the user's accounts; with none it shows the empty
    state.
  - `POST /connections/{id}/delete` removes the connection and its assignments and
    only works on the owner's connection.

## Test evidence

Recapture live red → green for the two affected labels (`sync`, `e2e`) and update
the README "Test evidence" section. Red captured before each implementation exists,
per the standing rule.

## Out of scope

- No Alembic / general migration framework.
- No background job queue.
- No editing of an existing connection (only add + remove).
