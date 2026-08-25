"""reset() deve cancellare le righe ANCHE sul tier Turso Cloud.

Prima del 2026-08-25 unlinkava solo il file locale (che sul tier remoto non
esiste o è stantio) e svuotava la copia in memoria: il tool rispondeva
"Graph reset." dopo una conferma esplicita, ma il reload ripartiva dal cloud
identico — un wipe finto sul percorso più pericoloso che ci sia.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neuron import db as _db                     # noqa: E402
from neuron import registry as _registry         # noqa: E402


def _cloud_stub():
    """A shared in-memory 'cloud' store with the real schema."""
    conn = sqlite3.connect(":memory:")
    # _create_store_schema never touches self: an empty instance is enough for the DDL
    g = _registry.Graph.__new__(_registry.Graph)
    g._create_store_schema(conn, "test")
    conn.execute("INSERT INTO nodes (context, keyword, salience) VALUES ('test','ada',5)")
    conn.execute("INSERT INTO links (context, source, target, link_type, weight) "
                 "VALUES ('test','ada','pranzo','deepening','medium')")
    conn.commit()
    return conn


class _SharedConn:
    """Each remote connect() returns a fresh connection; this test reuses the
    same sqlite3 instead, so close() must not actually close it."""

    def __init__(self, inner):
        self._inner = inner

    def execute(self, *a, **k):
        return self._inner.execute(*a, **k)

    def commit(self):
        self._inner.commit()

    def close(self):
        pass


def test_reset_on_remote_tier_deletes_rows(monkeypatch, tmp_path):
    cloud = _cloud_stub()
    monkeypatch.setattr(_db, "REMOTE_TURSO", True)
    monkeypatch.setattr(_db, "connect", lambda path: _SharedConn(cloud))

    reg = _registry.GraphRegistry(graphs_dir=str(tmp_path / "graphs"))
    reg.reset(context="test")

    for table in ("nodes", "links", "node_vectors", "episodes", "refs", "_graveyard"):
        n = cloud.execute(f"SELECT COUNT(*) FROM {table} "
                          "WHERE context='test'").fetchone()[0]
        assert n == 0, f"{table}: context not wiped"


def test_reset_all_on_remote_tier_sweeps_every_context(monkeypatch, tmp_path):
    cloud = _cloud_stub()
    cloud.execute("INSERT INTO nodes (context, keyword, salience) "
                  "VALUES ('altro','bob',3)")
    cloud.commit()
    monkeypatch.setattr(_db, "REMOTE_TURSO", True)
    monkeypatch.setattr(_db, "connect", lambda path: _SharedConn(cloud))

    reg = _registry.GraphRegistry(graphs_dir=str(tmp_path / "graphs"))
    reg.reset()

    n = cloud.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    assert n == 0, "full reset left rows in the cloud"
