# Email the user when a connection's token breaks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the daily sync survive a single connection failing, and email the affected user once — branded like the daily report — when Canvas rejects their token.

**Architecture:** `run_daily_sync` wraps each connection in try/except, marks it `ok`/`error`, and returns the connections that *newly* broke on a 401/403. `jobs.run_sync` sends a branded email per newly-broken connection (best-effort). `sync.py` stays pure (returns data; no mailer import).

**Tech Stack:** FastAPI, SQLModel, httpx (Canvas mocked at the transport boundary), Jinja2 email template, pytest, in-memory SQLite for tests.

## Global Constraints

- TDD-first: failing test before implementation. One live **red** + one **green** screenshot for the layer, in `docs/test-evidence/`, referenced in the README. Red captured before the code exists.
- Never store, print, or log the token in plaintext — not in `run_daily_sync`, `build_token_error_email`, or the jobs email loop.
- New behavior → its own `tests/test_tokenalert.py` (label `tokenalert`) and its own README "Layer 24" section. Do not fold into an existing layer.
- Token rejection = `httpx.HTTPStatusError` with `response.status_code` in `(401, 403)`. Other failures are marked `error` but never emailed.
- "Once per breakage": email only on the working → broken transition (connection was not already `error`), using the existing `sync_status` — no new column.
- Email is branded HTML (logo header + gradient, same as the report email) plus a plain-text fallback.

---

### Task 1: Layer 24 — token-rejection email alert (single layer, single commit)

**Files:**
- Create: `tests/test_tokenalert.py`
- Modify: `app/sync.py` (add `import httpx`, `_is_token_rejection`, return `newly_broken` from `run_daily_sync`)
- Create: `app/templates/token_error_email.html`
- Modify: `app/mailer.py` (add `build_token_error_email`)
- Modify: `app/jobs.py` (`run_sync` sends emails via `_email_broken_connections`)
- Create: `docs/test-evidence/tokenalert-red.png`, `docs/test-evidence/tokenalert-green.png`
- Modify: `README.md` (new "Layer 24" section)

**Interfaces:**
- Produces: `run_daily_sync(session, client) -> list[Connection]` (newly-broken token rejections); `build_token_error_email(connection, base_url) -> tuple[str, str, str]` (subject, text_body, html).
- Consumes: `sync_connection` (unchanged, still raises on failure); `send_email`, `_EMAIL_TEMPLATES` from `app.mailer`; `_connect_smtp`, `_engine` in `app.jobs`.

- [ ] **Step 1: Write the failing tests** — `tests/test_tokenalert.py`:

```python
"""Layer 24 — email the user when a connection's token breaks.

Daily sync survives a single connection failing, marks each ok/error, and
returns the connections that newly broke on a Canvas token rejection (401/403).
build_token_error_email renders the branded alert. In-memory SQLite; no Neon.
"""

import httpx
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.mailer import build_token_error_email
from app.models import Assignment, Connection, User
from app.sync import run_daily_sync

GOOD = "https://good.test"
BAD = "https://bad.test"
ERR = "https://err.test"


def make_session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return Session(eng)


def a_connection(s, user, *, label, base_url, status="pending", token="tok"):
    conn = Connection(user_id=user.id, label=label, base_url=base_url,
                      account_type="student", access_token=token, sync_status=status)
    s.add(conn); s.commit(); s.refresh(conn)
    return conn


def a_user(s, email="p@x.com"):
    user = User(email=email, password_hash="h")
    s.add(user); s.commit(); s.refresh(user)
    return user


def canvas(request):
    """200 with one course + assignment for GOOD; 401 for BAD; 500 for ERR."""
    host = request.url.host
    if host == "bad.test":
        return httpx.Response(401, json={"errors": [{"message": "Invalid access token."}]})
    if host == "err.test":
        return httpx.Response(500, json={"errors": ["boom"]})
    path = request.url.path
    if path.endswith("/courses"):
        return httpx.Response(200, json=[{"id": 10, "name": "Bio"}])
    if path.endswith("/courses/10/assignments"):
        return httpx.Response(200, json=[{
            "id": 1, "name": "Lab", "due_at": "2026-06-20T23:59:00Z",
            "points_possible": 25, "submission_types": ["online_upload"],
            "html_url": f"{GOOD}/a/1", "description": "<p>Do it.</p>"}])
    return httpx.Response(200, json=[])


def client():
    return httpx.Client(transport=httpx.MockTransport(canvas))


def test_daily_sync_continues_past_failure_marking_each():
    s = make_session()
    user = a_user(s)
    good = a_connection(s, user, label="Good", base_url=GOOD)
    bad = a_connection(s, user, label="Bad", base_url=BAD)

    run_daily_sync(s, client())  # must not raise

    assert s.get(Connection, good.id).sync_status == "ok"
    assert s.get(Connection, bad.id).sync_status == "error"
    stored = s.exec(select(Assignment).where(Assignment.connection_id == good.id)).all()
    assert len(stored) == 1


def test_returns_newly_broken_token_rejection():
    s = make_session()
    user = a_user(s)
    bad = a_connection(s, user, label="Bad", base_url=BAD)

    broken = run_daily_sync(s, client())

    assert [c.id for c in broken] == [bad.id]


def test_excludes_already_error_connection():
    s = make_session()
    user = a_user(s)
    a_connection(s, user, label="Bad", base_url=BAD, status="error")

    broken = run_daily_sync(s, client())

    assert broken == []


def test_non_token_failure_marked_error_not_returned():
    s = make_session()
    user = a_user(s)
    err = a_connection(s, user, label="Err", base_url=ERR)

    broken = run_daily_sync(s, client())

    assert s.get(Connection, err.id).sync_status == "error"
    assert broken == []


def test_build_token_error_email_branded_no_token():
    s = make_session()
    user = a_user(s)
    conn = a_connection(s, user, label="Yavapai College", base_url=GOOD,
                        token="super-secret-token-value")

    subject, text_body, html = build_token_error_email(conn, "https://cd.test")

    assert "Yavapai College" in subject
    assert "Yavapai College" in html
    assert "New Access Token" in html
    assert "https://cd.test/static/logo.png" in html
    assert "https://cd.test/connections" in html
    for blob in (subject, text_body, html):
        assert "super-secret-token-value" not in blob
```

