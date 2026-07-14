"""T66 — balanced stimulus: activation × novelty, path-visible, anti-echo."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neuron.models import Graph, Link, Node  # noqa: E402


def _g():
    g = Graph()
    g.turn_count = 20
    for kw, dom, sal, turn in [("java", "backend", 8, 20), ("spring", "backend", 9, 19),
                               ("servlet", "backend", 4, 15), ("cors", "frontend", 5, 5)]:
        g.add_node(Node(keyword=kw, turn=turn, topic="t", domain=dom,
                        sentiment="neutral", salience=sal))
    g.add_link(Link(source="java", target="spring", link_type="deepening",
                    weight="strong", rationale="", created_turn=19, last_active_turn=20))
    g.links[-1].co_activation_count = 6            # over-familiar pair
    g.add_link(Link(source="java", target="servlet", link_type="deepening",
                    weight="medium", rationale="", created_turn=15, last_active_turn=15))
    g.add_link(Link(source="servlet", target="cors", link_type="deepening",
                    weight="tangential", rationale="", created_turn=5, last_active_turn=5))
    return g


class TestStimulusCandidates(unittest.TestCase):
    def test_balanced_ranking_recall_and_spark(self):
        c = {x["keyword"]: x for x in _g().stimulus_candidates(["java"])}
        # recall is served: the useful strong neighbour is there, tagged as such
        self.assertEqual(c["spring"]["reasons"], ["recall"])
        # ...but damped for over-familiarity (score < raw activation)
        self.assertLess(c["spring"]["score"], c["spring"]["act"])
        # the spark is served: 2-hop path, interpretable reasons, boosted score
        self.assertEqual(c["cors"]["path"], ["java", "servlet", "cors"])
        self.assertEqual(c["cors"]["hops"], 2)
        self.assertIn("tangential", c["cors"]["reasons"])
        self.assertIn("→frontend", c["cors"]["reasons"])
        self.assertTrue(any(r.startswith("dormant") for r in c["cors"]["reasons"]))
        self.assertGreater(c["cors"]["score"], c["cors"]["act"])   # bonus applied

    def test_seeds_excluded_and_empty_graph(self):
        g = _g()
        kws = [x["keyword"] for x in g.stimulus_candidates(["java"])]
        self.assertNotIn("java", kws)
        self.assertEqual(Graph().stimulus_candidates(["ghost"]), [])


class TestPiggybackAntiEcho(unittest.TestCase):
    def test_rotation_via_cooldown(self):
        from neuron import stimulus as st
        g = _g()

        class _FakeSrv:
            flash_enabled = True
            STIMULUS_MIN_ACTIVATION = 0.05
            STIMULUS_MAX_CHARS = 200
        orig = st._S
        st._S = lambda: _FakeSrv
        st._stim_recent.clear()
        try:
            first = st._stimulus_block(g, ["java"])
            g.turn_count += 1
            second = st._stimulus_block(g, ["java"])
            g.turn_count += 1
            third = st._stimulus_block(g, ["java"])
            self.assertIn("java ⇢ spring", first)     # 1-hop path shown too
            self.assertIn("java ⇢ servlet", second)   # spring in cooldown
            self.assertIn("java ⇢ servlet ⇢ cors", third)  # the spark surfaces
            self.assertIn("(", third)                 # reasons rendered
        finally:
            st._S = orig
            st._stim_recent.clear()

    def test_two_hop_spark_clears_score_floor(self):
        """Regression (field test 2026-07-13): with the floor on raw act, a
        2-hop spark (act ~0.12 < 0.15) could NEVER be emitted."""
        from neuron import stimulus as st
        g = _g()

        class _FakeSrv:
            flash_enabled = True
            STIMULUS_MIN_ACTIVATION = 0.15   # the real production floor
            STIMULUS_MAX_CHARS = 200
        orig = st._S
        st._S = lambda: _FakeSrv
        st._stim_recent.clear()
        try:
            st._stim_recent["spring"] = g.turn_count    # recall in cooldown
            st._stim_recent["servlet"] = g.turn_count   # bridge in cooldown
            out = st._stimulus_block(g, ["java"])
            self.assertIn("cors", out)                  # the spark gets through
        finally:
            st._S = orig
            st._stim_recent.clear()


if __name__ == "__main__":
    unittest.main(verbosity=2)
