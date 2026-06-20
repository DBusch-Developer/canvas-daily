# Branded HTML daily report email — design (Layer 22)

Date: 2026-06-20

## Problem

The daily report email is plain text — hard to scan, no branding, and the
assignment names aren't clickable. We want a designed HTML email with the logo,
clear sections, status pills, and each assignment linking into the app.

## Goal

Send the daily report as a branded **HTML email** (with a plain-text fallback),
where each assignment name links to its Canvas Daily detail page.

## Validated design (already previewed in real inboxes)

- **Header:** the real hosted logo (`/static/logo.png`) + the brand gradient rule.
  (Hosted, not embedded: clean in Gmail with no attachment; Outlook shows it after
  the recipient's one-time "show content" — unavoidable for any image, sender
  cannot override.)
- **Greeting:** today's date + "Here's your day — N things across your classes."
- **Three sections**, each with a colored count chip: **Past due** (red),
  **Due today** (coral), **Upcoming** (blue). Empty sections are omitted.
- **Each assignment is a card:** the **name links to
  `{base_url}/assignments/{id}`**, with the class code · local due time beneath, a
  **status pill** on the right (Missing / Late / Past due / Due today / Upcoming),
  and a small **Quiz** tag inline when `is_quiz`.
- **Upcoming is a flat list** (no week grouping — confirmed).
- **Footer:** "Open your dashboard →" button + a one-line AI-breakdown tip.
- **Plain-text fallback** is the existing `build_report_email` text, sent as the
  multipart alternative.

## Non-goals

- No week-grouping in the email.
- No embedded/inline logo (hosted only).
- No change to which assignments appear (still Past due / Due today / Upcoming;
  completed is still excluded, as today).

## Components

- `app/templates/report_email.html` — **new** Jinja template, email-safe inline
  styles, rendered with `buckets`, `base_url`, `total`, and `day`.
- `app/mailer.py`:
  - **new** `build_report_html(session, user, now, base_url)` → HTML string.
    Computes buckets via `report_for_user` (same as `build_report_email`) and
    renders the template. Status/pill logic mirrors the dashboard card.
  - `send_email(smtp, sender, recipient, subject, body, html=None)` — add the
    optional `html`; when given, attach it as the HTML alternative (multipart).
    Backward compatible (no `html` → plain text only, as today).
  - `send_daily_reports(session, smtp, sender, now, base_url=...)` — add
    `base_url` (default from config); build text + html and send both.
  - `build_report_email` (plain text) is unchanged — the existing `mailer` layer
    stays green.
- `app/config.py` (or reuse): a `public_base_url()` reading `PUBLIC_BASE_URL` env,
  default `https://canvas-daily.org`.
- `app/jobs.py` `run_email` passes the public base URL.
- `.github/workflows/daily-email.yml` — add `PUBLIC_BASE_URL` env (optional; the
  default already points at production).

## Rendering details

- Class code uses `assignment.course_short` (e.g. `CSA250`), falling back to the
  connection label when there's no code.
- Due time uses `assignment.due_display` (local time, from Layer 19).
- The template escapes assignment names (Jinja autoescape) — no raw HTML from
  Canvas reaches the email; and the access token is never referenced (the existing
  "no token in the email" test continues to hold for the text part, and the HTML
  test asserts it too).

## Cleanup

Remove the throwaway preview artifacts created while iterating: `_send_test_email.py`,
`_send_real_email.py`, `.github/workflows/test-email.yml`, and `docs/email-mockup.html`.

## Test plan — TDD, Layer 22

New file `tests/test_reportemail.py` (label `reportemail`). Uses in-memory SQLite +
the FastAPI TestClient is not needed; it calls `build_report_html` directly with a
seeded session (in-memory engine), so it runs without a Neon branch.

Tests:

- **Sections + names:** with assignments in past due / due today / upcoming, the
  HTML contains each section heading and each assignment name.
- **Links:** each assignment name links to `{base_url}/assignments/{id}` (assert the
  `href` is present for a seeded id).
- **Status pill:** a missing past-due item shows "Missing"; a due-today item shows
  "Due today".
- **Quiz tag:** a quiz assignment renders a "Quiz" marker.
- **No token:** the rendered HTML never contains a connection's access token.
- **send_email html alternative:** `send_email(..., html=...)` produces a message
  whose HTML part contains the markup, while the plain part contains the text
  (multipart). Without `html`, behaviour is unchanged.

TDD order (honoring CLAUDE.md test-evidence rules):

1. Write the failing tests first (`build_report_html` / the `html=` param don't
   exist yet).
2. Capture **red live, before any code**: `reportemail-red.png`.
3. Add the Layer 22 section to the README with the red screenshot.
4. Implement until green.
5. Capture **green**: `reportemail-green.png`, add to README.
6. Verify by eye, run `check_evidence`, commit with the pre-commit hook, push. Then
   send one real report to confirm it looks right end-to-end.