- [ ] **Step 2: Confirm red (plain pytest)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_tokenalert.py -v`
Expected: import FAILS — `ImportError: cannot import name 'build_token_error_email' from 'app.mailer'`.

- [ ] **Step 3: Capture red live (before any implementation)**

Run: `.venv\Scripts\python.exe tools/run_to_html.py tokenalert-red tests/test_tokenalert.py`
Expected: prints `[RED ...]`. Then serve + screenshot:
- Background: `.venv\Scripts\python.exe -m http.server 8731 --directory docs/test-evidence`
- Browser → `http://127.0.0.1:8731/tokenalert-red.html`, screenshot `.frame` → `docs/test-evidence/tokenalert-red.png`. Verify legible.

- [ ] **Step 4: Implement `run_daily_sync` resilience** — `app/sync.py`. Add `import httpx` at the top, and replace `run_daily_sync` with:

```python
def run_daily_sync(session, client):
    """Sync every connection. One path for one connection and for four.

    Per-connection resilient: a single connection failing marks only that
    connection `error` and does not abort the rest. Returns the connections
    that newly broke on a Canvas token rejection (401/403) — i.e. were not
    already `error` — so the caller can notify their owners once.
    """
    newly_broken = []
    for connection in session.exec(select(Connection)).all():
        was_error = connection.sync_status == "error"
        try:
            sync_connection(session, connection, client)
            connection.sync_status = "ok"
        except Exception as exc:
            connection.sync_status = "error"
            if not was_error and _is_token_rejection(exc):
                newly_broken.append(connection)
        session.add(connection)
    session.flush()
    return newly_broken


def _is_token_rejection(exc):
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in (401, 403)
    )
```

- [ ] **Step 5: Create the email template** — `app/templates/token_error_email.html`:

