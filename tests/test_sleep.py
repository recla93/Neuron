"""E3.3 — sleep-mode trigger (Graph.sleep_maybe). The pre-staged stimulus was parked on 2026-11-14.

Pure (neuron.models), runs anywhere.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import neuron.models as m
from neuron.models import Graph, Node, Link


def _g():
    g = Graph()
    g.add_node(Node(keyword="python", turn=1, topic="t", domain="backend",
                    sentiment="neutral", salience=3))
    g.add_node(Node(keyword="django", turn=1, topic="t", domain="backend",
                    sentiment="neutral", salience=1))
    g.add_link(Link(source="python", target="django", link_type="deepening", weight="medium",
                    rationale="", created_turn=1, last_active_turn=1))
    return g


def test_sleep_skipped_when_recent():
    g = _g(); g._loaded_ts = time.time()          # just active
    assert g.sleep_maybe() is None


def test_sleep_skipped_without_timestamp():
    g = _g(); g._loaded_ts = None                 # fresh/legacy store
    assert g.sleep_maybe() is None


def test_sleep_consolidate_flag_runs_without_error():
    g = _g(); g._loaded_ts = time.time() - (m.SLEEP_IDLE_SECONDS + 10)
    rep = g.sleep_maybe(do_consolidate=True)
    assert rep["consolidated"] is True            # ran the consolidate path


