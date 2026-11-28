"""The named bonus scaled by concept rarity (NEURON_NAMED_IDF, off by default):
a concept of every fact gets almost nothing, a concept of one fact all of it."""
import neuron.search as ns


def test_rarity_favours_the_rare_concept(monkeypatch):
    monkeypatch.setattr(ns, "NAMED_IDF", 1.0)
    facts = [{"concepts": ["neuron", "turso"]}, {"concepts": ["neuron"]},
             {"concepts": ["neuron"]}, {"concepts": ["neuron", "ci"]}]
    r = ns._concept_rarity(facts)
    assert r["turso"] == 1.0
    assert r["neuron"] == 0.01          # in every fact: the floor
    assert r["neuron"] < r["ci"]
