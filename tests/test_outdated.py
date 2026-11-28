"""Outdated progress (2026-11-28): a progress fact is a snapshot of a state; once
three newer progress facts share one of its concepts it is labelled outdated
and goes after the current facts. Never removed. Other kinds never age so."""
import pytest


@pytest.fixture
def S(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from neuron import server as S
    from neuron.registry import GraphRegistry
    monkeypatch.setattr(S, "_g", GraphRegistry(graphs_dir=str(tmp_path)))
    monkeypatch.setattr(S, "TURSO_ENGINE", False)
    monkeypatch.setattr(S, "OUTDATED_AFTER", 3)
    S._OUTDATED_CACHE.clear()
    S._g.switch("graymatter")
    return S


def _progress(g, n, concept="release"):
    ids = []
    for i in range(n):
        fid = g.add_fact("progress", f"release state {i} {concept}", [concept], i + 1)["id"]
        g.facts[fid]["created_at"] = f"2026-11-2{i}T10:00:00"   # add_fact returns a report
        ids.append(fid)
    return ids


def test_the_oldest_state_is_outdated_after_three_newer(S):
    g = S._g.get()
    ids = _progress(g, 4)
    assert S._outdated(g) == {ids[0]}
    assert S._fact_line(g, g.facts[ids[0]], "release").startswith("[progress · outdated] ")
    assert S._fact_line(g, g.facts[ids[1]], "release").startswith("[progress] ")


def test_a_shared_concept_is_enough_and_other_kinds_never_age(S):
    g = S._g.get()
    first = g.add_fact("progress", "gate and release state", ["gate", "release"], 1)["id"]
    dec = g.add_fact("decision", "release order: GM first", ["release"], 1)["id"]
    for fid in (first, dec):
        g.facts[fid]["created_at"] = "2026-11-01T00:00:00"
    _progress(g, 3, "gate")
    out = S._outdated(g)
    assert first in out and dec not in out


def test_outdated_facts_go_after_the_current_ones(S, monkeypatch):
    g = S._g.get()
    ids = _progress(g, 4)
    hits = [(g.facts[ids[0]], 0.9, "release"), (g.facts[ids[3]], 0.7, "release")]
    monkeypatch.setattr(S._search, "_search_facts", lambda q, gr, *a, **k: list(hits) if gr is g else [])
    got = [h[0]["id"] for h in S._fact_hits("release", g, 3, 0.4, 0.15)]
    assert got == [ids[3], ids[0]]                    # moved, never dropped


def test_zero_turns_it_off(S, monkeypatch):
    g = S._g.get()
    _progress(g, 5)
    monkeypatch.setattr(S, "OUTDATED_AFTER", 0)
    assert S._outdated(g) == frozenset()
