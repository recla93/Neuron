"""Tests for `neuron manage` (P1 #6 — was zero coverage).

Covers the path helper, context listing, and the overview/export/dispatch paths
against a temporary graph store (no real store touched).
"""
from __future__ import annotations

import json
import os
import sys

sys.modules.setdefault("turso", None)  # force sqlite3 fallback in neuron.db

from neuron import manage as M


def test_graphs_dir_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("NS_GRAPHS_DIR", str(tmp_path))
    assert M._graphs_dir() == os.path.normpath(str(tmp_path))


def test_contexts_empty_returns_default(monkeypatch, tmp_path):
    monkeypatch.setenv("NS_GRAPHS_DIR", str(tmp_path))
    assert M._contexts() == ["default"]


def test_overview_on_empty_store_returns_0(monkeypatch, tmp_path):
    monkeypatch.setenv("NS_GRAPHS_DIR", str(tmp_path))
    assert M.do_overview() == 0


def test_export_writes_json(monkeypatch, tmp_path):
    monkeypatch.setenv("NS_GRAPHS_DIR", str(tmp_path))
    # A missing context loads as an empty graph; export must still emit valid JSON.
    out = tmp_path / "dump.json"
    rc = M.do_export(str(out), "default")
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def test_main_overview_dispatch(monkeypatch, tmp_path):
    monkeypatch.setenv("NS_GRAPHS_DIR", str(tmp_path))
    assert M.main(["--overview"]) == 0
