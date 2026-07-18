"""C2 (consolidate trust-aware) + C3 (introspect)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def _node(kw, sal=0, trust=0.0, turn=1, vec=None):
    from neuron.models import Node
    return Node(keyword=kw, turn=turn, topic="t", domain="d", sentiment="neutral",
                salience=sal, trust=trust, vector=vec)


def test_trusted_orphan_survives_drop():
    """C2 — salience bassa ma trust alto: il nodo non è un orfano da droppare."""
    from neuron.models import Graph
    g = Graph(); g.turn_count = 20
    g.add_node(_node("fragile", sal=1))            # sotto soglia, nessun link
    g.add_node(_node("fidato", sal=1, trust=2.0))  # sotto soglia MA confermato
    report: list = []
    g._drop_orphans(orphan_salience=2, inactive_turns=10, turn=20, report=report)
    kws = {nd.keyword for nd in g.nodes}
    assert "fidato" in kws and "fragile" not in kws


def test_trusted_node_not_absorbed():
    """C2 — protect_salience conta salience+trust: il gemello fidato sopravvive."""
    from neuron.models import Graph
    g = Graph(); g.turn_count = 5
    v = [1.0, 0.0]
    # il LUNGO verrebbe assorbito (survivor = keyword più corto); il trust lo salva
    g.add_node(_node("kafka_lungo", sal=1, trust=5.0, vec=v))
    g.add_node(_node("kafka", sal=1, trust=0.0, vec=v))
    g.consolidate(sim_threshold=0.9, protect_salience=4)
    kws = {nd.keyword for nd in g.nodes}
    assert kws == {"kafka_lungo", "kafka"}                 # nessun merge: protetto


def test_introspect_shape():
    pytest.importorskip("mcp")
    import asyncio
    import json
    import neuron.server as srv
    from neuron.models import Graph
    g = Graph(); g.turn_count = 12
    g.add_node(_node("forte", sal=9, turn=11))
    g.add_node(_node("fidato", sal=1, trust=3.0, turn=1))
    out = asyncio.run(srv._tool_introspect({}, "", g))
    data = json.loads(out[0].text)
    assert data["nodes"] == 2
    assert data["strongest_memory"][0]["keyword"] == "forte"
    assert data["most_trusted"][0]["keyword"] == "fidato"
    assert data["recent_growth"] == 1                      # cut = 12-10 = 2 → solo 'forte'
    assert "loop_stats" in data
