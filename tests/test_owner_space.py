"""Every row says who wrote it and in which space (ADR-013, phase 4a).

Nodes, links and episodes had no author: in the shared-store team mode nobody
could tell who wrote what. A local file is one user's by construction; a shared
store is not, and its older rows must not get an invented author.
"""
import sqlite3

import pytest

from neuron import db as _db
from neuron.models import Graph, Link, Node


@pytest.fixture
def ada(monkeypatch):
    monkeypatch.setenv("NEURON_USER", "ada")
    monkeypatch.delenv("NEURON_SPACE", raising=False)
    monkeypatch.setattr(_db, "REMOTE_TURSO", False)


def _graph(*kws):
    g = Graph()
    for kw in kws:
        g.add_node(Node(keyword=kw, turn=1, topic="t", domain="general", sentiment="neutral"))
    return g


def _rows(path, table):
    with sqlite3.connect(path) as c:
        return c.execute(f"SELECT owner, space FROM {table}").fetchall()


def test_new_rows_carry_owner_and_space(tmp_path, ada):
    g = _graph("proxy", "firewall")
    g.add_link(Link(source="proxy", target="firewall", link_type="deepening",
                    weight="medium", rationale="", created_turn=1, last_active_turn=1))
    g.add_episode("proxy", "il proxy rifiuta il handshake wss", 1)
    path = str(tmp_path / "g.db")
    g.save_sqlite(path)
    for table in ("nodes", "links", "episodes"):
        assert set(_rows(path, table)) == {("ada", "personale:ada")}, table


def test_the_creator_stays_the_owner(tmp_path, ada, monkeypatch):
    """Updates do not take authorship: ON CONFLICT never names owner/space."""
    path = str(tmp_path / "g.db")
    _graph("proxy").save_sqlite(path)
    monkeypatch.setenv("NEURON_USER", "bruno")
    g = Graph()
    g.load_sqlite(path)
    g.get_node("proxy").salience += 3
    g.mark_node_dirty("proxy")
    g.add_node(Node(keyword="cache", turn=2, topic="t", domain="general", sentiment="neutral"))
    g.save_sqlite(path)
    with sqlite3.connect(path) as c:
        owners = dict(c.execute("SELECT keyword, owner FROM nodes").fetchall())
    assert owners == {"proxy": "ada", "cache": "bruno"}


def _legacy(path):
    """A store as it was before 4a: same data, no owner/space columns.

    The cached engine connection is closed first: a legacy file is migrated by
    a NEW process, with a fresh open. A connection cached across an outside DDL
    does not see it — the second-opener hazard, not the migration."""
    cached = _db._local_conn_cache.pop(path, None)
    if cached is not None:
        cached.close()
    c = sqlite3.connect(path)
    try:
        for table in ("nodes", "links", "episodes"):
            c.execute(f"ALTER TABLE {table} DROP COLUMN owner")
            c.execute(f"ALTER TABLE {table} DROP COLUMN space")
        c.commit()
    finally:
        c.close()


def test_a_local_legacy_file_becomes_its_users(tmp_path, ada):
    path = str(tmp_path / "g.db")
    g = _graph("proxy")
    g.add_episode("proxy", "fatto", 1)
    g.save_sqlite(path)
    _legacy(path)
    g2 = Graph()
    g2.load_sqlite(path)
    g2.add_node(Node(keyword="cache", turn=2, topic="t", domain="general", sentiment="neutral"))
    g2.save_sqlite(path)                                   # the migration runs on save
    assert set(_rows(path, "nodes")) == {("ada", "personale:ada")}
    assert set(_rows(path, "episodes")) == {("ada", "personale:ada")}


def test_a_shared_store_keeps_old_rows_unattributed(tmp_path, ada, monkeypatch):
    path = str(tmp_path / "g.db")
    _graph("proxy").save_sqlite(path)
    _legacy(path)
    monkeypatch.setattr(_db, "REMOTE_TURSO", True)          # the team's shared DB
    with sqlite3.connect(path) as c:
        Graph()._ensure_schema(c, "default")
        assert c.execute("SELECT owner, space FROM nodes").fetchall() == [("", "")]


def test_the_space_follows_the_store(monkeypatch, ada):
    from neuron.models import current_space
    assert current_space() == "personale:ada"
    monkeypatch.setattr(_db, "REMOTE_TURSO", True)
    assert current_space() == "team:shared"
    monkeypatch.setenv("NEURON_SPACE", "team:backend")
    assert current_space() == "team:backend"
