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

How these are made: `python tools/run_to_html.py <label> <pytest target>` runs pytest with color forced on and renders the output to a terminal-styled HTML page; a headless browser screenshots that page to a PNG. Same command for every layer, so red and green get documented as we go.

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
