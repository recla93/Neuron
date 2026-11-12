"""The vector fallback ranks nodes by what they know, not by their name.

A question in plain language ("il python lanciato dal repo importa i pacchetti
sbagliati") was compared with node NAMES (`cwd-shadowing`): on the real `ai`
graph the right node reached the top 5 for 3 questions out of 14. Against name +
topic + facts, 8 out of 14 (2026-11-04).
"""
import threading
import zlib

import pytest

from neuron import search
from neuron import server as S
from neuron.models import Graph, Node


class _WordEmbed:
    """One dimension per word: two texts are close when they share words."""
    def embed(self, texts, **_kw):  # batch_size, as fastembed
        for t in texts:
            v = [0.0] * 384
            for w in t.lower().replace(".", " ").split():
                v[zlib.crc32(w.encode()) % 384] += 1.0
            yield v


@pytest.fixture
def words(monkeypatch):
    monkeypatch.setattr(S, "_embedder", _WordEmbed())
    monkeypatch.setattr(S, "_embed_cache", {})
    monkeypatch.setattr(S, "_embed_dim_checked", True)
    monkeypatch.setattr(search, "_RECALL_VECS", {})
    monkeypatch.setattr(search, "_recall_disk", None)
    monkeypatch.setattr(search, "_recall_warming", False)


def _graph():
    g = Graph()
    for kw in ("alfa", "beta"):
        g.add_node(Node(keyword=kw, turn=1, topic="t", domain="general", sentiment="neutral"))
    g.add_episode("alfa", "pacchetti importati dalla cartella sbagliata", 1)
    g.add_episode("beta", "ricetta della torta di mele", 1)
    return g


def test_a_node_is_found_by_its_facts_not_its_name(words):
    hits = S._search_recall("pacchetti importati cartella sbagliata", 2, _graph())
    assert hits[0][0] == "alfa", hits


def test_a_node_with_many_facts_is_judged_on_its_best_one(words):
    """All facts in one text diluted the one that answers: a node with five
    facts lost to a node with a single half-match. One text per fact, best wins."""
    g = Graph()
    for kw in ("alfa", "beta"):
        g.add_node(Node(keyword=kw, turn=1, topic="t", domain="general", sentiment="neutral"))
    for i, fact in enumerate(("uno due tre quattro cinque sei", "sette otto nove dieci undici",
                              "rosso verde blu giallo viola", "nord sud est ovest centro",
                              "pacchetti cartella sbagliata"), 1):
        g.add_episode("alfa", fact, i)
    g.add_episode("beta", "pacchetti cartella vuota", 1)
    assert S._search_recall("pacchetti cartella sbagliata", 1, g)[0][0] == "alfa"


def test_a_new_fact_changes_what_the_node_answers_to(words):
    g = _graph()
    assert S._search_recall("torta mele forno", 1, g)[0][0] == "beta"
    g.add_episode("alfa", "torta mele forno forno forno", 2)
    assert S._search_recall("torta mele forno", 1, g)[0][0] == "alfa"


def test_a_big_backlog_is_embedded_in_the_background(words, monkeypatch):
    """The whole `ai` graph took 47 s to embed: the first query after an
    install must not wait for it. It answers with what is there."""
    gate = threading.Event()

    class _Slow(_WordEmbed):
        def embed(self, texts, **_kw):  # batch_size, as fastembed
            gate.wait(5)
            yield from super().embed(texts)

    g = _graph()                       # names embedded before the slow model arrives
    monkeypatch.setattr(S, "_embedder", _Slow())
    monkeypatch.setattr(search, "_get_embedding", lambda t: next(_WordEmbed().embed([t])))
    monkeypatch.setattr(search, "_RECALL_INLINE_MAX", 1)
    assert S._search_recall("torta mele", 1, g) == []          # returned, did not block
    gate.set()
    for t in threading.enumerate():
        if t.name == "recall-warm":
            t.join(5)
    assert S._search_recall("torta mele", 1, g)[0][0] == "beta"


