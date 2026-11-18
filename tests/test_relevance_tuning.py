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


# --- ADR-016 D2: the flash is a dormant fact, or nothing ----------------------

def _flash_graph(tmp_path, monkeypatch):
    g, call = _setup(tmp_path, monkeypatch, {"aspect": [1.0, 0.0, 0.0],
                                             "eventi": [1.0, 0.0, 0.0]})
    _node(g, "stimoli")
    g.add_fact("idea", "eventi in stile aspect per gli stimoli", ["stimoli"], 1)
    g.add_fact("habit", "l'utente vuole il piano prima: aspect", ["stimoli"], 1)
    g.turn_count = 20
    return g, call


def test_forgotten_facts_returns_a_dormant_idea_with_its_id(tmp_path, monkeypatch):
    """Not recalled (the question is about something else), but near it. A
    habit is the user's, never a flash."""
    g, call = _flash_graph(tmp_path, monkeypatch)
    monkeypatch.setattr(S, "FACT_MIN_SCORE", 2.0)       # nothing is recalled here
    out = call("forgotten", {"facts": True, "near": "eventi e stimoli del daemon", "top_n": 5})
    assert "[idea] eventi in stile aspect per gli stimoli (stimoli) #" in out, out
    assert "habit" not in out and "piano" not in out, out
    import re
    fid = re.search(r"#([0-9a-f]{8})", out).group(1)
    again = call("forgotten", {"facts": True, "near": "eventi e stimoli del daemon", "exclude": [fid]})
    assert again.startswith("No dormant fact"), "an excluded fact (already flashed) does not come back"


def test_forgotten_facts_skips_what_the_recall_already_shows(tmp_path, monkeypatch):
    g, call = _flash_graph(tmp_path, monkeypatch)
    out = call("forgotten", {"facts": True, "near": "eventi e stimoli del daemon"})
    assert out.startswith("No dormant fact"), f"the recall shows it already: {out}"


def test_forgotten_facts_needs_the_fact_to_be_dormant(tmp_path, monkeypatch):
    g, call = _flash_graph(tmp_path, monkeypatch)
    monkeypatch.setattr(S, "FACT_MIN_SCORE", 2.0)
    g.turn_count = 3                                       # written 2 turns ago
    out = call("forgotten", {"facts": True, "near": "eventi e stimoli del daemon", "threshold": 5})
    assert out.startswith("No dormant fact"), out


def test_forgotten_near_with_nothing_near_says_so(tmp_path, monkeypatch):
    """The fallback to the oldest node answered every question: GM showed it as
    a flash ("che tempo fara' domani a Milano?" -> `clipboard`)."""
    g, call = _setup(tmp_path, monkeypatch, {"clipboard": [0.0, 1.0, 0.0]})
    g.add_node(Node(keyword="clipboard", turn=1, topic="t", domain="general",
                    sentiment="neutral", salience=3))
    g.turn_count = 20
    out = call("forgotten", {"near": "che tempo fara domani a Milano?"})
    assert out.startswith("No dormant concept near"), out
    assert "clipboard" not in out


def test_a_migrated_fact_can_be_a_flash(tmp_path, monkeypatch):
    """Live on 2026-11-08 a migrated graph had no flash at all: its facts are
    kind `fact` only because the old episodes had no kind."""
    g, call = _flash_graph(tmp_path, monkeypatch)
    monkeypatch.setattr(S, "FACT_MIN_SCORE", 2.0)
    g.add_fact("fact", "eventi del bus interno, decisi a luglio", ["stimoli"], 1, source="migration")
    g.add_fact("fact", "eventi scritti dal modello, tipo fatto", ["stimoli"], 1)
    out = call("forgotten", {"facts": True, "near": "eventi e stimoli del daemon", "top_n": 5})
    assert "eventi del bus interno" in out, out
    assert "scritti dal modello" not in out, "a model's `fact` is a detail, not a flash"


def test_a_flash_must_be_linked_not_only_near(tmp_path, monkeypatch):
    """Live on 2026-11-08 the bench fact flashed on a question about keyword
    casing at 0.49: near, unrelated. A flash shares a concept with what the
    recall shows, or the message names it."""
    g, call = _flash_graph(tmp_path, monkeypatch)
    monkeypatch.setattr(S, "FACT_MIN_SCORE", 2.0)
    out = call("forgotten", {"facts": True, "near": "eventi del daemon"})   # names no concept
    assert out.startswith("No dormant fact"), out


def test_italian_verb_endings_name_the_concept():
    """A usage test on 2026-11-08: "possiamo committare queste modifiche?" did
    not reach the habit indexed under `commit`."""
    from neuron.search import _names, _words
    for msg, concept in (("possiamo committare?", "commit"), ("committiamo ora", "commit"),
                         ("pushiamo la release", "push"), ("le wheels vecchie", "wheel")):
        assert _names(concept, _words(msg)), (msg, concept)
    assert not _names("test", _words("testimonianze del processo")), "a short stem must not reach every word"


