# Group the Upcoming column by week — design (Layer 18)

Date: 2026-06-18

## Problem

The dashboard is a three-column board (Past due / Due today / Upcoming) with the
**Completed** section below it. The board's height is driven by its tallest
column, and **Upcoming** can hold many weeks of assignments — so the board gets
very long and you have to scroll past the entire Upcoming list to reach
Completed. Not user-friendly.

## Goal

Break the **Upcoming** column into collapsible **week groups** so the column
stays short and Completed is reachable without a long scroll.

## Non-goals

- Past due and Due today columns are unchanged.
- No real pagination / page reloads — collapsing uses the same `<details>`
  pattern as the Completed section.
- No change to bucketing (`report_for_user`) — only the upcoming list's
  presentation changes.

## Weeks — a pure grouping function (`app/dates.py`)

```python
def group_by_week(assignments, now):
    """Group upcoming assignments into Monday-start calendar weeks.

    `assignments` is already sorted by due date. Returns an ordered list of
    {"label": str, "items": [...]}, one entry per week that has assignments, in
    chronological order. Weeks with no assignments are skipped.
    """
```

- A week is keyed by its **Monday**: `due.date() - timedelta(days=due.weekday())`.
- Labels, relative to the Monday of `now`'s week:
  - same week → **"This week"**
  - the following week → **"Next week"**
  - any later week → **"Week of Jun 30"** (`f"Week of {monday:%b} {monday.day}"`,
    which avoids the platform-specific `%-d`).
- Because the input is sorted by due date, inserting into an order-preserving
  dict keyed by Monday yields the weeks in chronological order; only weeks that
  actually have items appear.
- Pure (assignment objects in, label/items groups out) — unit-tested without a
  database.

## Wiring (`app/web.py` report route)

The `report` route builds the buckets as today, then also computes the grouped
upcoming and passes it to the template:

```python
buckets = report_for_user(session, user.id, _now())
upcoming_weeks = group_by_week(buckets["upcoming"], _now())
return TEMPLATES.TemplateResponse(
    request, "report.html", {"buckets": buckets, "upcoming_weeks": upcoming_weeks})
```

## Dashboard (`app/templates/report.html`)

The board still loops the three columns. For the **upcoming** column only, render
each week group as a collapsible disclosure instead of one flat card list:

- Each week is a `<details>` whose `<summary>` shows the label and count, e.g.
  **"Next week (8)"**.
- The **first** week group is `open`; the rest are collapsed.
- Inside each `<details>` is the same `<ul class="cards">` of cards used today
  (same card markup — status, course code, due, open link).
- The Past due and Due today columns keep rendering their flat card lists exactly
  as now. The empty-state ("All clear") still shows when there is no upcoming
  work (i.e. `upcoming_weeks` is empty).

A small CSS rule styles the week disclosures within the column (reusing existing
tokens; no new layout).

## Components touched

- `app/dates.py` — add `group_by_week` (and `timedelta` import).
- `app/web.py` — pass `upcoming_weeks` to the template.
- `app/templates/report.html` — render the upcoming column as weekly disclosures.
- `app/static/app.css` — small style for the week disclosures.
- `tests/test_upcomingweeks.py` — new enforced layer (label `upcomingweeks`).
- `README.md` — new "Layer 18" test-evidence section.
- `docs/test-evidence/upcomingweeks-red.png`, `upcomingweeks-green.png`.

## Test plan — TDD, Layer 18

New file `tests/test_upcomingweeks.py` (label `upcomingweeks`). One red + one
green for the layer; red captured live before any implementation. The grouping
tests are pure; the dashboard test uses the FastAPI TestClient against in-memory
SQLite.

Tests:

- **Empty input** → `group_by_week([], now)` is `[]`.
- **Labels:** an assignment due this week → group label "This week"; one due the
  following week → "Next week"; one two-plus weeks out → "Week of <Mon date>".
- **Only non-empty weeks:** assignments this week and three weeks out (nothing in
  between) produce exactly two groups (no empty middle weeks).
- **Order and membership:** groups are in chronological order and each item lands
  in its week.
- **Dashboard renders weekly disclosures:** with several upcoming assignments
  across weeks, the dashboard shows `<details>` groups with the week labels, the
  first one `open`; Past due / Due today still render flat.

TDD order (honoring CLAUDE.md test-evidence rules):

1. Write the failing tests first (`group_by_week` and the weekly markup don't
   exist yet).
2. Capture **red live, before any code**: `upcomingweeks-red.png`.
3. Add the Layer 18 section to the README with the red screenshot.
4. Implement until green.
5. Capture **green**: `upcomingweeks-green.png`, add to README.
6. Verify images by eye, run `check_evidence`, commit with the pre-commit hook,
   push.
