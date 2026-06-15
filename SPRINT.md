# Canvas Daily — Phase 2 Sprint Spec

> Early-warning layer on top of Canvas. One person, many Canvas connections, one daily report. Built test-first.

**Owner:** Diana Busch · Next Chapter Cohort, Phase 2
**Stack:** FastAPI · SQLModel · Neon (Postgres) · Jinja2 · pytest · cron + SMTP
**Two APIs:** Canvas LMS (third-party data) + Groq / Llama 3.3 70B (AI study breakdown)
**Sprint window:** this week

---

## 1. Sprint Goal

Ship a working full-stack Canvas Daily that lets one user connect two or more Canvas accounts across different institutions and receive one correct, correctly-labeled daily report — Past due / Due today / Upcoming — with every item linking to an instant, app-rendered detail page that can generate an AI study breakdown on demand. Built TDD-first, covering pure functions, both API layers, and the ORM path.

Two APIs carry the product: **Canvas** pulls the assignment data; **Groq / Llama 3.3 70B** turns a single assignment into an actionable study plan. The detail page is where they meet — stored Canvas detail on the page, one button to get the AI breakdown.

The slice that proves it's full-stack and not a script that emails: the **silver-platter detail page**. It needs persisted data, real server-rendered pages, the ORM tying them together, and the AI breakdown wired in.

---

## 2. User Stories

### Accounts & connections
1. As a new user, I can sign up and log in so my Canvas connections are private to me.
2. As a user, I can add a Canvas connection with a label, institution base URL, account type (student or observer), and access token.
3. As a parent, I can add more than one connection — including across different Canvas institutions — and have them all feed one report.
4. As a user, I can see and remove the connections I've added.

### The daily report
5. As a user, I receive one daily email merging every assignment across all my connections, sorted by due date and labeled by which connection it came from.
6. As a user, the report groups items into Past due, Due today, and Upcoming so one glance shows what needs attention.
7. As a parent, each item is clearly attributed to the right kid, and I can scan for late and missing flags first.

### The silver-platter detail page
8. As a user, I can click any report item and land on an app-rendered detail page in one click.
9. As a user, that page shows full instructions, submission type, points possible, due date, and current status.
10. As a user, the detail page loads instantly because detail was pre-fetched and stored on the last daily run.

### AI study breakdown
11. As a user, I can press one button on the detail page to get an AI study breakdown of that assignment.
12. As a user, the breakdown gives me what's being asked, a step-by-step plan, what to watch out for, and a time estimate — so I can start the work, not just read about it.
13. As a user, if the AI is slow or fails, I see a clear message instead of a broken page.

### Defensible build
14. As a reviewer, I can walk the data model, both API integrations, the pre-fetch decision, and the tests and have each part defended.

---

## 3. Acceptance Criteria

Tied to the stories above.

**Auth (1)**
- A user can sign up and log in; sessions persist.
- A logged-out user cannot view another user's connections or report.

**Add connection (2)**
- The form captures label, base URL, account type, and token.
- The token is encrypted at rest, never stored or logged in plaintext.
- Base URL lives on the connection, not as a global config.

**Multiple connections (3)**
- A user with two connections on different institution domains gets both merged into one report.
- A user with one connection and a user with four run through the exact same code path.

**Manage connections (4)**
- Added connections are listed with their labels.
- Removing a connection stops its items from appearing in the next report.

**Daily report (5, 6, 7)**
- Items from all connections appear in one email, sorted by due date.
- Each item shows its connection label.
- Items bucket correctly: Past due (due date before now), Due today (due within today), Upcoming (future).
- Status reflects the right person per connection (student token → that student, observer token → that child).
- Late and missing flags are visible on flagged items.

**Detail page (8, 9, 10)**
- Every report item links to a detail page in one click.
- The page renders instructions, submission type, points possible, due date, and status.
- The page reads from stored data — no live Canvas call on click.
- HTML descriptions are sanitized before rendering.
- Ungraded work (null score) renders cleanly, not as blank or zero.

**AI breakdown (11, 12, 13)**
- A "Break this down" button on the detail page calls the AI endpoint with the assignment's title, description, points, due date, and course.
- The response comes back in four sections: What's being asked, Step-by-step plan, Watch out for, Time estimate.
- The response is capped (under ~300 words) and rendered as markdown.
- A timeout returns a clear "took too long" message; any other failure returns a clear error — never a broken page.
- The Groq API key is read from the environment, never hardcoded or logged.

**Defensible (14)**
- Test suite is built test-first and covers pure functions, both API layers (mocked), and ORM integration against a Neon test branch.

---

## 4. Specifications

### Data model
One-to-many: a **user** owns many **connections**; each connection owns many stored **assignments**.

**User** — id, email, password hash, created_at.

**Connection** — id, user_id (FK), label, base_url, account_type (`student` | `observer`), access_token (encrypted at rest), created_at.

**Assignment** (stored detail) — id, connection_id (FK), canvas_assignment_id, name, description (sanitized HTML), due_at, points_possible, submission_types, html_url, workflow_state (`unsubmitted` | `submitted` | `graded`), score (nullable), submitted_at (nullable), late (bool), missing (bool), excused (bool), fetched_at.

### Canvas fetch
- Endpoint: assignments per connection, with `include[]=submission` to fold the token-user's own submission state into the same call — no second loop.
- Follow `Link` header pagination; never take only the first page.
- The included submission reflects the token's own user, so each connection naturally reports the right person.
- Sanitize the raw HTML `description` before storing.