# --- ADR-015 D7.1: user/habit facts whatever the message, once per session ---

def test_profile_brings_user_and_habit_facts_on_any_message(tmp_path, monkeypatch):
    """"possiamo committare?" scored 0.33 against the habit about commits, under
    the 0.40 floor (live, 2026-11-08): by similarity it never came back."""
    g, call = _setup(tmp_path, monkeypatch, {"turso": [1.0, 0.0, 0.0]})
    _node(g, "commit")
    habit = g.add_fact("habit", "Never commit before a real usage test.", ["commit"], 1)
    user = g.add_fact("user", "The user is Italian and codes in English.", ["lingua"], 2)
    old = g.add_fact("habit", "Old habit, replaced.", ["commit"], 1)
    g.add_fact("habit", "New habit.", ["commit"], 3, supersedes=old["id"])
    g.add_fact("decision", "Turso in local too.", ["turso"], 1)

    out = call("pre_turn", {"topic": "carbonara recipe", "profile": True})
    assert "profile: [habit] New habit." in out, out          # newest first
    for f in (habit, user):
        assert f"#{f['id']}" in out, out
    assert "Old habit" not in out and "Turso in local" not in out, out
    assert "profile:" not in call("pre_turn", {"topic": "carbonara recipe"})


def test_session_start_says_where_the_work_stopped(tmp_path, monkeypatch):
    """Fable's need 2 (2026-11-18): the session's first call carries the newest
    progress facts, dated; those on a concept the message names come first.
    Not on later calls, not repeated among the facts."""
    g, call = _setup(tmp_path, monkeypatch, {})
    _node(g, "neuron")
    old = g.add_fact("progress", "Neuron 7 bench done.", ["neuron"], 1)
    g.add_fact("progress", "mocoder icon fixed.", ["mocoder"], 2)
    g.add_fact("progress", "mocoder tests green.", ["mocoder"], 3)
    for i, f in enumerate(sorted(g.facts.values(), key=lambda f: f["turn"])):   # 15/11, 16/11, 17/11
        f["created_at"] = f"2026-11-1{5 + i}T10:00:00"
    out = call("pre_turn", {"topic": "dove eravamo con neuron?", "profile": True})
    last = out.split("last: ", 1)[1]
    assert last.startswith(f"[progress] Neuron 7 bench done. #{old['id']} (15/11)"), out   # named first, dated
    assert "mocoder tests green" in last and "mocoder icon" not in last, out              # then the newest; 2 in all
    assert out.count(f"#{old['id']}") == 1, out
    assert "last:" not in call("pre_turn", {"topic": "dove eravamo con neuron?"}), "only at session start"


def test_a_handoff_retires_the_previous_one_and_opens_the_next_session(tmp_path, monkeypatch):
    """2026-11-18: the newest progress facts were the day's small steps; the
    session opens with the current handoff instead, and only one is current."""
    g, call = _setup(tmp_path, monkeypatch, {})
    call("store_turn", {"facts": [{"kind": "progress", "text": "Handoff A: bench pending.", "concepts": ["handoff"]}]})
    a = next(i for i, f in g.facts.items() if f["text"].startswith("Handoff A"))
    out = call("store_turn", {"facts": [{"kind": "progress", "text": "Handoff B: bench done, release not ready.",
                                         "concepts": ["handoff", "release"]}]})
    b = next(i for i, f in g.facts.items() if f["text"].startswith("Handoff B"))
    assert g.facts[a]["status"] == "superseded" and f"retires handoff #{a}" in out, out
    call("store_turn", {"facts": [{"kind": "progress", "text": "Small step after the handoff.", "concepts": ["misc"]}]})
    last = call("pre_turn", {"topic": "ciao", "profile": True}).split("last: ", 1)[1]
    assert last.startswith("[progress] Handoff B"), last
    assert "Small step after the handoff." in last and "Handoff A" not in last, last


def test_profile_respects_its_cap_and_is_not_repeated_as_a_fact(tmp_path, monkeypatch):
    g, call = _setup(tmp_path, monkeypatch, {"commit": [1.0, 0.0, 0.0]})
    _node(g, "commit")
    keep = g.add_fact("habit", "commit only after a usage test", ["commit"], 2)
    g.add_fact("habit", "x" * 1100, ["commit"], 1)           # older, past the cap
    out = call("pre_turn", {"topic": "commit", "keywords": ["commit"], "profile": True})
    assert out.count(f"#{keep['id']}") == 1, out             # in the profile, not again in facts
    profile = out.split("profile: ", 1)[1].split(" | facts:", 1)[0]
    assert "x" * 50 not in profile, out          # past the cap; relevance may still bring it


