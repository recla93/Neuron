"""pre_turn and around keep what is relevant to the question, not what is near.

Tuned 2026-10-24 on the two field reports. The embedder is replaced by fixed
vectors, so each test pins the rule, not the model.
"""

import asyncio

from neuron import server as S
from neuron.models import Link, Node
from neuron.registry import GraphRegistry


def _setup(tmp_path, monkeypatch, vectors):
    monkeypatch.setattr(S, "_g", GraphRegistry(graphs_dir=str(tmp_path)))
    monkeypatch.setattr(S, "TURSO_ENGINE", False)
    # One-shot per process: `around` here would spend the loop hint that
    # test_skills_resources expects to see. Restored at teardown.
    monkeypatch.setattr(S, "_loop_hint_sent", S._loop_hint_sent)
    S._turn_search_cache.clear()

    def emb(text):
        for word, v in vectors.items():
            if word in text:
                return v
        return [0.0, 0.0, 1.0]
    monkeypatch.setattr(S, "_get_embedding", emb)
    g = S._g.get()
    return g, lambda tool, args: asyncio.run(S.call_tool(tool, args))[0].text


def _node(g, kw):
    g.add_node(Node(keyword=kw, turn=1, topic="t", domain="general", sentiment="neutral"))


def test_the_fact_relevant_to_the_question_wins_over_the_newest(tmp_path, monkeypatch):
    """`ngrx` carries the facts of every turn it was in; the newest, about
    selectors, came first for a question about HTTP errors."""
    g, call = _setup(tmp_path, monkeypatch, {"errori": [1.0, 0.0, 0.0],
                                             "selettori": [0.0, 1.0, 0.0]})
    _node(g, "ngrx")
    g.add_episode("ngrx", "Gli errori HTTP si gestiscono negli effect.", 1)
    g.add_episode("ngrx", "I selettori memoizzati ricalcolano solo se cambia l'input.", 2)

    out = call("pre_turn", {"topic": "errori HTTP", "keywords": ["ngrx"]})
    assert "errori HTTP si gestiscono" in out, out
    assert "selettori" not in out, f"il fatto fuori tema non e' stato tagliato: {out}"


def test_a_weak_vector_match_does_not_drag_its_links_in(tmp_path, monkeypatch):
    """`focus` (0.33) entered a question about the frontend (0.59), and with it
    every link of the Safari bug."""
    g, call = _setup(tmp_path, monkeypatch, {})
    for kw in ("frontend", "nginx", "focus", "primeng"):
        _node(g, kw)
    for a, b in (("frontend", "nginx"), ("focus", "primeng")):
        g.add_link(Link(source=a, target=b, link_type="deepening", weight="medium",
                        rationale="", created_turn=1, last_active_turn=1))
    monkeypatch.setattr(S, "_search_recall",
                        lambda query, top_n, graph: [("frontend", 0.59), ("focus", 0.33)])

    out = call("pre_turn", {"topic": "dove gira il frontend in produzione"})
    assert "frontend" in out and "nginx" in out, out
    assert "primeng" not in out, f"il nodo debole ha portato dentro i suoi link: {out}"


def test_around_searches_with_the_content_words_of_the_sentence(tmp_path, monkeypatch):
    seen = []
    g, call = _setup(tmp_path, monkeypatch, {})
    monkeypatch.setattr(S, "_get_embedding", lambda t: seen.append(t) or [1.0, 0.0, 0.0])
    _node(g, "focus")

    out = call("around", {"topic": "menu a tendina che perde il focus su browser Apple"})
    probe = seen[0]
    assert "focus" in probe and "tendina" in probe, probe
    assert not {"che", "il", "su", "a"} & set(probe.split()), f"parole vuote nella ricerca: {probe}"
    assert f"as '{probe}'" in out, "l'intestazione deve dire con cosa ha cercato"


# --- ADR-015 D7: facts are recalled by what they say and the concepts named ---

def test_an_off_topic_message_gets_no_fact(tmp_path, monkeypatch):
    """The absolute floor: below it nothing comes back, even with slots free.
    Until 6.9 the five slots filled anyway (the carbonara got 2-3 facts)."""
    g, call = _setup(tmp_path, monkeypatch, {"turso": [1.0, 0.0, 0.0]})
    _node(g, "turso")
    g.add_fact("decision", "Turso anche in locale, il cloud e' facoltativo.", ["turso"], 1)

    out = call("pre_turn", {"topic": "ricetta della carbonara con guanciale"})
    assert "Turso anche in locale" not in out, out


def test_a_message_that_names_the_concept_gets_its_fact(tmp_path, monkeypatch):
    """"TUTTI usano Turso" barely resembles the sentence about Turso in local
    (cos 0.3), but it names the concept: the bonus takes it over the floor.
    The same message without the name does not."""
    g, call = _setup(tmp_path, monkeypatch, {"locale": [0.3, 0.954, 0.0],
                                             "usano": [1.0, 0.0, 0.0]})
    _node(g, "turso")
    g.add_fact("decision", "Tutti e tre i tool girano anche in locale.", ["turso"], 1)

    named = call("pre_turn", {"topic": "TUTTI usano Turso"})
    assert "girano anche in locale" in named, named
    unnamed = call("pre_turn", {"topic": "TUTTI usano il database"})
    assert "girano anche in locale" not in unnamed, unnamed


def test_facts_lead_the_answer_and_carry_kind_and_id(tmp_path, monkeypatch):
    """The budget cut takes the tail: facts first, so links and nodes go before
    a fact does."""
    g, call = _setup(tmp_path, monkeypatch, {"errori": [1.0, 0.0, 0.0]})
    _node(g, "ngrx")
    rep = g.add_fact("fix", "Gli errori HTTP si gestiscono negli effect.", ["ngrx"], 1)

    out = call("pre_turn", {"topic": "errori HTTP", "keywords": ["ngrx"]})
    body = out.split("<recalled-memory", 1)[1]
    assert body.split(">", 1)[1].lstrip().startswith("facts: "), out
    assert f"[fix] Gli errori HTTP si gestiscono negli effect. (ngrx) #{rep['id']}" in out, out
