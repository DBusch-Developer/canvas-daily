# Class on the detail page — design (Layer 17)

Date: 2026-06-18

## Problem

The dashboard card now shows the class (course code), but the assignment **detail
page** does not — so when you open an assignment you can't see which class it
belongs to.

## Goal

Show the class on the detail page, using the `course_code` already stored on the
assignment.

## Non-goals

- No model, fetch, or sync change — `Assignment.course_code` already exists and is
  populated by the sync.
- No code shortening — show the stored `course_code` as-is.

## Placement

The detail page already shows **Connection** as a metacard in the meta grid
(Due date / Connection / Points / Score / Submission / Submitted). Add a **"Class"
metacard** to that grid, right after the Connection metacard, following the same
markup and the existing conditional pattern (Points/Score render only when set).

```html
<li class="metacard">
  <span class="metacard__label">Connection</span>
  <span class="metacard__value">{{ a.connection.label }}</span>
</li>
{% if a.course_code %}
  <li class="metacard">
    <span class="metacard__label">Class</span>
    <span class="metacard__value">{{ a.course_code }}</span>
  </li>
{% endif %}
```

- Rendered **only when `course_code` is set** (omitted otherwise, like the other
  conditional metacards).
- No CSS change — reuses `.metacard`.

## Components touched

- `app/templates/detail.html` — add the Class metacard.
- `tests/test_detailclass.py` — new enforced layer (label `detailclass`).
- `README.md` — new "Layer 17" test-evidence section.
- `docs/test-evidence/detailclass-red.png`, `detailclass-green.png`.

## Test plan — TDD, Layer 17

New file `tests/test_detailclass.py` (label `detailclass`, so screenshots are
`detailclass-red.png` / `detailclass-green.png`). One red + one green for the
layer; red captured live before any implementation. Uses the FastAPI TestClient
against in-memory SQLite (as in the other web layers), so it runs without a Neon
branch.

Tests:

- **Shows class:** the detail page of an assignment with `course_code` set shows a
  "Class" label and the code value.
- **Omits when blank:** an assignment with empty `course_code` renders no "Class"
  metacard.

TDD order (honoring CLAUDE.md test-evidence rules):

1. Write the failing tests first (the Class metacard doesn't exist yet).
2. Capture **red live, before any code**: `detailclass-red.png`.
3. Add the Layer 17 section to the README with the red screenshot.
4. Implement until green.
5. Capture **green**: `detailclass-green.png`, add to README.
6. Verify images by eye, run `check_evidence`, commit with the pre-commit hook,
   push.
