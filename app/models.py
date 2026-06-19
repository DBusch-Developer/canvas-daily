"""The data model: one user owns many connections; each connection owns many
stored assignments. The access token is encrypted at rest via a column type
that encrypts on write and decrypts on read, so callers only ever see plaintext.
"""

import re
from datetime import datetime, timezone

from sqlalchemy import JSON, Column
from sqlalchemy.types import Text, TypeDecorator
from sqlmodel import Field, Relationship, SQLModel

from app import crypto
from app.dates import to_local


def _utcnow():
    """Naive UTC now — matches the timestamp columns, no deprecation."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EncryptedToken(TypeDecorator):
    """Stores ciphertext; hands back plaintext. Tokens never sit in the DB raw."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else crypto.encrypt(value)

    def process_result_value(self, value, dialect):
        return None if value is None else crypto.decrypt(value)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    created_at: datetime = Field(default_factory=_utcnow)

    connections: list["Connection"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Connection(SQLModel, table=True):
    __tablename__ = "connections"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    label: str
    base_url: str  # per-connection — institutions live on different Canvas domains
    account_type: str  # "student" | "observer"
    access_token: str = Field(sa_column=Column(EncryptedToken, nullable=False))
    created_at: datetime = Field(default_factory=_utcnow)
    last_synced_at: datetime | None = None
    sync_status: str = Field(default="pending")  # pending | ok | error

    user: User | None = Relationship(back_populates="connections")
    assignments: list["Assignment"] = Relationship(
        back_populates="connection",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Assignment(SQLModel, table=True):
    __tablename__ = "assignments"

    id: int | None = Field(default=None, primary_key=True)
    connection_id: int = Field(foreign_key="connections.id", index=True)
    canvas_assignment_id: int
    name: str
    description: str = ""
    due_at: datetime | None = None
    points_possible: float | None = None
    submission_types: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    course_code: str = ""
    time_zone: str = ""
    html_url: str = ""
    workflow_state: str | None = None
    score: float | None = None  # null until graded
    submitted_at: datetime | None = None
    late: bool = False
    missing: bool = False
    excused: bool = False
    fetched_at: datetime = Field(default_factory=_utcnow)

    connection: Connection | None = Relationship(back_populates="assignments")

    @property
    def is_quiz(self) -> bool:
        """True when Canvas marks this assignment as a quiz."""
        return "online_quiz" in self.submission_types

    @property
    def course_short(self) -> str:
        """Leading code token of the course, e.g. 'CSA250'. Empty when no code."""
        return self.course_code.split()[0] if self.course_code else ""

    @property
    def course_trimmed(self) -> str:
        """course_code without a trailing '(...)' section number."""
        return re.sub(r"\s*\([^)]*\)\s*$", "", self.course_code).strip()

    @property
    def due_local(self):
        """due_at as an aware datetime in the course's timezone, or None."""
        return to_local(self.due_at, self.time_zone)

    @property
    def due_display(self) -> str:
        """e.g. 'Jun 19, 2026 · 11:59 PM', or 'No due date'."""
        d = self.due_local
        if d is None:
            return "No due date"
        time_part = d.strftime("%I:%M %p").lstrip("0")
        return f"{d.strftime('%b')} {d.day}, {d.year} · {time_part}"
