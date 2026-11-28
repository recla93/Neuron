"""A fact keeps its OWN concepts in a turn of several facts (2026-11-28).

A turn keeps at most MAX_KEYWORDS_PER_TURN keywords. The concepts of the last
facts fell past that cap, found no keyword, and the fact was indexed under the
whole turn's keywords: the other facts' concepts. Live, a minor pending stored
fifth in a batch of five got 'blocker' and showed as a blocker."""
import asyncio

import pytest


@pytest.fixture
def S(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from neuron import server as S
    from neuron.registry import GraphRegistry
    monkeypatch.setattr(S, "_g", GraphRegistry(graphs_dir=str(tmp_path)))
    monkeypatch.setattr(S, "TURSO_ENGINE", False)
    S._g.switch("graymatter")
    return S


def _store(S, facts):
    return asyncio.run(S.call_tool("store_turn", {"facts": facts}))[0].text


def _concepts(S, text):
    g = S._g.get()
    return next(f["concepts"] for f in g.facts.values() if f["text"].startswith(text))


def test_concepts_past_the_turn_cap_stay_on_their_fact(S):
    out = _store(S, [
        {"kind": "decision", "text": "First fact about the gate.", "concepts": ["blocker", "gate", "release", "tags"]},
        {"kind": "fact", "text": "Second fact about the bench.", "concepts": ["bench", "noise", "recall", "baseline"]},
        {"kind": "fact", "text": "Third fact about the inbox.", "concepts": ["inbox", "triage", "home", "graph"]},
    ])
    third = _concepts(S, "Third fact")
    assert "inbox" in third and "triage" in third, out
    assert not {"blocker", "gate", "bench"} & set(third), third


def test_a_fact_whose_concepts_all_fall_never_takes_the_others(S):
    _store(S, [
        {"kind": "decision", "text": "A decision on the blockers.", "concepts": ["blocker", "release"]},
        {"kind": "fact", "text": "A fact with verbs only.", "concepts": ["sistemare", "verificare"]},
    ])
    assert "blocker" not in _concepts(S, "A fact with verbs only")


def test_a_single_fact_turn_is_unchanged(S):
    _store(S, [{"kind": "fact", "text": "Lunch is at noon.", "concepts": ["lunch", "schedule"]}])
    assert _concepts(S, "Lunch is at noon") == ["lunch", "schedule"]
