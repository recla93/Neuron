"""Un secondo apertore sqlite3 su un grafo vivo cancella il WAL del worker.

Riprodotto dal vivo il 2026-09-12: store A -> `sqlite3.connect` esterno + close
-> store B, C -> riavvio pulito -> solo A sul disco. Senza la lettura esterna,
tutti e tre. Il meccanismo: alla close() sqlite3 crede di essere l'ultima
connessione, fa checkpoint e CANCELLA il -wal (su Windows può, Rust apre con
FILE_SHARE_DELETE); il worker libSQL continua a scrivere su un file che non
esiste più. `mode=ro` non fa checkpoint e lascia il WAL dov'è.

Il secondo apertore di produzione era il fallback "L2 guard" di db.py: un
secondo processo senza il lock apriva un sqlite3 SCRIVIBILE sullo stesso file.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _wal(path: str):
    p = path + "-wal"
    return os.path.getsize(p) if os.path.exists(p) else None


def test_a_read_only_open_leaves_the_live_wal_alone(tmp_path):
    turso = pytest.importorskip("turso")
    from neuron.db import TURSO_OPEN_KW, connect_read_only

    path = str(tmp_path / "g.db")
    live = turso.connect(path, **TURSO_OPEN_KW)
    live.execute("CREATE TABLE t (v TEXT)")
    live.execute("INSERT INTO t VALUES ('A')")
    live.commit()
    before = _wal(path)
    assert before, "il commit di pyturso deve stare nel WAL"

    # il caso rotto, per contrasto: un sqlite3 nudo cancella il WAL alla close
    # (lo si prova su una COPIA, per non rompere il file vivo del test)
    import shutil
    copy = str(tmp_path / "copy.db")
    shutil.copy(path, copy)
    shutil.copy(path + "-wal", copy + "-wal")
    plain = sqlite3.connect(copy)
    plain.execute("SELECT count(*) FROM t").fetchone()
    plain.close()
    assert _wal(copy) in (None, 0), "il caso che perdeva i turni"

    # il caso corretto: read-only, il WAL resta
    ro = connect_read_only(path)
    assert ro.execute("SELECT count(*) FROM t").fetchone()[0] == 1
    ro.close()
    assert _wal(path) == before

    # e le scritture successive del worker sopravvivono alla sua chiusura
    live.execute("INSERT INTO t VALUES ('B')")
    live.commit()
    live.close()
    assert [r[0] for r in sqlite3.connect(path).execute("SELECT v FROM t ORDER BY v")] == ["A", "B"]


def test_a_read_only_connection_refuses_writes(tmp_path):
    from neuron.db import connect_read_only

    path = str(tmp_path / "g.db")
    sqlite3.connect(path).execute("CREATE TABLE t (v TEXT)").connection.commit()
    ro = connect_read_only(path)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO t VALUES ('x')")


def test_the_lock_fallback_is_read_only_and_names_the_reason(tmp_path, monkeypatch, capsys):
    """Un secondo processo senza il lock non deve più diventare un secondo
    writer. Legge, e a una scrittura risponde con il PERCHÉ e il da farsi."""
    from neuron import db

    path = str(tmp_path / "graph_x.db")
    sqlite3.connect(path).execute("CREATE TABLE t (v TEXT)").connection.commit()
    monkeypatch.setattr(db, "LOCAL_TURSO_ENGINE", True)
    monkeypatch.setattr(db, "DEGRADED_PATHS", set())
    monkeypatch.setattr(db, "_local_conn_cache", {})
    monkeypatch.setattr(db._time, "sleep", lambda _s: None)

    class _Turso:
        @staticmethod
        def connect(_p, **_kw):
            raise RuntimeError("Locking error: Failed locking file (os error 33)")
    monkeypatch.setattr(db, "_local_turso", _Turso)

    conn = db._open_local_engine(path)
    assert "READ-ONLY" in capsys.readouterr().err
    assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 0     # legge
    with pytest.raises(sqlite3.OperationalError) as exc:
        conn.execute("INSERT INTO t VALUES ('x')")                       # non scrive
    msg = str(exc.value)
    assert "graph_x.db" in msg and "another process" in msg and "One writer" in msg, msg
    assert path in db.DEGRADED_PATHS


def _eof_while_tshm(monkeypatch, db, opened):
    class _Turso:
        @staticmethod
        def connect(p, **_kw):
            if os.path.exists(p + "-tshm"):
                raise OSError("IoError('io-error-completion: UnexpectedEof')")
            opened.append(p)
            return sqlite3.connect(p)
    monkeypatch.setattr(db, "_local_turso", _Turso, raising=False)
    monkeypatch.setattr(db, "LOCAL_TURSO_ENGINE", True, raising=False)
    monkeypatch.setattr(db, "DEGRADED_PATHS", set())
    monkeypatch.setattr(db, "_local_conn_cache", {})
    monkeypatch.setattr(db._time, "sleep", lambda _s: None)


def test_a_stale_tshm_over_an_empty_wal_is_set_aside_and_the_engine_reopens(tmp_path, monkeypatch):
    """Live 12-14/11: a -tshm describing frames the empty WAL did not have made
    every open fail (UnexpectedEof) and Neuron wrote two days on the fallback."""
    from neuron import db

    path = str(tmp_path / "graph_x.db")
    for suffix, data in (("", b""), ("-wal", b""), ("-tshm", b"old")):
        open(path + suffix, "wb").write(data)
    opened = []
    _eof_while_tshm(monkeypatch, db, opened)

    db._open_local_engine(path)
    assert opened == [path]                                   # the engine, not the fallback
    assert path not in db.DEGRADED_PATHS
    assert not os.path.exists(path + "-tshm") and os.path.exists(path + "-tshm.stale")


def test_a_tshm_with_frames_in_the_wal_is_never_touched(tmp_path, monkeypatch):
    """Frames in the WAL may be writes not yet replayed: the sidecar stays."""
    from neuron import db

    path = str(tmp_path / "graph_x.db")
    sqlite3.connect(path).execute("CREATE TABLE t (v TEXT)").connection.commit()
    open(path + "-wal", "wb").write(b"frames")
    open(path + "-tshm", "wb").write(b"old")
    opened = []
    _eof_while_tshm(monkeypatch, db, opened)

    db._open_local_engine(path)
    assert opened == [] and path in db.DEGRADED_PATHS
    assert open(path + "-tshm", "rb").read() == b"old"


def test_other_open_failures_still_get_a_writable_single_opener(tmp_path, monkeypatch):
    """Il caso L2 (NotFound transitorio, nessun altro processo sul file) resta
    com'era: sqlite3 scrivibile sullo stesso file, unico apertore."""
    from neuron import db

    class _Boom:
        @staticmethod
        def connect(_path, **_kw):
            raise OSError("open: NotFound")
    monkeypatch.setattr(db, "_local_turso", _Boom, raising=False)
    monkeypatch.setattr(db, "LOCAL_TURSO_ENGINE", True, raising=False)
    monkeypatch.setattr(db, "_local_conn_cache", {})
    monkeypatch.setattr(db._time, "sleep", lambda _s: None)

    conn = db._open_local_engine(str(tmp_path / "g.db"))
    conn.execute("CREATE TABLE t(x)")
    conn.execute("INSERT INTO t VALUES (1)")
    assert conn.execute("SELECT x FROM t").fetchone()[0] == 1