### Pre-fetch decision
The daily job pre-fetches full detail for every assignment and stores it. Detail pages then read from storage — instant load, no live Canvas call on click. Heavier daily write in exchange for an instant, reliable click. Stored detail reflects the last daily run, which is the correct freshness for a once-a-day product.

### AI study breakdown (second API)
On-demand, not pre-fetched — the user presses a button on the detail page when they want it.

- **Provider:** Groq, using the OpenAI-compatible API (`base_url = https://api.groq.com/openai/v1`), model `llama-3.3-70b-versatile`, temperature `0.5`.
- **Key:** read from `GROQ_API_KEY` in the environment. Never hardcoded, never logged.
- **Input:** the endpoint receives the assignment's title, description, points, due date, and course name, assembled into a context block. Missing fields are dropped, not sent blank.
- **System prompt:** instructs a fixed four-section markdown format — *What's being asked*, *Step-by-step plan* (concrete, specific steps), *Watch out for* (grading gotchas), *Time estimate* (phased) — capped under 300 words, direct tone.
- **Output:** markdown, rendered on the page.
- **Errors:** a timeout returns a 504 with a "took too long" message; any other failure returns a clear error. The page shows the message, never breaks.

This is the AI half of the two-API requirement. Canvas supplies the raw assignment; Groq turns it into a plan the student can act on.

### Report
- Reads from storage, groups by status (Past due / Due today / Upcoming), sorts by due date within each group.
- Each item carries its connection label.
- Sent once daily via SMTP; scheduled with cron.

### Pages (Jinja2, server-rendered)
- Sign up / log in
- Connection management (add / list / remove)
- The grouped report view
- One detail page per stored assignment

---

## 5. TDD Plan — Prompts for Writing Tests

Red, green, refactor. pytest as the runner. Inside-out. Each block below is a prompt to drive the test first.

### Layer 1 — Pure functions (no mocking)
> Write pytest tests for the date/bucketing helpers. Given a due date and a fixed "now": parse and format the due date; compute days overdue; compute hours until due; classify into past-due, due-today, or upcoming. Cover the boundaries — due exactly now, due at the last second of today, due at midnight tomorrow. Known input, known output, no mocks.

### Layer 2 — Canvas fetch (Canvas API mocked)
> Write pytest tests for the Canvas fetch with the API mocked. Assert it follows `Link` header pagination across multiple pages and assembles all assignments, not just page one. Assert `include[]=submission` data is parsed into workflow_state, score, submitted_at, and the late/missing/excused flags. Assert the HTML description is sanitized. Assert a null score is preserved as null, not coerced to zero. No real network calls.

### Layer 3 — AI breakdown (Groq mocked)
> Write pytest tests for the AI breakdown endpoint with the Groq client mocked. Assert it assembles the context block from title, description, points, due date, and course, and drops missing fields rather than sending them blank. Assert it sends the system prompt and returns the model's markdown content. Assert a timeout returns a 504 with a clear message, and any other failure returns a clean error — never an unhandled exception. Assert no real API call and no key in any log line.

### Layer 4 — ORM integration (real Neon test branch)
> Write pytest integration tests against a Neon test branch. Assert a user can own many connections; a connection can own many stored assignments; deleting a connection cascades or stops its assignments from surfacing. Assert stored assignment detail round-trips — write it, read it back, fields match. Assert the report query groups by status and sorts by due date.

### Layer 5 — End-to-end (rendered pages)
> Write an E2E test for the user flow: sign up, add a connection, view the report, click into a detail page, press "Break this down" and see a rendered breakdown. Assert the detail page renders Canvas detail from stored data with no live Canvas call. Assert a logged-out user is blocked from another user's report.

---

## 6. Build Order

1. Stand up FastAPI + pytest. Re-derive the pure date/bucketing functions test-first.
2. Model the schema on Neon — users, connections, stored assignments. Auth in. Migrate existing tokens in as the first connections.
3. Build sign up, log in, and connection management (add own / add kids' / list / remove).
4. Refactor the fetch to loop over a user's connections, enrich with `include[]=submission`, follow pagination, sanitize description. Test against mocked Canvas.
5. Build the daily job: pre-fetch full detail for every assignment, store tagged by connection label.
6. Render the grouped report and the one-click detail pages from storage. Verify end-to-end.
7. Build the AI breakdown endpoint (Groq / Llama 3.3 70B) test-first, wire the "Break this down" button into the detail page.
8. Wire scheduled daily email. Recruit a few early users for feedback.

---

## 7. Risks & Open Questions

- **Canvas API access depends on the institution.** Some schools restrict developer keys or observer scope. Confirm a typical user can connect without IT involvement before investing in acquisition — that's the real adoption bottleneck.
- **Base URL per connection**, not global — institutions are on different Canvas domains.
- **Token security** — encrypt at rest, scope carefully, never log.

---

## 8. Definition of Done

- A user can sign up, add two or more Canvas accounts across different institutions, and receive one correct, correctly-labeled daily report.
- The report groups into Past due / Due today / Upcoming and shows the right person's status per connection.
- Any assignment opens an instant app-rendered detail page in one click, with instructions, submission type, points, due date, and status.
- The detail page can generate an AI study breakdown (Groq / Llama 3.3 70B) in the four-section format, with errors handled cleanly.
- The test suite is built test-first and covers the pure functions, both API layers, and the ORM integration path.
- Every part of the data model, both API integrations, the pre-fetch decision, and the tests can be defended in a walkthrough.