```html
{# Token-rejected alert email. Inline styles for email clients. label autoescaped. #}
<!doctype html><html><body style="margin:0; padding:0; background:#eef2f9; font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; color:#0d2750;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f9;"><tr><td align="center" style="padding:26px 14px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px; width:100%; background:#ffffff; border-radius:16px; overflow:hidden; box-shadow:0 6px 18px rgba(13,39,80,.08);">
<tr><td style="padding:24px 30px 8px;"><img src="{{ logo_url }}" alt="Canvas Daily" width="210" style="display:block; width:210px; max-width:58%; height:auto;"></td></tr>
<tr><td style="height:3px; background:#f5824f; background:linear-gradient(90deg,#e0218a,#6a4cff,#2f6bff);"></td></tr>
<tr><td style="padding:22px 30px 2px;">
<table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="background:#fff4e5; color:#b54708; font-size:12px; font-weight:700; letter-spacing:.5px; padding:5px 12px; border-radius:999px;">ACTION NEEDED</td></tr></table>
<h1 style="margin:12px 0 0; font-size:22px; line-height:1.3; color:#0d2750;">One of your Canvas connections needs a new token</h1>
<p style="margin:10px 0 0; font-size:15px; line-height:1.6; color:#48566b;">Canvas stopped accepting the access token for <strong style="color:#0d2750;">{{ label }}</strong>, so new assignments from it aren&rsquo;t showing up in your daily report. This usually means the token expired or was removed in Canvas. It&rsquo;s a two&#8209;minute fix:</p>
</td></tr>
<tr><td style="padding:18px 30px 0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafc; border:1px solid #e9eef6; border-radius:12px;"><tr><td style="padding:18px 20px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
<tr><td valign="top" width="30" style="font-size:15px; font-weight:700; color:#6a4cff;">1.</td><td style="font-size:14px; line-height:1.55; color:#3c4a60; padding-bottom:10px;">Log in to Canvas and open <strong style="color:#0d2750;">Account &rarr; Settings</strong>.</td></tr>
<tr><td valign="top" style="font-size:15px; font-weight:700; color:#6a4cff;">2.</td><td style="font-size:14px; line-height:1.55; color:#3c4a60; padding-bottom:10px;">Under <strong style="color:#0d2750;">Approved Integrations</strong>, click <strong style="color:#0d2750;">+ New Access Token</strong>. Give it a purpose like &ldquo;Canvas Daily,&rdquo; and set the <strong style="color:#0d2750;">expiration date as far in the future as Canvas allows</strong> &mdash; it&rsquo;s required, so you can&rsquo;t leave it blank.</td></tr>
<tr><td valign="top" style="font-size:15px; font-weight:700; color:#6a4cff;">3.</td><td style="font-size:14px; line-height:1.55; color:#3c4a60; padding-bottom:10px;">Click <strong style="color:#0d2750;">Generate Token</strong>, then <strong style="color:#0d2750;">select the whole token and copy it manually</strong> &mdash; Canvas shows it only once and there&rsquo;s no copy button, so be sure to grab all of it.</td></tr>
<tr><td valign="top" style="font-size:15px; font-weight:700; color:#6a4cff;">4.</td><td style="font-size:14px; line-height:1.55; color:#3c4a60;">Back in Canvas Daily, remove this connection and add it again with the new token.</td></tr>
</table>
</td></tr></table>
</td></tr>
<tr><td align="center" style="padding:22px 30px 6px;"><table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="border-radius:10px; background:#0d2750;">
<a href="{{ base_url }}/connections" style="display:inline-block; padding:13px 26px; font-size:15px; font-weight:700; color:#ffffff; text-decoration:none; border-radius:10px;">Fix this connection &rarr;</a></td></tr></table>
<p style="margin:12px 0 0; font-size:13px; color:#8a98ad;">Your other connections are still working &mdash; only this one needs attention.</p></td></tr>
<tr><td style="padding:14px 30px 26px;"><div style="border-top:1px solid #e3e9f2; padding-top:14px;"><p style="margin:0; font-size:12px; line-height:1.6; color:#9aa7bb;">Canvas Daily &middot; One day. Every class.</p></div></td></tr>
</table></td></tr></table></body></html>
```

- [ ] **Step 6: Implement `build_token_error_email`** — append to `app/mailer.py`:

```python
def build_token_error_email(connection, base_url):
    """Return (subject, text_body, html) telling a user one connection's token
    was rejected by Canvas. The token is never included."""
    subject = f"Action needed: reconnect {connection.label} on Canvas Daily"
    text_body = (
        f"Canvas stopped accepting the access token for {connection.label}, so "
        f"new assignments from it aren't showing up in your daily report.\n\n"
        "To fix it:\n"
        "1. Log in to Canvas and open Account > Settings.\n"
        "2. Under Approved Integrations, click + New Access Token. Give it a "
        "purpose like \"Canvas Daily\" and set the expiration date as far in the "
        "future as Canvas allows (it's required).\n"
        "3. Click Generate Token, then select the whole token and copy it "
        "manually -- Canvas shows it only once and there's no copy button.\n"
        "4. Back in Canvas Daily, remove this connection and add it again with "
        "the new token.\n\n"
        f"Fix it here: {base_url}/connections\n\n"
        "Canvas Daily - One day. Every class.\n"
    )
    html = _EMAIL_TEMPLATES.get_template("token_error_email.html").render(
        logo_url=f"{base_url}/static/logo.png",
        base_url=base_url,
        label=connection.label,
    )
    return subject, text_body, html
```

