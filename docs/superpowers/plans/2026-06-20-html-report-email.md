# Branded HTML Daily Report Email Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send the daily report as a branded HTML email (with plain-text fallback) where each assignment links to its Canvas Daily detail page.

**Architecture:** A new Jinja template `report_email.html` is rendered by a new `build_report_html` in `mailer.py` (the pill/status data is precomputed in Python so the template is pure rendering). `send_email` gains an optional `html` alternative; `send_daily_reports` sends both. The plain-text `build_report_email` is unchanged, so the existing `mailer` layer stays green.

**Tech Stack:** Jinja2 (standalone Environment in mailer.py), Python `email.message`, pytest with in-memory SQLite.

## Global Constraints

- TDD-first, red before green. New behavior is its own layer `tests/test_reportemail.py` (label `reportemail`).
- Evidence: `reportemail-red.png` / `reportemail-green.png`, both in README as **Layer 22**. Red captured live before any implementation. Both PNGs committed together at the end.
- Hosted logo only (no embed): `{base_url}/static/logo.png`. Flat Upcoming (no week grouping). Empty sections omitted.
- Assignment name links to `{base_url}/assignments/{id}`. Class code = `course_short` (fallback connection label). Due = `due_display`. Quiz tag when `is_quiz`.
- `build_report_email` (plain text) is unchanged. `send_email` and `send_daily_reports` stay backward-compatible (new args have defaults) so the existing `mailer` layer passes.
- Public URL from `PUBLIC_BASE_URL` env, default `https://canvas-daily.org`.
- Remove the throwaway preview artifacts (`_send_test_email.py`, `_send_real_email.py`, `.github/workflows/test-email.yml`, `docs/email-mockup.html`).
- Commit with the pre-commit hook (evidence + full suite). Short messages. Commit on `main`, no branches.
- Run pytest via the venv: `.venv/Scripts/python.exe -m pytest ...`.

---

## File Structure

- `tests/test_reportemail.py` — **create.** Layer 22 tests: HTML render + send_email html.
- `app/templates/report_email.html` — **create.** Email template (inline styles).
- `app/mailer.py` — **modify.** Jinja env, pills, `build_report_html`, `send_email(html=)`, `send_daily_reports(base_url=)`.
- `README.md` — **modify.** Add Layer 22.

---

## Task 1: Write the failing Layer 22 tests and capture RED

**Files:**
- Create: `tests/test_reportemail.py`
- Capture: `docs/test-evidence/reportemail-red.png`
- Modify: `README.md`

**Interfaces:**
- Consumes (do not exist yet): `build_report_html(session, user, now, base_url) -> str`; `send_email(smtp, sender, recipient, subject, body, html=None)`.
- Produces: the enforced `reportemail` layer.

- [ ] **Step 1: Write `tests/test_reportemail.py`**

```python
"""Layer 22 - branded HTML daily report email.

Renders the daily report as HTML: sections, assignment names linking to their
Canvas Daily detail pages, status pills, a Quiz tag, no access token. send_email
gains an optional HTML alternative. In-memory SQLite; no Neon needed.
"""

from datetime import datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.mailer import build_report_html, send_email
from app.models import Assignment, Connection, User

BASE = "https://cd.test"


def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


def seed(s, *, token="canvas-tok"):
    user = User(email="r@x.com", password_hash="h")
    s.add(user)
    s.commit()
    s.refresh(user)
    conn = Connection(user_id=user.id, label="Yavapai", base_url="https://school.test",
                      account_type="student", access_token=token)
    s.add(conn)
    s.commit()
    s.refresh(conn)

    def add(cid, name, due, **kw):
        s.add(Assignment(connection_id=conn.id, canvas_assignment_id=cid, name=name,
                         due_at=due, submission_types=kw.get("st", ["online_upload"]),
                         course_code=kw.get("code", "CSA250 Intro (1)"),
                         time_zone="America/Phoenix", html_url="https://school.test/a",
                         workflow_state="unsubmitted", missing=kw.get("missing", False)))
    # past due (missing), due-ish, and far upcoming + a quiz
    add(1, "Essay", datetime(2026, 6, 10, 6, 59), missing=True)
    add(2, "Pop Quiz", datetime(2030, 1, 1, 6, 59), st=["online_quiz"])
    s.commit()
    return user


NOW = datetime(2026, 6, 15, 19, 0)  # mid-day June 15 Arizona


def test_html_has_sections_and_linked_names():
    with Session(engine()) as s:
        user = seed(s)
        html = build_report_html(s, user, NOW, BASE)
    assert "Past due" in html
    assert "Upcoming" in html
    assert "Essay" in html
    assert "Pop Quiz" in html
    # name links to the in-app detail page
    assert 'href="https://cd.test/assignments/' in html


def test_html_shows_status_pill_and_quiz_tag():
    with Session(engine()) as s:
        user = seed(s)
        html = build_report_html(s, user, NOW, BASE)
    assert "Missing" in html       # the past-due item is missing
    assert ">Quiz<" in html        # the quiz tag


def test_html_never_contains_the_token():
    with Session(engine()) as s:
        user = seed(s, token="SUPER-SECRET-TOKEN")
        html = build_report_html(s, user, NOW, BASE)
    assert "SUPER-SECRET-TOKEN" not in html


def test_send_email_attaches_html_alternative():
    class FakeSMTP:
        def __init__(self):
            self.messages = []

        def send_message(self, msg):
            self.messages.append(msg)

    smtp = FakeSMTP()
    send_email(smtp, "from@cd.test", "to@x.com", "Subj", "plain body",
               html="<b>rich body</b>")
    msg = smtp.messages[0]
    assert msg.is_multipart()
    html_part = msg.get_body(preferencelist=("html",))
    assert "rich body" in html_part.get_content()
    text_part = msg.get_body(preferencelist=("plain",))
    assert "plain body" in text_part.get_content()
```

