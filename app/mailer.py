"""The daily email: one message per user, merging every assignment across all
their connections — grouped Past due / Due today / Upcoming, sorted by due date,
each item labeled by its connection. Plain text, summary only: names, due dates,
and labels — never descriptions or tokens.

SMTP is injected so it can be faked in tests; a thin entry point builds the real
smtplib client from the environment.
"""

import os
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlmodel import select

from app.models import User
from app.reports import report_for_user

_SECTIONS = [("past_due", "Past due"), ("due_today", "Due today"), ("upcoming", "Upcoming")]

_EMAIL_TEMPLATES = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(["html"]),
)

# Background / text colors for each section's count chip.
_SECTION_CHIP = {
    "past_due": ("#fde8e8", "#b42318"),
    "due_today": ("rgba(245,130,79,.14)", "#c2552a"),
    "upcoming": ("#eaf0fb", "#2f6bff"),
}


def _pill(assignment, key):
    """The per-assignment status pill: (label, background, text color)."""
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


def build_report_email(session, user, now):
    """Return (subject, body) for a user's daily report."""
    buckets = report_for_user(session, user.id, now)
    total = sum(len(buckets[key]) for key, _ in _SECTIONS)

    lines = ["Your Canvas Daily report", ""]
    for key, title in _SECTIONS:
        lines.append(f"== {title} ==")
        items = buckets[key]
        if not items:
            lines.append("  (nothing)")
        for assignment in items:
            label = assignment.connection.label
            quiz = " (Quiz)" if assignment.is_quiz else ""
            lines.append(f"  - [{label}] {assignment.name}{quiz} — due {assignment.due_display}")
        lines.append("")

    subject = f"Canvas Daily — {total} assignment{'s' if total != 1 else ''}"
    return subject, "\n".join(lines).rstrip() + "\n"


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
