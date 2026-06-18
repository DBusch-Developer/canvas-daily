"""Layer 10 — background sync + account setup page (Canvas mocked, Neon test branch)."""

import logging
import os
from datetime import datetime

import httpx
import pytest
from sqlmodel import Session, SQLModel, select

from app.db import make_engine
from app.models import Assignment, Connection, User

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (setup-flow tests need a Neon test branch)",
)

BASE = "https://school.test"


@pytest.fixture(scope="module")
def engine():
    eng = make_engine(os.environ["TEST_DATABASE_URL"])
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture(autouse=True)
def wipe(engine):
    yield
    with engine.begin() as conn:
        for table in reversed(SQLModel.metadata.sorted_tables):
            conn.execute(table.delete())


def a_user_and_connection(engine, email="d@x.com", token="tok"):
    """Create a user + one connection; return the connection id."""
    with Session(engine) as s:
        user = User(email=email, password_hash="h")
        s.add(user); s.commit(); s.refresh(user)
        conn = Connection(user_id=user.id, label="Mine", base_url=BASE,
                          account_type="student", access_token=token)
        s.add(conn); s.commit(); s.refresh(conn)
        return conn.id


def test_new_connection_defaults_to_pending(engine):
    conn_id = a_user_and_connection(engine)
    with Session(engine) as s:
        assert s.get(Connection, conn_id).sync_status == "pending"
