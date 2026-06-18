"""Layer 18 - group the Upcoming column by week.

`group_by_week` buckets upcoming assignments into Monday-start calendar weeks,
labelled 'This week' / 'Next week' / 'Week of <Mon date>', skipping empty weeks.
The dashboard renders each week as a collapsible <details> (first open). The
grouping tests are pure; the dashboard test uses the FastAPI TestClient against
in-memory SQLite.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.dates import group_by_week
from app.models import Assignment, Connection, User
from app.web import create_app, get_session

# 2026-06-17 is a Wednesday; its week's Monday is 2026-06-15.
NOW = datetime(2026, 6, 17, 9, 0)


def at(days):
    """A stand-in assignment due `days` from NOW (group_by_week only reads due_at)."""
    return SimpleNamespace(due_at=NOW + timedelta(days=days))


# ---- group_by_week (pure) ----

def test_empty_input_yields_no_groups():
    assert group_by_week([], NOW) == []


def test_this_next_and_later_labels():
    groups = group_by_week([at(1), at(7), at(20)], NOW)
    labels = [g["label"] for g in groups]
    assert labels[0] == "This week"
    assert labels[1] == "Next week"
    assert labels[2].startswith("Week of")


def test_skips_empty_middle_weeks():
    # This week and ~3 weeks out, nothing between -> exactly two groups.
    groups = group_by_week([at(1), at(21)], NOW)
    assert len(groups) == 2


def test_items_land_in_their_week_in_order():
    a1, a2, a3 = at(1), at(2), at(8)
    groups = group_by_week([a1, a2, a3], NOW)
    assert groups[0]["assignments"] == [a1, a2]
    assert groups[1]["assignments"] == [a3]


# ---- dashboard renders weekly disclosures ----

@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture
def app(engine):
    application = create_app()

    def _get_session():
        with Session(engine) as s:
            yield s

    application.dependency_overrides[get_session] = _get_session
    return application


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


def seed_two_upcoming(client, engine, email="wk@x.com"):
    client.post("/signup", data={"email": email, "password": "hunter2pw"},
                follow_redirects=False)
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        conn = Connection(user_id=user.id, label="Yavapai", base_url="https://school.test",
                          account_type="student", access_token="tok")
        s.add(conn)
        s.commit()
        s.refresh(conn)
        # Fixed far-future dates in different weeks -> always "upcoming".
        for cid, name, due in [
            (1, "Essay One", datetime(2030, 6, 4, 12, 0)),
            (2, "Essay Two", datetime(2030, 6, 18, 12, 0)),
        ]:
            s.add(Assignment(connection_id=conn.id, canvas_assignment_id=cid, name=name,
                             due_at=due, points_possible=10.0,
                             submission_types=["online_upload"],
                             html_url=f"https://school.test/a/{cid}",
                             workflow_state="unsubmitted"))
        s.commit()


def test_dashboard_renders_weekly_disclosures(client, engine):
    seed_two_upcoming(client, engine)
    resp = client.get("/")
    assert resp.status_code == 200
    # The upcoming column is split into week <details> groups, first one open.
    assert 'class="weekgroup"' in resp.text
    assert 'class="weekgroup" open' in resp.text
    assert "Essay One" in resp.text
    assert "Essay Two" in resp.text
