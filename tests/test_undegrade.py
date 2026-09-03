"""The L2 degrade flag must clear once Turso opens again.

Regression: DEGRADED_PATHS was add-only, so route() reported "sqlite!degraded"
for the whole process life after a single failed open -- even though the
sqlite3 fallback is never cached and every connect() already retries Turso.

Quella premessa reggeva solo per `_local_conn_cache`. La cache del SEED
accoglieva anche il ripiego sqlite3, quindi `_open_local_engine` non veniva
piu' richiamato e il flag restava acceso davvero: i due test in fondo coprono
quella meta', e il latch globale che ne seguiva.
"""
import pytest

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


# ---------------------------------------------------------------------------
# La cache del seed teneva in vita il ripiego
# ---------------------------------------------------------------------------
# Il docstring in cima a questo file dava per scontato che «the sqlite3 fallback
# is never cached, so every connect() already retries Turso». Vero per
# `_local_conn_cache`, che accoglie solo handle Turso. Falso per la cache del
# seed, dove finiva qualunque cosa uscisse da `connect_local` — e da li' in poi
# `_open_local_engine` non veniva piu' richiamato, quindi l'un-degrade sopra non
# girava mai.


def _fake_engine(monkeypatch, fails):
    """Motore Turso finto che fallisce finche' `fails[0]`."""
    class _Conn:
        def execute(self, *_a):
            return self

        def close(self):
            pass

    class _Turso:
        @staticmethod
        def connect(_p):
            if fails[0]:
                raise RuntimeError("Locking error: os error 33")
            return _Conn()

    monkeypatch.setattr(db, "LOCAL_TURSO_ENGINE", True)
    monkeypatch.setattr(db, "DEGRADED_PATHS", set())
    monkeypatch.setattr(db, "_local_conn_cache", {})
    monkeypatch.setattr(db._time, "sleep", lambda _s: None)
    monkeypatch.setattr(db, "_local_turso", _Turso)


def test_the_seed_comes_back_to_turso_when_the_lock_is_released(tmp_path, monkeypatch):
    """Riprodotto prima del fix: lock rilasciato, `connect_local` sullo stesso
    path tornava subito a turso-local, ma `_seed_connection` continuava a
    restituire l'handle sqlite3 in cache — e la riga di stato diceva
    `degraded` per tutta la vita del processo."""
    pytest.importorskip("mcp")
    import neuron.server as srv
    from neuron import search

    path = str(tmp_path / "base_knowledge.db")
    open(path, "wb").close()
    fails = [True]
    _fake_engine(monkeypatch, fails)
    monkeypatch.setattr(srv, "_seed_conn_cache", {})
    monkeypatch.setattr(search, "_SEED_RETRY_SEC", 0.0)
    monkeypatch.setattr(search, "_seed_retry_at", {})

    search._seed_connection(path)                       # lock ancora preso
    assert db.route() == "sqlite!degraded(base_knowledge.db)"

    fails[0] = False
    search._seed_connection(path)                       # lock libero
    assert db.route() == "turso-local", "il ripiego in cache ha bloccato l'un-degrade"


def test_a_degraded_file_does_not_switch_off_vector_sql_everywhere(monkeypatch):
    """Un file caduto su sqlite3 alza la STESSA `no such function` di un motore
    senza vettori, ma non dice niente sul motore.

    Latchare il globale li' spegneva il tier SQL nativo anche sui grafi sani, e
    con esso `cross_context_matches` — cioe' l'avviso di contesto distante. Un
    lock transitorio su un file diventava una degradazione permanente su tutto.
    """
    pytest.importorskip("mcp")
    import neuron.server as srv
    from neuron import search

    seed, graph = r"C:\x\base_knowledge.db", r"C:\x\graph_ai.db"
    monkeypatch.setattr(db, "DEGRADED_PATHS", {seed})
    monkeypatch.setattr(srv, "_vector_sql_ok", True)
    exc = RuntimeError("no such function: vector_distance_cos")

    assert search._permanent_vector_failure(exc, seed) is True, "quel file va saltato"
    assert srv._vector_sql_ok is True, "un file degradato ha spento il tier globale"

    assert search._permanent_vector_failure(exc, graph) is True
    assert srv._vector_sql_ok is False, "un motore senza vettori deve latchare"