- [ ] **Step 2: Run the layer to confirm it is RED**

Run: `.venv/Scripts/python.exe tools/run_to_html.py reportemail-red tests/test_reportemail.py`
Expected: `[RED ...]`. Import fails on `build_report_html`; the send_email test fails because `html=` isn't accepted yet.

- [ ] **Step 3: Screenshot the red page**

Serve: `.venv/Scripts/python.exe -m http.server 8731 --directory docs/test-evidence` (background). Navigate to `http://127.0.0.1:8731/reportemail-red.html`, screenshot `.frame` to `reportemail-red.png`, move into `docs/test-evidence/`. Stop the server. Verify legible.

- [ ] **Step 4: Add the Layer 22 README section**

Insert after the Layer 21 block, before "How these are made":

```markdown
**Layer 22 — branded HTML daily report email**

The daily email was plain text. It's now a branded HTML email (with a plain-text fallback): the hosted logo, the date, the Past due / Due today / Upcoming sections, and each assignment as a card whose **name links to its Canvas Daily detail page**, with the class code, local due time, a status pill, and a Quiz tag. `build_report_html` renders a Jinja email template (status/pill data precomputed in Python); `send_email` gained an HTML alternative. The plain-text `build_report_email` is unchanged, so the `mailer` layer is untouched.

Red — `build_report_html` and the `html=` alternative don't exist yet:

![HTML report email tests failing](docs/test-evidence/reportemail-red.png)

Green — after rendering the HTML email and adding the multipart send:

![HTML report email tests passing](docs/test-evidence/reportemail-green.png)
```

- [ ] **Step 5: Do NOT commit yet.** Both PNGs commit together at the end (Task 5).

---

## Task 2: `send_email` gains an HTML alternative

**Files:**
- Modify: `app/mailer.py`
- Test: `tests/test_reportemail.py::test_send_email_attaches_html_alternative`

**Interfaces:**
- Produces: `send_email(smtp, sender, recipient, subject, body, html=None)`.

- [ ] **Step 1: Add the optional `html` param**

In `app/mailer.py`, change `send_email`:

```python
def send_email(smtp, sender, recipient, subject, body, html=None):
    """Hand a message to an SMTP client. With `html`, send multipart (text + html)."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)
    if html is not None:
        msg.add_alternative(html, subtype="html")
    smtp.send_message(msg)
    return msg
```

