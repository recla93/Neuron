"""A keyword folded into an existing node by the semantic dedup is that node for
the rest of the turn: its link, its episode, its co-occurrence land there.

They kept the original name. The episode went to a node that does not exist
(lost), the link was written with a dangling endpoint, and a later turn linking
to that name was dropped as "endpoints must be stored concepts". Seen on
2026-10-26: `hook automatico`, stored one turn earlier, could not be linked.
"""

import asyncio

from neuron import server as S
from neuron.registry import GraphRegistry

FACT = "Il framework si avvia da una classe con @SpringBootApplication."


class _SameVector:
    """Every text gets the same direction: any two multi-word keywords merge."""
    def embed(self, texts):
        return [[1.0] + [0.0] * (S.VECTOR_DIM - 1) for _ in texts]


def test_a_merged_keyword_carries_its_link_and_episode_to_the_node(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_g", GraphRegistry(graphs_dir=str(tmp_path)))
    monkeypatch.setattr(S, "_embedder", _SameVector())
    monkeypatch.setattr(S, "_embed_dim_checked", True)
    monkeypatch.setattr(S, "_embed_cache", {})
    monkeypatch.setattr(S, "dedup_enabled", True)
    call = lambda tool, args: asyncio.run(S.call_tool(tool, args))[0].text

    call("store_turn", {"topic": "avvio applicazione", "keywords": ["spring boot", "java"]})
    out = call("store_turn", {
        "topic": "avvio applicazione",
        "keywords": ["spring framework", "annotazione"],
        "links": [{"source": "spring framework", "target": "annotazione",
                   "link_type": "cause-effect"}],
        "episode": FACT})

    g = S._g.get()
    nodes = {nd.keyword for nd in g.nodes}
    assert "spring framework" not in nodes, "the dedup did not merge: the test proves nothing"
    assert "merged" in out and "spring boot" in out, f"the merge is not reported: {out}"
    assert FACT in g.recent_episodes("spring boot"), "the episode was lost"
    assert any(lk.link_type == "cause-effect" and {lk.source, lk.target} == {"spring boot", "annotazione"}
               for lk in g.links), "the link did not reach the merged node"
    dangling = [lk for lk in g.links if lk.source not in nodes or lk.target not in nodes]
    assert not dangling, f"links to nodes that do not exist: {dangling}"

    # The next turn may name the keyword it used: it resolves to the node.
    out = call("store_turn", {
        "topic": "avvio applicazione", "keywords": ["classe"],
        "links": [{"source": "classe", "target": "spring framework"}]})
    assert "dropped link" not in out, out
