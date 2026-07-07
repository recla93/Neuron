"""E1.1 — il fallback Python non ri-embedda a ogni ricerca.

Un nodo senza vettore viene embeddato UNA volta (poi cache in memoria) e marcato
dirty per la persistenza. Richiede il modulo server (mcp/fastembed) → importorskip.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def test_fallback_embeds_missing_vector_once_and_persists():
    pytest.importorskip("mcp")
    pytest.importorskip("fastembed")
    import neuron.server as srv
    from neuron.models import Graph, Node

    orig_turso = srv.TURSO_ENGINE
    orig_embed = srv._get_embedding
    srv.TURSO_ENGINE = False  # forza il ramo Python (no vector_distance_cos SQL)
    calls: dict[str, int] = {}

    def counting(text: str):
        calls[text] = calls.get(text, 0) + 1
        return [0.1] * srv.VECTOR_DIM

    srv._get_embedding = counting
    try:
        g = Graph()
        g.add_node(Node(keyword="alpha", turn=1, topic="t", domain="d", sentiment="neutral"))
        g.nodes[0].vector = None            # simula vettore mancante su disco
        g._dirty_vectors.discard("alpha")

        srv._search_embeddings(["query"], top_n=5, graph=g)
        srv._search_embeddings(["query"], top_n=5, graph=g)

        assert calls.get("alpha", 0) == 1        # embeddato una sola volta (poi in cache)
        assert g.nodes[0].vector is not None      # cache in memoria
        assert "alpha" in g._dirty_vectors        # marcato per la persistenza
    finally:
        srv._get_embedding = orig_embed
        srv.TURSO_ENGINE = orig_turso