- [ ] **Step 2: Run the send test**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reportemail.py::test_send_email_attaches_html_alternative -v`
Expected: PASS.

---

## Task 3: The email template and `build_report_html`

**Files:**
- Create: `app/templates/report_email.html`
- Modify: `app/mailer.py`
- Test: `tests/test_reportemail.py` (the three HTML tests)

**Interfaces:**
- Consumes: `report_for_user`, `Assignment.course_short` / `due_display` / `is_quiz`.
- Produces: `build_report_html(session, user, now, base_url) -> str`.

- [ ] **Step 1: Create `app/templates/report_email.html`**

```html
{# Daily report email. Inline styles for email clients. Names are autoescaped. #}
<!doctype html><html><body style="margin:0; padding:0; background:#eef2f9; font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; color:#0d2750;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f9;"><tr><td align="center" style="padding:26px 14px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px; width:100%; background:#ffffff; border-radius:16px; overflow:hidden; box-shadow:0 6px 18px rgba(13,39,80,.08);">
<tr><td style="padding:24px 30px 8px;"><img src="{{ logo_url }}" alt="Canvas Daily" width="210" style="display:block; width:210px; max-width:58%; height:auto;"></td></tr>
<tr><td style="height:3px; background:#f5824f; background:linear-gradient(90deg,#e0218a,#6a4cff,#2f6bff);"></td></tr>
<tr><td style="padding:22px 30px 2px;"><p style="margin:0; font-size:13px; font-weight:700; letter-spacing:.5px; color:#9aa7bb; text-transform:uppercase;">{{ day }}</p>
<h1 style="margin:6px 0 0; font-size:22px; line-height:1.25; color:#0d2750;">Here's your day &mdash; {{ total }} thing{{ '' if total == 1 else 's' }} across your classes.</h1></td></tr>
{% for section in sections %}
<tr><td style="padding:18px 30px 0;"><table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="background:{{ section.chip_bg }}; color:{{ section.chip_fg }}; font-size:12px; font-weight:700; letter-spacing:.5px; padding:5px 12px; border-radius:999px;">{{ section.title | upper }} &middot; {{ section.count }}</td></tr></table></td></tr>
<tr><td style="padding:10px 30px 0;">
{% for r in section.rows %}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafc; border:1px solid #e9eef6; border-radius:12px; margin-bottom:8px;"><tr><td style="padding:13px 15px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="font-size:16px; font-weight:700;"><a href="{{ r.url }}" style="color:#0d2750; text-decoration:none;">{{ r.name }}</a>{% if r.is_quiz %} <span style="background:rgba(245,130,79,.14); color:#c2552a; font-size:10px; font-weight:700; padding:2px 7px; border-radius:999px; vertical-align:middle;">Quiz</span>{% endif %}</td>
<td align="right" style="white-space:nowrap;"><span style="background:{{ r.pill_bg }}; color:{{ r.pill_fg }}; font-size:11px; font-weight:700; padding:3px 9px; border-radius:999px;">{{ r.pill }}</span></td>
</tr></table>
<p style="margin:5px 0 0; font-size:13px; color:#6b7a90;">{{ r.cls }} &nbsp;&middot;&nbsp; due {{ r.due }}</p>
</td></tr></table>
{% endfor %}
</td></tr>
{% endfor %}
<tr><td align="center" style="padding:22px 30px 8px;"><table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="border-radius:10px; background:#0d2750;">
<a href="{{ base_url }}" style="display:inline-block; padding:13px 26px; font-size:15px; font-weight:700; color:#ffffff; text-decoration:none; border-radius:10px;">Open your dashboard &rarr;</a></td></tr></table>
<p style="margin:12px 0 0; font-size:13px; color:#8a98ad;">Tip: open any assignment, then hit &ldquo;Break this down with AI&rdquo; for a research plan and outline.</p></td></tr>
<tr><td style="padding:14px 30px 26px;"><div style="border-top:1px solid #e3e9f2; padding-top:14px;"><p style="margin:0; font-size:12px; line-height:1.6; color:#9aa7bb;">Canvas Daily &middot; One day. Every class.</p></div></td></tr>
</table></td></tr></table></body></html>
```

- [ ] **Step 2: Add the Jinja env, pills, and `build_report_html` to `app/mailer.py`**

Add to the imports at the top of `app/mailer.py`:

```python
import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
```

After `_SECTIONS = [...]`, add:

```python
_EMAIL_TEMPLATES = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(["html"]),
)

# (label, background, text) per status chip/pill.
_SECTION_CHIP = {
    "past_due": ("#fde8e8", "#b42318"),
    "due_today": ("rgba(245,130,79,.14)", "#c2552a"),
    "upcoming": ("#eaf0fb", "#2f6bff"),
}


def _pill(assignment, key):
    if key == "past_due":
        if assignment.missing:
            return "Missing", "#fde8e8", "#b42318"
        if assignment.late:
            return "Late", "#fff4e5", "#b54708"
        return "Past due", "#fde8e8", "#b42318"
    if key == "due_today":
        return "Due today", "rgba(245,130,79,.14)", "#c2552a"
    return "Upcoming", "#eaf0fb", "#2f6bff"


def build_report_html(session, user, now, base_url):
    """Render the daily report as branded HTML (names link into the app)."""
    buckets = report_for_user(session, user.id, now)
    total = sum(len(buckets[key]) for key, _ in _SECTIONS)
    sections = []
    for key, title in _SECTIONS:
        items = buckets[key]
        if not items:
            continue
        rows = []
        for a in items:
            label, bg, fg = _pill(a, key)
            rows.append({
                "name": a.name,
                "url": f"{base_url}/assignments/{a.id}",
                "cls": a.course_short or a.connection.label,
                "due": a.due_display,
                "is_quiz": a.is_quiz,
                "pill": label, "pill_bg": bg, "pill_fg": fg,
            })
        chip_bg, chip_fg = _SECTION_CHIP[key]
        sections.append({"title": title, "count": len(items),
                         "chip_bg": chip_bg, "chip_fg": chip_fg, "rows": rows})
    day = now.strftime("%A, %B ") + str(now.day)
    return _EMAIL_TEMPLATES.get_template("report_email.html").render(
        logo_url=f"{base_url}/static/logo.png", base_url=base_url,
        total=total, day=day, sections=sections)
