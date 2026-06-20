"""Layer 21 - resilient DB pool (no stale-connection 500s).

Neon (serverless Postgres) closes idle connections, so after the app sits idle a
pooled connection goes dead and the next request 500s until a refresh swaps it
out. The engine must (a) pre-ping connections to check they're alive before use,
and (b) recycle connections older than a few minutes. These tests pin both on the
engine `make_engine` builds, using a local sqlite URL (no Neon needed).
"""

from app.db import make_engine


def test_engine_pre_pings_connections():
    engine = make_engine("sqlite://")
    # SQLAlchemy tests the connection's liveness before handing it to a request,
    # transparently replacing one the server has dropped.
    assert engine.pool._pre_ping is True


def test_engine_recycles_stale_connections():
    engine = make_engine("sqlite://")
    # Connections older than 5 minutes are recycled rather than reused.
    assert engine.pool._recycle == 300
