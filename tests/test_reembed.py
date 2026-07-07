"""E0.3 — test del core di reembed (SQL) con sqlite reale, senza fastembed."""
import importlib.util
import os
import sqlite3
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    path = os.path.join(_ROOT, "scripts", "reembed.py")
    spec = importlib.util.spec_from_file_location("reembed", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


re_mod = _load()


def _make_db(with_context=True, dim_old=8):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    if with_context:
        c.execute("CREATE TABLE nodes (context TEXT, keyword TEXT)")
        c.execute("CREATE TABLE node_vectors (context TEXT, keyword TEXT, embedding BLOB, dim INTEGER, PRIMARY KEY(context,keyword))")
        c.executemany("INSERT INTO nodes VALUES (?,?)", [("default", "alpha"), ("java/spring", "bean")])
        c.executemany("INSERT INTO node_vectors VALUES (?,?,?,?)",
                      [("default", "alpha", b"x" * dim_old, dim_old),
                       ("java/spring", "bean", b"x" * dim_old, dim_old)])
    else:
        c.execute("CREATE TABLE nodes (keyword TEXT)")
        c.execute("CREATE TABLE node_vectors (context TEXT, keyword TEXT, embedding BLOB, dim INTEGER, PRIMARY KEY(context,keyword))")
        c.execute("INSERT INTO nodes VALUES ('alpha')")
    c.commit()
    return path, c


def _fake_embed(kw):
    return [0.0, 1.0, 2.0]          # 3-dim


def _fake_pack(vec):
    return bytes(len(vec))


def test_reembed_rewrites_vectors_and_meta():
    path, c = _make_db()
    try:
        rep = re_mod.reembed_conn(c, _fake_embed, _fake_pack, dim=3, model_name="new-model")
        assert rep["nodes"] == 2
        assert rep["contexts"] == ["default", "java/spring"]
        dims = dict(c.execute("SELECT keyword, dim FROM node_vectors").fetchall())
        assert dims == {"alpha": 3, "bean": 3}          # ridimensionati al nuovo modello
        meta = dict(c.execute("SELECT key, value FROM meta").fetchall())
        assert meta["embed_model"] == "new-model" and meta["embed_dim"] == "3"
    finally:
        c.close(); os.unlink(path)


def test_reembed_dry_run_writes_nothing():
    path, c = _make_db(dim_old=8)
    try:
        rep = re_mod.reembed_conn(c, _fake_embed, _fake_pack, dim=3, model_name="new-model", dry_run=True)
        assert rep["dry_run"] is True and rep["nodes"] == 2
        dims = {d for (d,) in c.execute("SELECT dim FROM node_vectors").fetchall()}
        assert dims == {8}                               # invariato
        assert c.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 0
    finally:
        c.close(); os.unlink(path)


def test_reembed_legacy_without_context_column():
    path, c = _make_db(with_context=False)
    try:
        rep = re_mod.reembed_conn(c, _fake_embed, _fake_pack, dim=3, model_name="m")
        assert rep["contexts"] == ["default"]
        row = c.execute("SELECT context, keyword, dim FROM node_vectors").fetchone()
        assert row == ("default", "alpha", 3)
    finally:
        c.close(); os.unlink(path)
