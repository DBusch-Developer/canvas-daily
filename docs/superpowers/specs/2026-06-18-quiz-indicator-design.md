# Quiz indicator — design (Layer 14)

Date: 2026-06-18

## Problem

Some Canvas assignments are quizzes. Canvas usually leaves the *assignment*
`description` blank for a quiz (the questions live on a separate quiz object), so
in Canvas Daily a quiz shows an empty Instructions panel with the generic
"No instructions provided" message — and nothing tells the student it's a quiz.

## Goal

Detect quizzes and label them clearly across all three work surfaces:

- the assignment **detail page**,
- the **dashboard** list, and
- the daily **email**.

## Non-goals

- No new Canvas fetch. The quiz signal is already stored.
- No fetching or rendering of quiz questions themselves (they aren't on the
  assignment object; the student takes the quiz in Canvas).
- No change to the AI breakdown behavior for quizzes.
- Only quizzes — not discussions, external tools, or other submission types.

## Detection — one source of truth

Canvas marks quiz assignments with `"online_quiz"` in `submission_types`, a field
already fetched in `app/canvas.py` and stored on every `Assignment`. Add a
computed property on the model:

```python
@property
def is_quiz(self) -> bool:
    return "online_quiz" in self.submission_types
```

It is pure (build an assignment, read the flag — no I/O), and because every
surface reads the same property, the detail page, dashboard, and email always
agree. No database migration is needed: `submission_types` is already populated
on existing rows.

## 1. Detail page (`app/templates/detail.html`)

- Add a **"Quiz" pill** to the header tags (`brief__tags`), next to the status
  badge.
- In the Instructions panel, when `a.is_quiz` **and** the description is empty,
  show a quiz-specific message instead of the generic empty state:
  *"This is a quiz. The questions aren't shown here — open it in Canvas to take
  it."*
- If a quiz *does* carry a description, render the description as today and still
  show the Quiz pill.

## 2. Dashboard (`app/templates/report.html`)

- Add a small **"Quiz" tag** to each card's `card__top`, alongside the status
  badge and course pill. Applies to both the active board cards and the
  completed-section cards.

## 3. Email (`app/mailer.py`)

- The email is plain text, one line per assignment:
  `  - [{label}] {name} — due {due_at}`.
- For a quiz, append a `(Quiz)` marker after the name:
  `  - [{label}] {name} (Quiz) — due {due_at}`.

## Styling

A `.tag--quiz` (or reuse an existing small-pill class) styled like the existing
`course-pill`, in `app/static/app.css`. No new layout — it sits inline with the
existing badge/pill row.

## Components touched

- `app/models.py` — add the `is_quiz` property to `Assignment`.
- `app/templates/detail.html` — Quiz pill + quiz instructions message.
- `app/templates/report.html` — Quiz tag on cards (active + completed).
- `app/mailer.py` — `(Quiz)` marker on quiz lines.
- `app/static/app.css` — small quiz-tag style.
- `tests/test_quiz.py` — new enforced layer (label `quiz`).
- `README.md` — new "Layer 14" test-evidence section.
- `docs/test-evidence/quiz-red.png`, `quiz-green.png`.

## Test plan — TDD, Layer 14

New file `tests/test_quiz.py` (label `quiz`, so screenshots are `quiz-red.png` /
`quiz-green.png`). One red + one green for the whole layer; red captured live
before any implementation.

Tests:

- **Property (pure):** an assignment with `submission_types=["online_quiz"]` has
  `is_quiz` true; one with `["online_upload"]` (or empty) has it false.
- **Detail page:** for a quiz with empty description, the page shows the "Quiz"
  pill and the quiz instructions message, and does *not* show the generic
  "No instructions provided". For a non-quiz, neither appears.
- **Detail page with description:** a quiz that has a description still shows the
  description and the Quiz pill (no quiz message).
- **Dashboard:** a quiz assignment renders a "Quiz" tag on its card; a non-quiz
  does not.
- **Email:** `build_report_email` includes `(Quiz)` on a quiz line and not on a
  non-quiz line.

TDD order (honoring CLAUDE.md test-evidence rules):

1. Write the failing tests first (the `is_quiz` property and the markup don't
   exist yet).
2. Capture **red live, before any code**: `quiz-red.png`.
3. Add the Layer 14 section to the README with the red screenshot.
4. Implement until green.
5. Capture **green**: `quiz-green.png`, add to README.
6. Verify images by eye, run `check_evidence`, commit with the pre-commit hook,
   push.
