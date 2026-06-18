"""The daily email: one message per user, merging every assignment across all
their connections — grouped Past due / Due today / Upcoming, sorted by due date,
each item labeled by its connection. Plain text, summary only: names, due dates,
and labels — never descriptions or tokens.

SMTP is injected so it can be faked in tests; a thin entry point builds the real
smtplib client from the environment.
"""

from email.message import EmailMessage

from sqlmodel import select

from app.models import User
from app.reports import report_for_user

_SECTIONS = [("past_due", "Past due"), ("due_today", "Due today"), ("upcoming", "Upcoming")]


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
            lines.append(f"  - [{label}] {assignment.name} — due {assignment.due_at}")
        lines.append("")

    subject = f"Canvas Daily — {total} assignment{'s' if total != 1 else ''}"
    return subject, "\n".join(lines).rstrip() + "\n"


def send_email(smtp, sender, recipient, subject, body):
    """Hand a plain-text message to an SMTP client (anything with send_message)."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)
    smtp.send_message(msg)
    return msg


def send_daily_reports(session, smtp, sender, now):
    """Send one report email to every user. Returns the number sent."""
    sent = 0
    for user in session.exec(select(User)).all():
        subject, body = build_report_email(session, user, now)
        send_email(smtp, sender, user.email, subject, body)
        sent += 1
    return sent