def test_vectors_survive_a_restart(words, monkeypatch, tmp_path):
    monkeypatch.setattr(search, "_persistable", lambda emb: True)
    monkeypatch.setattr(search, "_recall_path", lambda: str(tmp_path / "rv.bin"))
    g = _graph()
    S._search_recall("pacchetti importati", 1, g)
    assert (tmp_path / "rv.bin").exists()

    class _Refuse:
        def embed(self, texts, **_kw):  # batch_size, as fastembed
            raise AssertionError(f"re-embedded after a restart: {list(texts)}")

    monkeypatch.setattr(search, "_RECALL_VECS", {})          # a new process
    monkeypatch.setattr(search, "_recall_disk", None)
    monkeypatch.setattr(S, "_embedder", _Refuse())
    monkeypatch.setattr(search, "_get_embedding", lambda t: next(_WordEmbed().embed([t])))
    assert S._search_recall("pacchetti importati cartella", 1, g)[0][0] == "alfa"


# --- ADR-016 D6: the recall is usable early on a cold cache --------------------

def test_facts_are_embedded_before_node_texts(words, monkeypatch):
    """With a cold cache the recall waited for every node text too: 44 s on a
    137-text graph (2026-11-08). Facts come first, so they are usable early."""
    order = []
    real = search._embed_recall
    monkeypatch.setattr(search, "_RECALL_INLINE_MAX", 1000)
    monkeypatch.setattr(search, "_embed_recall",
                        lambda emb, eid, texts: order.extend(texts) or real(emb, eid, texts))
    g = _graph()
    search._ensure_recall_vecs(g)
    facts = {search._fact_text(f) for f in g.facts.values()}
    assert set(order[:len(facts)]) == facts, order


def test_a_stored_fact_has_its_vector_at_once(words, tmp_path, monkeypatch):
    """ADR-015 §3.2: the vector is made at the write, so the next recall finds
    the fact even while a cold cache warms in the background."""
    import asyncio
    from neuron.registry import GraphRegistry
    monkeypatch.setattr(S, "_g", GraphRegistry(graphs_dir=str(tmp_path)))
    monkeypatch.setattr(S, "TURSO_ENGINE", False)
    asyncio.run(S.call_tool("store_turn", {"facts": [
        {"kind": "fix", "text": "pacchetti importati dalla cartella sbagliata", "concepts": ["python"]}]}))
    f = next(iter(S._g.get().facts.values()))
    assert (id(S._embedder), search._fact_text(f)) in search._RECALL_VECS


def test_a_new_fact_close_to_an_active_one_is_reported_not_merged(words, tmp_path, monkeypatch):
    """ADR-015 D6: live 2026-11-08 one habit was stored five times, restated.
    The answer names the close one; the model decides with `supersedes`."""
    import asyncio
    from neuron.registry import GraphRegistry
    monkeypatch.setattr(S, "_g", GraphRegistry(graphs_dir=str(tmp_path)))
    monkeypatch.setattr(S, "TURSO_ENGINE", False)
    call = lambda facts: asyncio.run(S.call_tool("store_turn", {"facts": facts}))[0].text  # noqa: E731
    call([{"kind": "habit", "text": "no push if something breaks in use", "concepts": ["push"]}])
    old = next(iter(S._g.get().facts.values()))

    out = call([{"kind": "habit", "text": "no push if anything breaks in use", "concepts": ["push"]}])
    assert f"is similar to #{old['id']}" in out and f'supersedes="{old["id"]}"' in out, out
    assert len([f for f in S._g.get().facts.values() if f["status"] == "active"]) == 2, "never merged"

    other_kind = call([{"kind": "fix", "text": "no push if something breaks in use", "concepts": ["push"]}])
    assert "is similar to" not in other_kind, other_kind
    replaced = call([{"kind": "habit", "text": "no push when something breaks in use",
                      "concepts": ["push"], "supersedes": old["id"]}])
    assert "is similar to" not in replaced, replaced


