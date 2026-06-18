# Class (course code) on the dashboard card — design (Layer 15)

Date: 2026-06-18

## Problem

A dashboard card shows the assignment name, status, and the **connection label**
(the account name the user typed when adding the connection, e.g. "Diana -
Yavapai College"). It does not show which **class** the assignment belongs to, so
a card doesn't tell you what course it's for.

## Goal

Show the class on each dashboard card as a **short course code** (e.g. "BIO 101"),
while keeping the existing connection-label pill.

## Non-goals

- No extra Canvas calls — the sync job already fetches courses.
- Not the full course name, and not a name fallback — just the code (when Canvas
  provides one).
- Card only — not the detail page or the daily email (can be added later).

## Data — where the code comes from

The sync job (`app/sync.py`) already walks each connection's courses via
`fetch_courses`, then fetches assignments per course. The course code is
available there; it just isn't stored on the assignment.

- `app/canvas.py` `fetch_courses` currently returns `{"id", "name"}` per course.
  Add `"code": c.get("course_code")` (Canvas exposes `course_code` on the course
  object).
- `app/models.py` `Assignment` gains `course_code: str = ""`.
- `app/sync.py` `sync_connection` has `course["code"]` in hand; thread it into
  `_upsert` so each stored/updated assignment carries its course code. When a
  course has no code, store an empty string.

## Card display (`app/templates/report.html`)

- Keep the connection-label pill in `card__top` unchanged.
- Add a small class line under the assignment title when a code exists:
  ```html
  <h3 class="card__title">…</h3>
  {% if a.course_code %}<p class="card__class">{{ a.course_code }}</p>{% endif %}
  ```
  Styled muted/small via a new `.card__class` rule in `app/static/app.css`.
- Applies to both the active-board cards and the completed-section cards.
- If `course_code` is empty, the line is omitted (never a blank line).

## Backfill and migration (real-world catch)

- `course_code` is empty on existing rows until the **next daily sync**
  re-populates assignments. It will not appear retroactively before a sync runs.
- SQLModel's `create_all` only creates missing *tables*, not new *columns* on an
  existing table. On a live database the `assignments` table needs the column
  added once:
  ```sql
  ALTER TABLE assignments ADD COLUMN course_code VARCHAR NOT NULL DEFAULT '';
  ```
  Tests use a fresh in-memory SQLite database created with `create_all`, so they
  are unaffected. The implementation plan includes this migration step.

## Components touched

- `app/canvas.py` — `fetch_courses` returns the course code.
- `app/models.py` — `Assignment.course_code`.
- `app/sync.py` — store `course_code` during upsert.
- `app/templates/report.html` — class line on active + completed cards.
- `app/static/app.css` — `.card__class` style.
- `tests/test_coursecode.py` — new enforced layer (label `coursecode`).
- `README.md` — new "Layer 15" test-evidence section.
- `docs/test-evidence/coursecode-red.png`, `coursecode-green.png`.

## Test plan — TDD, Layer 15

New file `tests/test_coursecode.py` (label `coursecode`, so screenshots are
`coursecode-red.png` / `coursecode-green.png`). One red + one green for the whole
layer; red captured live before any implementation. Canvas is mocked at the httpx
transport boundary, and the card test uses the FastAPI TestClient against
in-memory SQLite (as in the existing web layers).

Tests:

- **Fetch:** `fetch_courses` includes the `code` from a course's `course_code`
  field; a course missing `course_code` yields `code` of `None`/empty.
- **Sync:** after `sync_connection` (Canvas mocked), the stored assignment has
  `course_code` set to its course's code.
- **Card renders code:** a dashboard with an assignment whose `course_code` is set
  shows that code in a `card__class` element.
- **Card omits when empty:** an assignment with empty `course_code` renders no
  `card__class` element (no blank line).

TDD order (honoring CLAUDE.md test-evidence rules):

1. Write the failing tests first (the field, fetch key, sync write, and markup
   don't exist yet).
2. Capture **red live, before any code**: `coursecode-red.png`.
3. Add the Layer 15 section to the README with the red screenshot.
4. Implement until green.
5. Capture **green**: `coursecode-green.png`, add to README.
6. Verify images by eye, run `check_evidence`, commit with the pre-commit hook,
   push.
