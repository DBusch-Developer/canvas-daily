# Canvas Daily

An early-warning layer on top of [Canvas LMS](https://www.instructure.com/canvas). One person can connect many Canvas accounts — their own and their kids', across different institutions — and receive **one** correct, correctly-labeled daily report: Past due, Due today, Upcoming. Every item links to an instant, app-rendered detail page that can generate an AI study breakdown on demand.

It does **not** replace Canvas. Work is still done and submitted in Canvas — Canvas Daily just makes sure nothing slips by unseen.

## How it works

- One user owns many Canvas **connections** (each with its own institution base URL and access token).
- A **daily job** fetches and stores full assignment detail for every connection.
- A grouped **daily email** merges everything into one report, sorted by due date and labeled by connection.
- Each item links to a **detail page** rendered instantly from stored data — no live Canvas call on click.
- On any detail page, one button generates an **AI study breakdown** of that assignment.

## Two APIs

| API | Role |
| --- | --- |
| **Canvas LMS** | Pulls the raw assignment data per connection. |
| **Groq / Llama 3.3 70B** | Turns a single assignment into an actionable study plan, on demand. |

The detail page is where they meet: stored Canvas detail on the page, one button for the AI breakdown.

## Stack

- **FastAPI** — setup screens, report, detail pages, AI endpoint
- **SQLModel** — ORM over **Neon** (Postgres)
- **Jinja2** — server-rendered templates
- **Groq** via the OpenAI-compatible API — `base_url = https://api.groq.com/openai/v1`, model `llama-3.3-70b-versatile`
- **pytest** — test runner
- **cron** — daily job · **SMTP** — email delivery

One language, one test runner, one deploy.

## Data model

One-to-many: a **user** owns many **connections**; each connection owns many stored **assignments**.

- **User** — id, email, password hash, created_at
- **Connection** — id, user_id, label, base_url, account_type (`student` | `observer`), access_token (encrypted at rest), created_at
- **Assignment** — id, connection_id, canvas_assignment_id, name, description (sanitized HTML), due_at, points_possible, submission_types, html_url, workflow_state, score (nullable), submitted_at (nullable), late, missing, excused, fetched_at

## The AI study breakdown

On-demand, not pre-fetched. When the user presses the button, the endpoint assembles the assignment's title, description, points, due date, and course into a context block (missing fields dropped, not sent blank) and asks Groq for a fixed four-section markdown response:

1. **What's being asked**
2. **Step-by-step plan**
3. **Watch out for**
4. **Time estimate**

Capped under ~300 words, temperature `0.5`. A timeout returns a clear "took too long" message; any other failure returns a clean error — never a broken page.

## Built test-first

TDD throughout — red, green, refactor — layered inside-out:

1. **Pure functions** — date/bucketing helpers. Known in, known out, no mocks.
2. **Canvas fetch** — Canvas mocked. Pagination, `include[]=submission` parsing, sanitization, null scores.
3. **AI breakdown** — Groq mocked. Context assembly, four-section output, timeout → 504, no key in logs.
4. **ORM integration** — real Neon test branch. One-to-many model, round-trip, report query.
5. **End-to-end** — sign up, add connection, view report, click into detail, generate breakdown.
6. **Daily sync** — Canvas mocked. Per-connection course walk, store full detail, upsert (no duplicates), one path for one connection or many.
7. **Daily email** — SMTP mocked. One message per user, grouped/sorted/labeled by connection, no token in the body.

### Test evidence

Each layer is documented with a captured pytest run — the failing red before the code exists, the passing green after. Images live in [`docs/test-evidence/`](docs/test-evidence/).

**Layer 1 — date bucketing**

Red — the function stubbed, logic not yet written:

![Date tests failing — six red failures](docs/test-evidence/dates-red.png)

Green — after writing `classify_due`:

![Date tests passing — six green passes](docs/test-evidence/dates-green.png)

**Layer 2 — Canvas fetch (Canvas mocked at the httpx transport)**

Red — the five behaviors written against an empty stub: pagination, `include[]=submission` parsing, HTML sanitization, null-score preservation, core field parsing:

![Canvas fetch tests failing — five red failures](docs/test-evidence/canvas-red.png)

Green — after writing `fetch_assignments` (and a later regression test normalizing Canvas `...Z` timestamps to naive UTC — six tests):

![Canvas fetch tests passing — six green passes](docs/test-evidence/canvas-green.png)

**Layer 3 — AI breakdown (Groq mocked at the httpx transport)**

Red — eight behaviors written against an empty stub: context assembly, dropping missing fields, the four-section system prompt, returning markdown, timeout → 504, clean non-timeout error, and no API key in any log line:

![AI breakdown tests failing — eight red failures](docs/test-evidence/ai-red.png)

Green — after writing `generate_breakdown` and the `/breakdown` endpoint:

![AI breakdown tests passing — eight green passes](docs/test-evidence/ai-green.png)

**Layer 4 — ORM integration (real Neon test branch)**

These run against a live Postgres branch, so they need `TEST_DATABASE_URL` set in `.env` (and skip cleanly without it). Six behaviors: a user owns many connections, a connection owns many assignments, deleting a connection cascades to its assignments, stored detail round-trips field-for-field, the access token is **encrypted at rest** (raw column is ciphertext), and the report query groups by status and sorts by due date.

Red — the ORM modules don't exist yet (an honest feature-missing red rather than a faked stub schema):

![ORM tests failing — collection error, modules missing](docs/test-evidence/models-red.png)

Green — after writing the models, engine, encrypted-token column, and report query:

![ORM tests passing — six green passes](docs/test-evidence/models-green.png)

**Layer 5 — end-to-end (the full user flow)**

Drives the whole product: sign up, log in, add a connection, view the grouped report, open a stored detail page, and press "Break this down" for the AI breakdown. Plus two guardrails — the detail page renders from stored data with **no live Canvas call**, and a logged-out user is **blocked from the report** (and from another user's assignment). Runs against the Neon test branch; skips in CI without it.

Red — the web app and auth module don't exist yet:

![E2E tests failing — collection error, modules missing](docs/test-evidence/e2e-red.png)

Green — after writing the FastAPI app, auth, sessions, and the Jinja2 pages:

![E2E tests passing — eight green passes](docs/test-evidence/e2e-green.png)

**Layer 6 — daily sync job (Canvas mocked, stored to the Neon test branch)**

The pre-fetch that fills storage: for every connection, list its active courses, fetch each course's assignments, and store full detail — so detail pages later read from storage with no live call. Four behaviors: course pagination, storing across a connection's courses, idempotent **upsert** (re-runs update, never duplicate), and one path that covers one connection or many.

Red — the sync module and `fetch_courses` don't exist yet:

![Daily sync tests failing — collection error, modules missing](docs/test-evidence/sync-red.png)

Green — after writing `fetch_courses`, `sync_connection`, and `run_daily_sync`:

![Daily sync tests passing — four green passes](docs/test-evidence/sync-green.png)

**Layer 7 — daily email (SMTP mocked)**

One plain-text email per user, merging every assignment across all their connections — grouped Past due / Due today / Upcoming, sorted by due date, each item labeled by its connection. Summary only (names, dates, labels), so **the access token never appears in the body**. SMTP is injected and faked in tests — no mail is actually sent.

Red — the mailer module doesn't exist yet:

![Daily email tests failing — collection error, module missing](docs/test-evidence/mailer-red.png)

Green — after writing `build_report_email`, `send_email`, and `send_daily_reports`:

![Daily email tests passing — four green passes](docs/test-evidence/mailer-green.png)

**Layer 8 — HTMX breakdown swap (Groq mocked)**

The AI breakdown swaps in place on the detail page instead of navigating to a separate page. An HTMX request (the `HX-Request` header) to the breakdown route returns just the result fragment — success markdown, or the timeout/error notice — with no site chrome; a normal POST still returns the full page, so the existing click-through keeps working. Groq is mocked; the AI logic is untouched.

Red — the route still returns the full page even for an HTMX request:

![HTMX breakdown tests failing — three red failures, one pass](docs/test-evidence/htmx-red.png)

Green — after splitting the fragment out and returning it on `HX-Request`:

![HTMX breakdown tests passing — four green passes](docs/test-evidence/htmx-green.png)

**Layer 9 — accounts list + sync stamping (Canvas mocked, Neon test branch)**

The settings screen as a real accounts list, and the sync stamp it surfaces. Six behaviors: a successful sync **stamps `last_synced_at`** (what the list shows); the settings screen **lists accounts**; an **empty state** when there are none; a **"Sync failed" badge** when a connection's last sync errored; **removing** a connection cascades to its assignments; and a user **cannot remove another user's** connection. Canvas is mocked at the httpx boundary; the web flow runs through a real TestClient against the Neon test branch. (Adding a connection and its background sync is Layer 10.)

Red — the accounts feature doesn't exist yet, so the test module can't even import:

![Accounts-list tests failing — collection error, feature missing](docs/test-evidence/autosync-red.png)

Green — after writing the accounts list, the `last_synced_at` stamp, the sync-failed badge, and the remove route:

![Accounts-list tests passing — six green passes](docs/test-evidence/autosync-green.png)

**Layer 10 — background sync + account setup page (Canvas mocked, Neon test branch)**

Adding a connection no longer freezes on a synchronous fetch: the connection is saved, the sync runs in a **background task**, and you land on a **setup page** that polls until it's done. Nine behaviors: a new connection defaults to **`pending`**; the background sync **stores assignments and marks `ok`**; a failed background sync **keeps the connection and marks `error`** (token-free log); adding a connection **redirects to the setup page**; assignments **appear on the dashboard once the background sync finishes**; a failed add still **persists with `error`**; the **status endpoint** reports the state as JSON; the **setup page renders, then redirects** once status is `ok`; and a user **cannot view another user's** setup page or status. Canvas is mocked at the httpx boundary; the flow runs through a real TestClient (which runs background tasks) against the Neon test branch.

Red — the background sync, setup page, and status endpoint don't exist yet, so the test module can't even import:

![Setup-flow tests failing — collection error, feature missing](docs/test-evidence/setup-red.png)

Green — after writing the background sync, the `sync_status` column, the setup page + poller, and the status endpoint:

![Setup-flow tests passing — nine green passes](docs/test-evidence/setup-green.png)

**Layer 11 — completed work in its own section (Neon test branch + TestClient)**

Done work no longer clutters the urgency columns. `report_for_user` routes anything **submitted, graded, or excused** into its own `completed` bucket (a **missing** past-due item is *not* done and stays in Past due). The dashboard shows completed work in a collapsible **`<details>` "Completed (N)"** section — read-only, **no detail page and no AI breakdown** — and the daily email drops completed work (and stops counting it in the subject total). Eight behaviors: submitted/graded/excused → `completed`; missing stays `past_due`; not-done due-today stays put; the completed item is **separated from the board and carries no `/assignments/{id}` link**; the disclosure shows the right count; no disclosure when nothing's done; and the email excludes completed from body and total.

Red — the `completed` bucket, the dashboard disclosure, and the email fix don't exist yet, so the bucketing assertions hit `KeyError: 'completed'`:

![Completed-section tests failing — feature missing](docs/test-evidence/completed-red.png)

Green — after adding the `completed` bucket, the dashboard disclosure, and the email total fix:

![Completed-section tests passing — eight green passes](docs/test-evidence/completed-green.png)

**Layer 12 — redesigned AI breakdown: structured sections + modal cards**

The "Break this down with AI" trigger moves to the top of the detail page beside "Open in Canvas". Groq is now asked for a **JSON object** with four sections — **What's being asked / Where to start researching / Outline of the work / Ideas & angles** — capped under 500 words, with the research section forbidden from inventing citations or URLs. The result renders as **styled cards in a modal** (no more raw markdown scrolled to the bottom), with a **copy-to-clipboard** button for pasting into a document. Bad JSON from the model degrades to the same clean error path as a timeout. Model text lands only in auto-escaped Jinja nodes, so no raw HTML reaches the page.

Red — `build_section_messages`, `generate_sections`, `SECTION_KEYS`, and the card/dialog markup don't exist yet, so the layer can't even import:

![Breakdown redesign tests failing — feature missing](docs/test-evidence/breakdown-red.png)

Green — after adding JSON-mode generation, parsing into four sections, and the card/dialog rendering:

![Breakdown redesign tests passing](docs/test-evidence/breakdown-green.png)

How these are made: `python tools/run_to_html.py <label> <pytest target>` runs pytest with color forced on and renders the output to a terminal-styled HTML page; a headless browser screenshots that page to a PNG. Same command for every layer, so red and green get documented as we go.

## Environment variables

All config comes from the environment — nothing is hardcoded. In local dev these are read from a `.env` file (loaded automatically); in production they're injected by the host. `.env` is never committed.

| Variable | Required | What it does |
| --- | --- | --- |
| `DATABASE_URL` | **Yes** | Connection string for the app's own Postgres (Neon). Used by the web app and both daily jobs. The jobs refuse to run if it's unset. Local dev may point this at a SQLite file instead. |
| `TOKEN_ENCRYPTION_KEY` | **Yes** | Fernet key that encrypts/decrypts Canvas access tokens at rest. Tokens are only ever stored as ciphertext; without this key they can't be read or written. |
| `GROQ_API_KEY` | **Yes** | Key for the Groq / Llama 3.3 service that generates the on-demand AI study breakdown. Read when the button is pressed — never logged or committed. |
| `SESSION_SECRET` | Prod | Signs login session cookies. Falls back to an insecure dev default if unset — must be a real secret in production. |
| `SMTP_HOST` | Email job | Mail server hostname. The email job refuses to run without it. |
| `SMTP_PORT` | No | Mail server port. Defaults to `587`. |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | Email job | Mail server login. If both are set the job authenticates; if absent it connects without auth. |
| `SMTP_FROM` | No | "From" address on the daily email. Falls back to `SMTP_USERNAME`. |
| `TEST_DATABASE_URL` | Tests | Points the integration/E2E tests at a throwaway Postgres branch — never production. Those tests skip cleanly when it's unset. |

## Running the daily jobs

Two cron entry points wire the tested cores to real resources (DB session, HTTP client, SMTP). They read config from the environment and run nothing locally by accident — `DATABASE_URL` must point at production, not the test branch.

```
python -m app.jobs sync    # fetch + store every connection's assignments
python -m app.jobs email   # send each user their grouped daily report
```

Example crontab (UTC):

```
0 6 * * *  cd /app && python -m app.jobs sync
0 7 * * *  cd /app && python -m app.jobs email
```

The email job also needs `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM`.

## Security

- Access tokens are **encrypted at rest** — never stored, printed, or logged in plaintext.
- The Groq key is read from `GROQ_API_KEY` in the environment — never hardcoded, never logged, never committed.
- Canvas `description` HTML is sanitized before it touches a page or email.
- `.env`, tokens, and secrets are never committed.

## Project docs

- **[SPRINT.md](SPRINT.md)** — the Phase 2 sprint spec: goal, user stories, acceptance criteria, specifications, TDD plan, build order, and definition of done.
- **[CLAUDE.md](CLAUDE.md)** — context and working rules for this repo.

---

**Owner:** Diana Busch · Next Chapter Cohort, Phase 2
