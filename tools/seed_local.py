"""Seed a local dev database with a demo user and assignments to click through.

    python tools/seed_local.py

Wipes and reseeds whatever DATABASE_URL points at, so point it at a local
SQLite file (e.g. sqlite:///./local.db), never production. Log in afterwards as
demo@canvasdaily.test / password123.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlmodel import Session, SQLModel  # noqa: E402

from app.auth import hash_password  # noqa: E402
from app.db import make_engine  # noqa: E402
from app.models import Assignment, Connection, User  # noqa: E402


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Set DATABASE_URL in .env (e.g. sqlite:///./local.db) first.")
    if "neon.tech" in url:
        raise SystemExit("DATABASE_URL points at Neon — seed a local SQLite file instead.")

    engine = make_engine(url)
    SQLModel.metadata.create_all(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    end_of_today = now.replace(hour=23, minute=59, second=0, microsecond=0)

    with Session(engine) as session:
        for table in reversed(SQLModel.metadata.sorted_tables):
            session.execute(table.delete())

        user = User(email="demo@canvasdaily.test", password_hash=hash_password("password123"))
        session.add(user)
        session.commit()
        session.refresh(user)

        mine = Connection(user_id=user.id, label="Mine", base_url="https://school.instructure.com",
                          account_type="student", access_token="demo-token-mine")
        kid = Connection(user_id=user.id, label="Kid A", base_url="https://k12.instructure.com",
                         account_type="observer", access_token="demo-token-kid")
        session.add(mine)
        session.add(kid)
        session.commit()
        session.refresh(mine)
        session.refresh(kid)

        rows = [
            # (connection, name, due, description, points) — spread across buckets
            (mine, "Persuasive essay", now - timedelta(days=3),
             "<p>Write a five-paragraph essay arguing for or against year-round school.</p>", 100),
            (mine, "Chapter 7 problem set", now - timedelta(hours=5),
             "<p>Complete problems 1–20. Show your work.</p>", 50),
            (mine, "Photosynthesis lab report", end_of_today,
             "<p>Submit your lab writeup with the data table and conclusion.</p>", 40),
            (kid, "Reading log", now + timedelta(days=1),
             "<p>Log 30 minutes of independent reading.</p>", 10),
            (kid, "Math quiz corrections", now + timedelta(days=3),
             "<p>Correct every missed problem and explain the fix.</p>", 20),
        ]
        for i, (conn, name, due, description, points) in enumerate(rows, start=1):
            session.add(Assignment(
                connection_id=conn.id, canvas_assignment_id=i, name=name, due_at=due,
                description=description, points_possible=points,
                submission_types=["online_text_entry"],
                html_url=f"https://example.test/a/{i}", workflow_state="unsubmitted",
            ))
        session.commit()

    print("Seeded. Log in at http://127.0.0.1:8000/login")
    print("  email:    demo@canvasdaily.test")
    print("  password: password123")


if __name__ == "__main__":
    main()
