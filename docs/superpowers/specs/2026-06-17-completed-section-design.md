# Completed work in its own section

Date: 2026-06-17

## Problem

The dashboard's Past due column is full of **completed** assignments. Completion
is currently only a badge overlay — done work still sits in its date bucket. The
urgency columns should show only work that still needs doing. Completed work
belongs in its own section, hidden until the user wants to reflect on it, with no
AI breakdown and no detail page.

## Decisions

- **Completed = `submitted_at` is set, OR `workflow_state == "graded"`, OR
  `excused`.** A late-but-submitted item is completed (it's turned in); a
  **missing** item (past due, never turned in) is NOT completed and stays in Past
  due.
- **Completion is checked before date bucketing.** Completed items go to a new
  `completed` bucket regardless of due date; everything else is date-classified
  exactly as today. `classify_due` (Layer 1) is unchanged — completion is a
  separate predicate, not a date concern.
- **The Completed section is a collapsed `<details>` disclosure** on the
  dashboard, below the three columns. Pure HTML, no new route, no JS framework.
- **Completed rows are read-only:** name (plain text, not a link), course label,
  due date, a status chip (Complete / Graded / Excused), and an optional external
  "Open in Canvas ↗" link if `html_url` is present. **No internal detail-page
  link, no breakdown button.**
- **Completed work drops out of the daily email** (the email renders only the
  three date sections). The email's total count is fixed to exclude completed.

## Changes

### `app/reports.py`
- Add a predicate `_is_completed(assignment)` → `True` when
  `submitted_at is not None or workflow_state == "graded" or excused`.
- `report_for_user` returns
  `{"past_due": [...], "due_today": [...], "upcoming": [...], "completed": [...]}`.
  For each assignment (still filtered to `due_at is not None`, still ordered by
  `due_at`): if `_is_completed` → `completed`; else `classify_due(due_at, now)`.

### `app/templates/report.html`
- The three columns are unchanged structurally; they now contain only not-done
  items (automatic). The hero stats already sum only the three date buckets, so
  they remain correct.
- Add below the board:
  ```
  {% if buckets.completed %}
  <details class="completed">
    <summary>Completed ({{ buckets.completed | length }})</summary>
    <ul class="cards cards--muted">
      {% for a in buckets.completed %}
        <li class="card card--done">
          <div class="card__top">
            <span class="badge badge--done">
              {{ 'Excused' if a.excused else ('Graded' if a.workflow_state == 'graded' else 'Complete') }}
            </span>
            <span class="course-pill">{{ a.connection.label }}</span>
          </div>
          <h3 class="card__title">{{ a.name }}</h3>   {# plain text — no detail link #}
          <div class="card__foot">
            <span class="due">due {{ a.due_at }}</span>
            {% if a.html_url %}<a class="card__open" href="{{ a.html_url }}" target="_blank" rel="noopener">Open in Canvas ↗</a>{% endif %}
          </div>
        </li>
      {% endfor %}
    </ul>
  </details>
  {% endif %}
  ```
  No `/assignments/{{ a.id }}` link and no breakdown affordance for completed rows.

### `app/mailer.py`
- `build_report_email`: change `total` from summing all buckets to summing only
  the three date sections (`sum(len(buckets[k]) for k, _ in _SECTIONS)`), so the
  subject count never includes completed work. `_SECTIONS` already lists only the
  three date keys, so the body already excludes completed.

## Test plan (TDD — live red before each implementation), new Layer 11 → `tests/test_completed.py`

Integration tests against the Neon test branch (skip without `TEST_DATABASE_URL`),
plus TestClient rendering and a mailer check.

- **Bucketing (`report_for_user`):**
  - a **submitted** past-due assignment lands in `completed`, not `past_due`.
  - a **graded** assignment lands in `completed`.
  - an **excused** assignment lands in `completed`.
  - a **missing** past-due assignment (not submitted, not graded, not excused)
    stays in `past_due`.
  - a not-done assignment due today stays in `due_today`.
- **Dashboard (`GET /`):**
  - a completed assignment does **not** appear inside the Past due column and has
    **no** `/assignments/{id}` link or breakdown button.
  - the Completed disclosure renders with the right count and the completed item's
    name.
  - a not-done past-due assignment still appears in Past due **with** its detail
    link.
- **Email (`build_report_email`):** a completed assignment is absent from the body
  and the subject's total excludes it.

## Test evidence

New `completed-red.png` / `completed-green.png` and a "Layer 11 — completed work
in its own section" README block right after Layer 10 (description → red → green).
Red captured live before the code exists.

## Out of scope

- No change to `classify_due` (Layer 1).
- No detail page or AI breakdown for completed work.
- No new "Completed" route or nav tab (a disclosure on the dashboard).
- No re-sorting of completed items beyond the existing due-date order.
