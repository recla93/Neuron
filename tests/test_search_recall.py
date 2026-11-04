"""The vector fallback ranks nodes by what they know, not by their name.

A question in plain language ("il python lanciato dal repo importa i pacchetti
sbagliati") was compared with node NAMES (`cwd-shadowing`): on the real `ai`
graph the right node reached the top 5 for 3 questions out of 14. Against name +
topic + facts, 8 out of 14 (2026-11-04).
"""
import zlib

import pytest

from neuron import server as S
from neuron.models import Graph, Node


class _WordEmbed:
    """One dimension per word: two texts are close when they share words."""
    def embed(self, texts):
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


def test_a_new_fact_changes_what_the_node_answers_to(words):
    g = _graph()
    assert S._search_recall("torta mele forno", 1, g)[0][0] == "beta"
    g.add_episode("alfa", "torta mele forno forno forno", 2)
    assert S._search_recall("torta mele forno", 1, g)[0][0] == "alfa"
