"""E3.3/E3.4 — sleep-mode trigger + pre-staging (Graph.sleep_maybe / take_staged_stimulus).

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


def test_sleep_prestages_top_stimulus_when_idle():
    g = _g(); g._loaded_ts = time.time() - (m.SLEEP_IDLE_SECONDS + 10)
    rep = g.sleep_maybe()
    assert rep is not None and rep["consolidated"] is False
    assert g.staged_stimulus and "django" in g.staged_stimulus


def test_sleep_consolidate_flag_runs_without_error():
    g = _g(); g._loaded_ts = time.time() - (m.SLEEP_IDLE_SECONDS + 10)
    rep = g.sleep_maybe(do_consolidate=True)
    assert rep["consolidated"] is True            # ran the consolidate path


def test_take_staged_is_one_shot():
    g = _g(); g.staged_stimulus = "django (act=0.50)"; g._staged_ts = time.time()
    assert g.take_staged_stimulus() == "django (act=0.50)"
    assert g.take_staged_stimulus() is None        # cleared after serving


def test_take_staged_drops_stale():
    g = _g(); g.staged_stimulus = "x"; g._staged_ts = time.time() - (m.STAGE_FRESH_SECONDS + 10)
    assert g.take_staged_stimulus() is None
    assert g.staged_stimulus is None               # dropped, not kept


def test_staged_state_survives_roundtrip():
    g = _g(); g.staged_stimulus = "django (act=0.50)"; g._staged_ts = 1000.0
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd); os.unlink(path)
    try:
        g.save_sqlite(path, context="default")
        g2 = Graph()
        g2.load_sqlite(path, context="default")
        assert g2.staged_stimulus == "django (act=0.50)"
        assert g2._staged_ts == 1000.0
        assert g2._loaded_ts is not None           # last_active_timestamp was written
    finally:
        os.path.exists(path) and os.unlink(path)