def test_a_fact_shown_in_the_session_does_not_come_back(tmp_path, monkeypatch):
    """ADR-015 D7: live 2026-11-08 the habit on releases came in the profile and
    again among the relevant facts of the next message."""
    g, call = _setup(tmp_path, monkeypatch, {"release": [1.0, 0.0, 0.0]})
    _node(g, "release")
    habit = g.add_fact("habit", "No push if anything breaks in use.", ["release"], 1)
    fix = g.add_fact("fix", "The release script now checks the tag.", ["release"], 2)

    first = call("pre_turn", {"topic": "release", "keywords": ["release"]})
    assert f"#{habit['id']}" in first and f"#{fix['id']}" in first, first
    again = call("pre_turn", {"topic": "release", "keywords": ["release"],
                              "exclude": [habit["id"], f"#{fix['id']}"], "profile": True})
    assert f"#{habit['id']}" not in again and f"#{fix['id']}" not in again, again




def test_a_note_on_a_fact_is_shown_next_to_it_and_facts_carry_their_author(tmp_path, monkeypatch):
    """A fact linked with `disputes` is a note on another (a correction, not a
    replacement): recall shows it beside it (2026-11-18: typed; a citation of
    #id in the text is only a mention). And `source` says which
    client wrote a fact — it said "model" for 300 of 319 live facts."""
    g, call = _setup(tmp_path, monkeypatch, {"turso": [1.0, 0.0, 0.0]})
    call("store_turn", {"by": "Claude Code", "facts": [
        {"kind": "decision", "text": "turso runs embedded on every client", "concepts": ["turso"]}]})
    fid = next(iter(g.facts))
    assert g.facts[fid]["source"] == "claude-code"
    call("store_turn", {"facts": [
        {"kind": "fact", "text": f"Progress on #{fid}: still being measured", "concepts": ["kimi"]},
        {"kind": "fix", "text": "kimi still runs on sqlite3", "concepts": ["kimi"],
         "link": {"type": "disputes", "to": fid}}]})
    mention = next(i for i, f in g.facts.items() if f["text"].startswith("Progress on"))
    note = next(i for i, f in g.facts.items() if f["text"].startswith("kimi still"))
    assert g.facts[note]["source"] == "model"          # no `by`, no handshake
    out = call("pre_turn", {"topic": "turso embedded"})
    # a correction shows beside the fact with what it says; a mere mention of #id does not
    assert f"#{fid} ↳#{note}: kimi still runs on sqlite3" in out, out
    assert f"↳#{mention}" not in out, out


def test_a_procedure_lists_its_solutions_and_the_links_survive_a_reload(tmp_path, monkeypatch):
    """2026-11-18: solutions linked to a procedure come under it, worked first,
    failed last and marked, and are not shown twice. A bad link is refused
    with a note, the fact kept. The links are rows in `fact_links`."""
    g, call = _setup(tmp_path, monkeypatch, {"search": [1.0, 0.0, 0.0]})
    call("store_turn", {"facts": [{"kind": "procedure", "concepts": ["websearch"],
        "text": "When web search fails: fall back to another source.", "evidence": "used 14/11"}]})
    pid = next(iter(g.facts))
    out = call("store_turn", {"facts": [
        {"kind": "fact", "text": "retry websearch: search keeps failing", "concepts": ["websearch"],
         "link": {"type": "failed_for", "to": pid}},
        {"kind": "fix", "text": "webfetch duckduckgo html: search works", "concepts": ["websearch"],
         "link": {"type": "solves", "to": "#" + pid}},
        {"kind": "idea", "text": "official docs url: search untested", "concepts": ["websearch"],
         "link": {"type": "solves", "to": pid}},
        {"kind": "fact", "text": "a link to nowhere", "concepts": ["websearch"],
         "link": {"type": "solves", "to": "deadbeef"}}]})
    assert "not linked: no fact #deadbeef" in out, out
    rec = call("pre_turn", {"topic": "web search fails"})
    line = next(l for l in rec.splitlines() if "[procedure]" in l)
    order = [line.index(s) for s in ("1) webfetch duckduckgo", "2) official docs", "3) ✗ retry websearch")]
    assert order == sorted(order), line
    assert "[failed #" in line and "[untried #" in line and "[proven #" in line, line
    assert rec.count("retry websearch") == 1, rec          # not twice
    S._g.save()
    import sqlite3
    from neuron.models import Graph
    db = next(tmp_path.glob("graph_*.db"))
    rows = sqlite3.connect(db).execute("SELECT type FROM fact_links").fetchall()
    assert sorted(r[0] for r in rows) == ["failed_for", "solves", "solves"], rows
    again = Graph()
    again.load_sqlite(str(db), context="default")
    assert again.fact_links == g.fact_links, (again.fact_links, g.fact_links)
