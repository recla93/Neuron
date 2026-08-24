"""The L2 degrade flag must clear once Turso opens again.

Regression: DEGRADED_PATHS was add-only, so route() reported "sqlite!degraded"
for the whole process life after a single failed open -- even though the
sqlite3 fallback is never cached and every connect() already retries Turso.
"""
from neuron import db


def test_degrade_flag_clears_on_successful_reopen(tmp_path, monkeypatch):
    path = str(tmp_path / "graph_x.db")
    monkeypatch.setattr(db, "LOCAL_TURSO_ENGINE", True)
    monkeypatch.setattr(db, "DEGRADED_PATHS", set())
    monkeypatch.setattr(db, "_local_conn_cache", {})
    monkeypatch.setattr(db._time, "sleep", lambda _s: None)

    class _Conn:
        def execute(self, *_a):
            return self

    fails = [True]

    class _Turso:
        @staticmethod
        def connect(_p):
            if fails[0]:
                raise RuntimeError("Locking error: os error 33")
            return _Conn()

    monkeypatch.setattr(db, "_local_turso", _Turso)

    db._open_local_engine(path)                 # lock holder still up
    assert path in db.DEGRADED_PATHS
    assert db.route() == "sqlite!degraded"

    fails[0] = False
    db._open_local_engine(path)                 # holder exited, lock free
    assert path not in db.DEGRADED_PATHS, "flag stayed sticky after recovery"
    assert db.route() == "turso-local"


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
