"""Cron entry points for the two daily jobs.

These are thin composition roots: read config from the environment, build the
real resources (DB session, HTTP client, SMTP connection), and hand them to the
already-tested cores in app.sync and app.mailer. No business logic lives here.

Run them from cron, e.g.:

    # fetch + store every connection's assignments, 6am UTC
    0 6 * * *  cd /app && python -m app.jobs sync
    # send each user their grouped report, 7am UTC
    0 7 * * *  cd /app && python -m app.jobs email

Required environment (alongside the existing app vars):
    DATABASE_URL          production Postgres (Neon) — NOT the test branch
    SMTP_HOST             mail server host                (email job)
    SMTP_PORT             default 587                     (email job)
    SMTP_USERNAME         login user                      (email job)
    SMTP_PASSWORD         login password                  (email job)
    SMTP_FROM             sender address (defaults to SMTP_USERNAME)
"""

import os
import smtplib
import sys
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from sqlmodel import Session

from app.db import make_engine
from app.mailer import send_daily_reports
from app.sync import run_daily_sync


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _engine():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set — refusing to run against an unknown database.")
    return make_engine(url)


def run_sync():
    """Daily pre-fetch: store every connection's assignments, then commit."""
    engine = _engine()
    with Session(engine) as session, httpx.Client(timeout=30.0) as client:
        run_daily_sync(session, client)
        session.commit()


def _connect_smtp():
    host = os.environ.get("SMTP_HOST")
    if not host:
        raise SystemExit("SMTP_HOST is not set — cannot send the daily email.")
    smtp = smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")))
    smtp.starttls()
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    if username and password:
        smtp.login(username, password)
    return smtp


def run_email():
    """Daily email: send each user their grouped report."""
    sender = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USERNAME")
    engine = _engine()
    smtp = _connect_smtp()
    try:
        with Session(engine) as session:
            count = send_daily_reports(session, smtp, sender, _now())
        print(f"sent {count} report email(s)")
    finally:
        smtp.quit()


def main(argv=None):
    load_dotenv()
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else ""
    if command == "sync":
        run_sync()
    elif command == "email":
        run_email()
    else:
        print("usage: python -m app.jobs [sync|email]")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
