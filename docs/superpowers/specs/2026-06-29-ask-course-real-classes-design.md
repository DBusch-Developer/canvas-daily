# Ask My Course — real classes only + per-account selector

**Date:** 2026-06-29
**Status:** Approved (design), pending spec review
**Scope:** The `/ask` course picker only. The daily email/report is **not** touched.

## Problem

The Ask My Course picker (`/ask`) lists *every* course Canvas returns for a
connection, flat, across all of a user's connections at once. Two problems:

1. **Canvas junk.** Canvas exposes non-academic "courses" — clubs, help desks,
   admin/parent spaces. Real data on the live DB:
   - College (canvas.yc.edu, student): real classes all read `DEPT### Title (#####)`
     — e.g. `CSA250 Intro Artificial Intelligence (22255)`, `BIO181 General Biology I (22133)`.
     Junk has no code: `Phi Theta Kappa`, `Ruff's House`, `YC Honors - 25/26`,
     `Student Orientation To Online Learning v.3`.
   - Grade school (ensignpeakacademy, observer): the **real** class has **no code** —
     `English: Language Arts Companion Part 2 (Live 2025-2026)`. Junk:
     `Lunch Brunch 🍔🍟😝`, `Math Help ➕➖➗✖️`, `Student and Parent Center`.
   - **No single rule generalizes.** A "must have a course code" regex cleans up
     college but wrongly hides the one real grade-school class and can't sort its junk.

2. **No account disambiguation.** A user owns many connections (the live account has
   5, several duplicate yc.edu logins). The same class (`CSA214`) appears many times
   with no way to tell which account it came from.

Why the daily email is out of scope: the email is assignment-driven — it only shows
work actually due, so course shells with no assignments never appear there. The picker
is the only surface that lists courses regardless of assignments, so it's the only
place the junk shows up.

## Goals

- Show only real classes in the picker by default, **never deleting** anything.
- Put the user in **full control of the hidden pile**: a hidden-courses section they
  can pull anything back from with a Show button. No hide button on real classes —
  in practice users don't hide their own classes, and a wrongly-hidden real class is
  recoverable via Show. (Tradeoff: a junk course the AI wrongly leaves showing has no
  user control to tuck away; accepted as mild clutter, not data loss.)
- Disambiguate accounts with a dropdown that shows one account at a time.

## Non-goals

- Changing the daily email/report.
- Perfect automatic classification. The AI is a smart default, not the final word.
- Deleting or unenrolling any Canvas course.

## Approach

### 1. Classification — AI as a smart default, user as the authority

- A new `hidden` state on each course, three-valued:
  - **NULL** — never decided yet; needs classification.
  - **False** — shown (real class).
  - **True** — hidden (junk).
- **AI judgment** runs once per NULL course. Input per course: the **name** and
  **whether it has graded assignments**. The model judges "a class taken for a grade"
  vs "club / help / admin / social space" and returns keep (False) or hide (True).
  This sets the *initial* placement only.
- **User override always wins and sticks.** Pressing Show sets `hidden=False`
  explicitly. Re-syncs and re-loads never re-classify a course whose `hidden` is not
  NULL, so the user's choice is permanent. New courses from later syncs arrive NULL and
  get classified once.
- **Failure handling (per CLAUDE.md):** if the Groq call times out or errors, the
  undecided courses default to **visible** (show-all) for this view — never a broken
  or empty page. They stay NULL so a later successful load can still classify them.

### 2. The "has graded assignments" signal

- During `sync_connection`, we already fetch assignments per course in the loop.
  Record an assignment count (or boolean) onto the `Course` row at sync time so the
  classifier has a deterministic, already-stored signal — no extra Canvas calls.

### 3. When AI runs

- **On picker load**, not in the daily job. When `/ask` is opened, any NULL courses
  for the user are classified in **one batched Groq call** (list of {name,
  has_assignments} in → list of keep/hide out), with a timeout and the show-all
  fallback. Keeps the daily run AI-free and matches the "AI is on-demand" rule.

### 4. The picker UI (`/ask`)

- **Account dropdown** at the top selects one account at a time. Option labels show
  enough to tell duplicates apart (connection label + school host). Defaults to the
  user's first account. Selecting an account filters the lists below to it.
- **Real classes** for the selected account as the main list, each linking to its
  chat page as today. No per-class controls — just clean links.
- **"Hidden courses (N)"** collapsible disclosure at the bottom listing that account's
  hidden courses, each with a **Show** control that moves it into the real list.
  When the account has none hidden, the disclosure is omitted cleanly.
- Ownership-guarded throughout; the show action is a login- and ownership-checked
  POST that redirects back to the picker for the same account.

## Data model changes

- `Course.hidden: bool | None = None` — three-valued visibility (NULL/False/True).
- `Course.has_assignments: bool` (or an int count) — set at sync time. Default False.
- No change to `Connection`; its `label`/`base_url` drive the dropdown.

## Routes

- `GET /ask?account={connection_id}` — picker for the selected account (default first).
  Classifies NULL courses on load (batched, with fallback).
- `POST /courses/{course_id}/show` — set `hidden=False`, redirect back to `/ask?account=`.
  (No user-initiated hide route; hiding is set only by the AI classifier.)
- All guarded by the existing login + `_owned_course_or_404` pattern and the
  `ASK_COURSE_ENABLED` flag.

## Testing — new TDD layers (live red → green, own README sections)

- **Classifier layer** (`test_courseclassify.py`, Groq mocked):
  - name + has_assignments → keep/hide for the real cases (CSA250 keep, Lunch Brunch
    hide, English Language Arts keep, Student Orientation hide).
  - timeout/error → show-all fallback, courses stay NULL, no key in logs.
  - already-decided (non-NULL) courses are not re-classified.
- **Picker layer** (`test_askpicker.py`, in-memory SQLite + mocked Canvas/Groq):
  - account dropdown lists the user's connections; default account selected.
  - only the selected account's non-hidden courses show in the main list.
  - hidden courses appear under the "Hidden courses" disclosure, not the main list.
  - the Show POST sets `hidden=False`, is login- and ownership-guarded, redirects back.

## Open questions

- None blocking. Whether the assignment signal is a count or a boolean is an
  implementation detail decided in the plan.
