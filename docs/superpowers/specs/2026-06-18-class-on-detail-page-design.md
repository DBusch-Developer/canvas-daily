# Class label on detail page and card — design (Layer 17)

Date: 2026-06-18

## Problem

The stored `course_code` is verbose (e.g. `CSA250 Intro Artificial Intelligence
(22255)`), and the displays don't show the class well:

- The **detail page** shows the connection ("Diana") in two places — a pill under
  the assignment name and a "Connection" metacard — which is redundant, and the
  class isn't shown at all.
- The **dashboard card** shows the full verbose course string, including the
  trailing `(22255)` section number.

## Goal

Show the class cleanly, derived from the stored `course_code`:

- Detail page header pill → the **short class code** (e.g. `CSA250`).
- Dashboard card → the course string **without the trailing `(…)`**.

## Derived values — two model properties

On `Assignment` (pure, no I/O — both derive from `course_code`):

```python
@property
def course_short(self) -> str:
    """Leading code token, e.g. 'CSA250'. Empty when there is no code."""
    return self.course_code.split()[0] if self.course_code else ""

@property
def course_trimmed(self) -> str:
    """course_code without a trailing '(...)' section number."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", self.course_code).strip()
```

(`import re` goes at the top of `app/models.py`.)

- `"CSA250 Intro Artificial Intelligence (22255)"` → `course_short` = `"CSA250"`,
  `course_trimmed` = `"CSA250 Intro Artificial Intelligence"`.
- `""` → both `""`.

## Detail page (`app/templates/detail.html`)

- Replace the header pill that currently shows `a.connection.label` with
  `a.course_short`, falling back to the connection label when there is no code so
  the pill is never empty:
  ```html
  <span class="course-pill course-pill--lg">{{ a.course_short or a.connection.label }}</span>
  ```
- Keep the existing **"Connection" metacard** in the meta grid as the single
  connection display.
- Do **not** add a separate "Class" metacard — the class now lives in the header
  pill.

## Dashboard card (`app/templates/report.html`)

- The class line under the title shows `a.course_trimmed` instead of
  `a.course_code` (both the active-board and completed-section cards). The
  `{% if a.course_code %}` guard is unchanged (still hidden when there's no code).

## Non-goals

- No model column, fetch, or sync change — both properties derive from the
  existing `course_code`.
- No CSS change — reuses `.course-pill` and `.card__class`.

## Components touched

- `app/models.py` — add `course_short` and `course_trimmed` properties.
- `app/templates/detail.html` — header pill shows the short code.
- `app/templates/report.html` — card class line shows the trimmed value.
- `tests/test_classlabel.py` — new enforced layer (label `classlabel`).
- `README.md` — new "Layer 17" test-evidence section.
- `docs/test-evidence/classlabel-red.png`, `classlabel-green.png`.

## Test plan — TDD, Layer 17

New file `tests/test_classlabel.py` (label `classlabel`). One red + one green for
the layer; red captured live before any implementation. The property tests are
pure; the page tests use the FastAPI TestClient against in-memory SQLite, so the
layer runs without a Neon branch.

Tests:

- **`course_short` (pure):** `"CSA250 Intro Artificial Intelligence (22255)"` →
  `"CSA250"`; `""` → `""`.
- **`course_trimmed` (pure):** the same input → `"CSA250 Intro Artificial
  Intelligence"`; a value with no parenthetical is unchanged; `""` → `""`.
- **Detail header shows the short code:** the detail page of an assignment with a
  code shows `CSA250` and not the verbose remainder in the header pill.
- **Detail header falls back:** an assignment with no code shows the connection
  label in the pill (never empty).
- **Card shows trimmed:** the dashboard card shows the trimmed course string and
  not the trailing `(22255)`.

TDD order (honoring CLAUDE.md test-evidence rules):

1. Write the failing tests first (the properties and markup don't exist yet).
2. Capture **red live, before any code**: `classlabel-red.png`.
3. Add the Layer 17 section to the README with the red screenshot.
4. Implement until green.
5. Capture **green**: `classlabel-green.png`, add to README.
6. Verify images by eye, run `check_evidence`, commit with the pre-commit hook,
   push.