def test_a_shown_fact_written_about_or_cited_counts_one_use(words, tmp_path, monkeypatch):
    """ADR-015 D8: shown by pre_turn, then written about (a shared concept) or
    named by #id in the next store_turn = used. The profile never counts: it is
    always shown."""
    import asyncio
    from neuron.registry import GraphRegistry
    monkeypatch.setattr(S, "_g", GraphRegistry(graphs_dir=str(tmp_path)))
    monkeypatch.setattr(S, "TURSO_ENGINE", False)
    call = lambda tool, args: asyncio.run(S.call_tool(tool, args))[0].text  # noqa: E731
    call("store_turn", {"facts": [
        {"kind": "fix", "text": "pacchetti importati dalla cartella sbagliata", "concepts": ["python"]},
        {"kind": "habit", "text": "the user tests before committing", "concepts": ["python"]}]})
    g = S._g.get()
    fix, habit = (next(f for f in g.facts.values() if f["kind"] == k) for k in ("fix", "habit"))

    shown = call("pre_turn", {"topic": "pacchetti importati cartella sbagliata",
                              "keywords": ["python"], "profile": True})
    assert f"#{fix['id']}" in shown and f'confirm(facts=["{fix["id"]}"])' in shown, shown
    call("store_turn", {"facts": [{"kind": "decision", "text": "run python with -I", "concepts": ["python"]}]})
    assert (fix["confirmed"], habit["confirmed"]) == (1, 0)
    call("store_turn", {"facts": [{"kind": "decision", "text": "unrelated", "concepts": ["rust"]}]})
    assert fix["confirmed"] == 1, "nothing shown since: no use"
    call("store_turn", {"facts": [{"kind": "idea", "text": f"generalize #{fix['id']}", "concepts": ["rust"]}]})
    assert fix["confirmed"] == 2
    out = call("confirm", {"facts": [f"#{fix['id']}"]})
    assert fix["id"] in out and fix["confirmed"] == 3


# --- the gist (2026-11-12): recall shows the short form, search reads the text ----

def test_a_gist_is_stored_shown_and_expanded(words, tmp_path, monkeypatch):
    import asyncio
    from neuron.registry import GraphRegistry
    monkeypatch.setattr(S, "_g", GraphRegistry(graphs_dir=str(tmp_path)))
    monkeypatch.setattr(S, "TURSO_ENGINE", False)
    call = lambda tool, args: asyncio.run(S.call_tool(tool, args))[0].text  # noqa: E731
    long_text = "pacchetti importati dalla cartella sbagliata perche' -m mette la cwd in testa a sys.path"
    call("store_turn", {"facts": [{"kind": "fix", "text": long_text, "concepts": ["python"],
                                   "gist": "fix: -m imports from cwd -> run with -I"}]})
    f = next(iter(S._g.get().facts.values()))
    assert f["gist"] == "fix: -m imports from cwd -> run with -I"

    out = call("pre_turn", {"topic": "pacchetti importati cartella sbagliata"})
    assert "fix: -m imports from cwd -> run with -I +" in out and long_text not in out, out
    assert long_text in call("pre_turn", {"topic": f"#{f['id']}"})        # the full text on request
    assert "No fact with id" in call("pre_turn", {"topic": "#deadbeef"})
    question = call("pre_turn", {"topic": f"cosa dice #{f['id']} sui pacchetti?"})   # a question:
    assert question.startswith("[neuron] ctx=") and long_text not in question, question  # normal recall

    # The gist survives a save and a reload of the store.
    S._g.get().save_sqlite(S._g._db_path(S._g.active), context=S._g.active, force=True)
    monkeypatch.setattr(S, "_g", GraphRegistry(graphs_dir=str(tmp_path)))
    assert S._g.get().facts[f["id"]]["gist"] == "fix: -m imports from cwd -> run with -I"


def test_a_fact_without_gist_is_shown_as_before():
    from neuron.models import fact_shown
    assert fact_shown({"text": "full text", "gist": ""}) == "full text"
    assert fact_shown({"text": "full text"}) == "full text"
    assert fact_shown({"text": "full text", "gist": "short"}) == "short +"


def test_an_old_store_without_the_gist_column_loads_and_is_upgraded(tmp_path):
    """A store written before the gist reads '' and gets the column at its next save."""
    from neuron import db as _db
    from neuron.models import Graph
    path = str(tmp_path / "graph_default.db")
    g = Graph()
    g.add_fact("fix", "old fact", ["x"], 1)
    g.save_sqlite(path, context="default", force=True)
    con = _db.connect_local(path)   # the store's own driver: stdlib sqlite3 misses its WAL
    con.execute("ALTER TABLE facts DROP COLUMN gist")
    con.commit()
    con.close()
    g2 = Graph()
    g2.load_sqlite(path, context="default")
    assert next(iter(g2.facts.values()))["gist"] == ""
    g2.save_sqlite(path, context="default", force=True)
    con = _db.connect_local(path)
    assert "gist" in {r[1] for r in con.execute("PRAGMA table_info(facts)")}
    con.close()
