"""Episodic payload tests (T56) — sqlite stdlib tier."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neuron.models import (  # noqa: E402
    EPISODE_MAX_CHARS, EPISODES_PER_NODE, Graph, Node,
)


def _node(kw, turn=1):
    return Node(keyword=kw, turn=turn, topic="t", domain="general",
                sentiment="neutral")


class TestEpisodes(unittest.TestCase):
    def test_add_and_recent(self):
        g = Graph()
        g.add_node(_node("retry backoff"))
        self.assertTrue(g.add_episode("retry backoff", "chose https over wss", turn=3))
        self.assertTrue(g.add_episode("retry backoff", "backoff base 0.5s", turn=5))
        self.assertEqual(g.recent_episodes("retry backoff", 2),
                         ["backoff base 0.5s", "chose https over wss"])

    def test_requires_existing_node_and_text(self):
        g = Graph()
        self.assertFalse(g.add_episode("ghost", "fact", turn=1))
        g.add_node(_node("real"))
        self.assertFalse(g.add_episode("real", "   ", turn=1))

    def test_same_turn_overwrites(self):
        g = Graph()
        g.add_node(_node("a"))
        g.add_episode("a", "v1", turn=2)
        g.add_episode("a", "v2", turn=2)
        self.assertEqual(g.recent_episodes("a"), ["v2"])

    def test_write_reports_what_it_lost(self):
        """Regola 1: mai troncare o scartare in silenzio. Il report della
        scrittura dice di quanto si è oltre e cosa è stato sfrattato."""
        g = Graph()
        g.add_node(_node("a"))
        # sotto i limiti: nessuna perdita da dichiarare
        clean = g.add_episode("a", "corto", turn=1)
        self.assertEqual(clean, {"stored": True})
        # oltre il cap annunciato ma dentro la grazia (1.5x): intero, e il
        # report non lo dice — un limite morbido dichiarato e' il nuovo limite
        grace = g.add_episode("a", "x" * (EPISODE_MAX_CHARS + 17), turn=2)
        self.assertEqual(grace, {"stored": True})
        self.assertEqual(len(g.recent_episodes("a")[0]), EPISODE_MAX_CHARS + 17)
        # oltre la grazia: taglio a confine di parola, e il report conta i
        # caratteri oltre il cap ANNUNCIATO, non oltre quello nascosto
        words = ("parola " * EPISODE_MAX_CHARS).strip()
        long = g.add_episode("a", words, turn=2)
        self.assertEqual(long["truncated"], len(words) - EPISODE_MAX_CHARS)
        stored = g.recent_episodes("a")[0]
        self.assertLessEqual(len(stored), EPISODE_MAX_CHARS * 3 // 2)
        self.assertTrue(stored.endswith("parola"), stored[-20:])
        # sfratto: il report nomina i turni buttati dal cap
        for t in range(3, EPISODES_PER_NODE + 4):
            rep = g.add_episode("a", f"fact {t}", turn=t)
        self.assertIn("dropped_turns", rep)
        self.assertEqual(len(g.episodes["a"]), EPISODES_PER_NODE)
        # nodo inesistente: falsy, come prima
        self.assertFalse(g.add_episode("ghost", "fact", turn=1))

    def test_cap_drops_oldest_and_tracks_removal(self):
        g = Graph()
        g.add_node(_node("a"))
        for t in range(1, EPISODES_PER_NODE + 3):
            g.add_episode("a", f"fact {t}", turn=t)
        eps = g.episodes["a"]
        self.assertEqual(len(eps), EPISODES_PER_NODE)
        self.assertEqual(eps[0]["turn"], 3)          # 1 and 2 dropped
        self.assertIn(("a", 1), g._removed_episodes)
        self.assertIn(("a", 2), g._removed_episodes)

    def test_roundtrip_sqlite(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "g.db")
            g = Graph()
            g.add_node(_node("retry backoff"))
            g.add_node(_node("salience"))
            g.turn_count = 4
            g.add_episode("retry backoff", "chose https over wss", turn=4)
            g.save_sqlite(path)
            # incremental save of a later episode
            g.turn_count = 5
            g.add_episode("retry backoff", "backoff base 0.5s", turn=5)
            g.save_sqlite(path)

            g2 = Graph()
            g2.load_sqlite(path)
            self.assertEqual(g2.recent_episodes("retry backoff", 2),
                             ["backoff base 0.5s", "chose https over wss"])
            self.assertEqual(g2.recent_episodes("salience"), [])

    def test_removed_node_deletes_episodes_rows(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "g.db")
            g = Graph()
            g.add_node(_node("a"))
            g.add_node(_node("b"))
            g.add_episode("a", "fact a", turn=1)
            g.save_sqlite(path)
            # simulate structural removal
            g.nodes = [nd for nd in g.nodes if nd.keyword != "a"]
            g._rebuild_node_map()
            g.episodes.pop("a", None)
            g._removed_nodes.add("a")
            g._dirty = True   # manual structural removal (mutation helpers set this)
            g.save_sqlite(path)
            g2 = Graph()
            g2.load_sqlite(path)
            self.assertEqual(g2.recent_episodes("a"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