- [ ] **Step 7: Wire the email into the cron root** — `app/jobs.py`. Add imports and update `run_sync`:

Change the mailer import line to include the new helpers:

```python
from app.mailer import build_token_error_email, send_daily_reports, send_email
```

Replace `run_sync` and add the helper:

```python
def run_sync():
    """Daily pre-fetch: store every connection's assignments, then email the
    owner of any connection whose token Canvas newly rejected."""
    engine = _engine()
    with Session(engine) as session, httpx.Client(timeout=30.0) as client:
        newly_broken = run_daily_sync(session, client)
        session.commit()
        _email_broken_connections(session, newly_broken)


def _email_broken_connections(session, connections):
    """Send one token-error email per newly-broken connection. Best-effort: a
    missing SMTP config or a single send failure never aborts the sync, and the
    token is never logged."""
    if not connections:
        return
    if not os.environ.get("SMTP_HOST"):
        return
    base_url = os.environ.get("PUBLIC_BASE_URL", "https://canvas-daily.org")
    sender = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USERNAME")
    smtp = _connect_smtp()
    try:
        for connection in connections:
            subject, text_body, html = build_token_error_email(connection, base_url)
            try:
                send_email(smtp, sender, connection.user.email, subject, text_body, html=html)
            except Exception:
                pass
    finally:
        smtp.quit()
```

- [ ] **Step 8: Confirm green**

Run: `.venv\Scripts\python.exe -m pytest tests/test_tokenalert.py -v`
Expected: 5 passed.

- [ ] **Step 9: Run the full suite (no regressions)**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all pass (existing `test_sync.py::test_run_daily_sync_covers_every_connection` still green — it ignores the new return value).

- [ ] **Step 10: Capture green**

Run: `.venv\Scripts\python.exe tools/run_to_html.py tokenalert-green tests/test_tokenalert.py`
Expected: `[GREEN ...]`. Screenshot `http://127.0.0.1:8731/tokenalert-green.html` `.frame` → `docs/test-evidence/tokenalert-green.png`. Verify. Stop the HTTP server.

- [ ] **Step 11: Add the README "Layer 24" section** — under Test evidence, after Layer 23, before the "How these are made" line:

```markdown
**Layer 24 — email the user when a connection's token breaks**

A Canvas token can stop working after it was added (revoked, expired, regenerated). The daily sync had no per-connection error handling — the first 401 aborted the whole run, and nobody was told. Now `run_daily_sync` marks each connection ok/error independently (one bad token no longer stops everyone's fetch) and returns the connections that *newly* broke on a token rejection; `jobs.run_sync` emails each owner once — branded like the daily report — with steps to issue a new token. Outages and already-broken connections don't trigger it, and the token never appears in the email.

Red — `run_daily_sync` doesn't return broken connections and `build_token_error_email` doesn't exist:

![Token alert tests failing](docs/test-evidence/tokenalert-red.png)

Green — after adding per-connection resilience and the branded alert email:

![Token alert tests passing](docs/test-evidence/tokenalert-green.png)
```

- [ ] **Step 12: Commit** (pre-commit hook runs check_evidence + full suite)

```bash
git add tests/test_tokenalert.py app/sync.py app/mailer.py app/jobs.py app/templates/token_error_email.html docs/test-evidence/tokenalert-red.png docs/test-evidence/tokenalert-green.png README.md docs/superpowers/plans/2026-06-21-token-rejected-email-alert.md
git commit -m "Email the user when a connection's token breaks (Layer 24)"
```

---

## Self-Review

- **Spec coverage:** per-connection resilience ✓ Step 4; token-rejection classification ✓ `_is_token_rejection`; once-per-breakage via `sync_status` ✓ (`was_error`); returns newly-broken ✓; branded email + template ✓ Steps 5–6; jobs wiring best-effort ✓ Step 7; Layer 24 evidence ✓ Steps 3/10/11. Out-of-scope (id=9, add-time, outages, reminders) intentionally absent.
- **Placeholder scan:** none — all code and copy is concrete.
- **Type consistency:** `run_daily_sync(session, client) -> list[Connection]` used in tests (Step 1) and impl (Step 4). `build_token_error_email(connection, base_url) -> (subject, text_body, html)` identical in tests (Step 1), impl (Step 6), and jobs (Step 7). Template variables `logo_url` / `base_url` / `label` match between Step 5 and Step 6.
