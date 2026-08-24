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
    assert db.route() == "sqlite!degraded(graph_x.db)"   # nomina il file

    fails[0] = False
    db._open_local_engine(path)                 # holder exited, lock free
    assert path not in db.DEGRADED_PATHS, "flag stayed sticky after recovery"
    assert db.route() == "turso-local"


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))


def test_route_names_the_degraded_files(monkeypatch):
    """route() e' globale ma il degrado e' per-path: senza il nome del file,
    un contesto degradato che nessuno riapre fa dire 'degraded' a tutto."""
    monkeypatch.setattr(db, "DEGRADED_PATHS", {r"C:\x\graph_ai.db", r"C:\x\graph_default.db"})
    assert db.route() == "sqlite!degraded(graph_ai.db,graph_default.db)"
    monkeypatch.setattr(db, "DEGRADED_PATHS", set())
    monkeypatch.setattr(db, "LOCAL_TURSO_ENGINE", True)
    assert db.route() == "turso-local"


def test_lock_violation_says_another_process_holds_it(tmp_path, monkeypatch, capsys):
    """Il degrado per lock altrui non e' un guasto: e' un secondo writer.

    Il messaggio unico ('open failed, degrading') copriva due mondi diversi —
    file rotto e caso ordinario di due client sullo stesso grafo — e mandava a
    cercare la cosa sbagliata. Il lock esclusivo va nominato per quello che e'.
    """
    path = str(tmp_path / "graph_x.db")
    monkeypatch.setattr(db, "LOCAL_TURSO_ENGINE", True)
    monkeypatch.setattr(db, "DEGRADED_PATHS", set())
    monkeypatch.setattr(db, "_local_conn_cache", {})
    monkeypatch.setattr(db._time, "sleep", lambda _s: None)

    class _Turso:
        @staticmethod
        def connect(_p):
            raise RuntimeError("Locking error: Failed locking file (os error 33)")
    monkeypatch.setattr(db, "_local_turso", _Turso)

    db._open_local_engine(path)
    err = capsys.readouterr().err
    assert "another" in err and "process" in err, err
    assert "graph_x.db" in err, "deve dire QUALE grafo"
    assert "ONE writer" in err, "deve dire cosa fare"