```

- [ ] **Step 3: Run the HTML tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reportemail.py -v`
Expected: all four PASS.

---

## Task 4: Send the HTML in the daily run

**Files:**
- Modify: `app/mailer.py` (`send_daily_reports`)

**Interfaces:**
- Consumes: `build_report_email`, `build_report_html`, `send_email`.

- [ ] **Step 1: Build and send both parts**

Change `send_daily_reports`:

```python
def send_daily_reports(session, smtp, sender, now, base_url=None):
    """Send one report email to every user. Returns the number sent."""
    base_url = base_url or os.environ.get("PUBLIC_BASE_URL", "https://canvas-daily.org")
    sent = 0
    for user in session.exec(select(User)).all():
        subject, body = build_report_email(session, user, now)
        html = build_report_html(session, user, now, base_url)
        send_email(smtp, sender, user.email, subject, body, html=html)
        sent += 1
    return sent
```

- [ ] **Step 2: Confirm the full layer still passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reportemail.py -q`
Expected: all PASS.

---

## Task 5: Capture GREEN, clean up, verify, commit

**Files:**
- Capture: `docs/test-evidence/reportemail-green.png`
- Delete: `_send_test_email.py`, `_send_real_email.py`, `.github/workflows/test-email.yml`, `docs/email-mockup.html`

- [ ] **Step 1: Render and screenshot GREEN**

Run: `.venv/Scripts/python.exe tools/run_to_html.py reportemail-green tests/test_reportemail.py`
Then serve `docs/test-evidence`, screenshot `reportemail-green.html` `.frame` to `reportemail-green.png`, move into `docs/test-evidence/`, stop the server.

- [ ] **Step 2: Remove the throwaway preview artifacts**

```bash
git rm -f _send_test_email.py docs/email-mockup.html .github/workflows/test-email.yml
rm -f _send_real_email.py
```
(`_send_real_email.py` was never committed, so plain `rm`.)

- [ ] **Step 3: Full suite and evidence check**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass (the `mailer` layer is untouched; the new `reportemail` layer passes).
Run: `.venv/Scripts/python.exe tools/check_evidence.py`
Expected: `OK - ... reportemail ...`.

- [ ] **Step 4: Commit and push**

```bash
git add app/ tests/test_reportemail.py docs/test-evidence/reportemail-red.png docs/test-evidence/reportemail-green.png docs/test-evidence/reportemail-red.html docs/test-evidence/reportemail-green.html README.md
git commit -m "Send the daily report as a branded HTML email (Layer 22)"
git push origin main
```
Expected: `[pre-commit] ok`, full suite passes, push succeeds (Render auto-deploys; the next daily email — and a manual GitHub Actions run — will be the new design).

- [ ] **Step 5: Confirm end-to-end (optional, manual)**

Trigger GitHub Actions → "Daily email" → Run workflow, and confirm the real email matches the approved design. (This now comes from the real app code, not a throwaway script.)

---

## Self-Review

**Spec coverage:**
- HTML email with logo/sections/linked names/pills/quiz → Task 3 (template + `build_report_html`); asserted by `test_html_has_sections_and_linked_names`, `test_html_shows_status_pill_and_quiz_tag`.
- Name links to `{base_url}/assignments/{id}` → Task 3; asserted (`href="https://cd.test/assignments/`).
- No token in HTML → Task 3; asserted by `test_html_never_contains_the_token`.
- Multipart send (html alternative) → Task 2; asserted by `test_send_email_attaches_html_alternative`.
- Flat upcoming, empty sections omitted → Task 3 (`if not items: continue`, no week grouping).
- `PUBLIC_BASE_URL` default → Task 4.
- Plain-text fallback / mailer layer untouched → `build_report_email` unchanged; `send_email`/`send_daily_reports` backward-compatible.
- Cleanup → Task 5 Step 2.
- Evidence (red live, green) → Task 1 (red) + Task 5 (green); both committed in Task 5.

**Placeholder scan:** No TBD/TODO; full code in every step; commands have expected output.

**Type consistency:** `build_report_html(session, user, now, base_url) -> str` used identically in the tests and `send_daily_reports`. `send_email(..., html=None)` matches the new call site and the existing one (no html). `_pill` returns `(label, bg, fg)`, consumed into the `rows` dicts the template reads.

**Coupling to verify during execution:** `mailer.py` importing `jinja2` is fine (Jinja2 is already a dependency). The existing `mailer` layer is gated behind `TEST_DATABASE_URL` and only checks the plain-text `build_report_email` + `send_email` (no html) + `send_daily_reports` count/To — all unchanged in behavior, so it stays green. The `reportemail` tests use a seeded in-memory engine and call `build_report_html` directly (no Neon).
