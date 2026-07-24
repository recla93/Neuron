"""Tests for `neuron setup` (P1 #6 — was zero coverage).

Covers the pure path helper and the doctor-backed status/repair/dispatch paths,
mocking neuron.clients so no real client configs are touched.
"""
from __future__ import annotations

import os
import sys

sys.modules.setdefault("turso", None)  # force sqlite3 fallback in neuron.db

from neuron import setup as S
from neuron import clients as C


def test_graphs_dir_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("NS_GRAPHS_DIR", str(tmp_path))
    assert S._graphs_dir() == os.path.normpath(str(tmp_path))


def test_graphs_dir_uses_slug_default(monkeypatch):
    monkeypatch.delenv("NS_GRAPHS_DIR", raising=False)
    monkeypatch.setenv("NEURON_SLUG", "neuronX")
    assert S._graphs_dir().endswith(os.path.join("neuronX", "graphs"))


def test_status_zero_problems_returns_0(monkeypatch):
    monkeypatch.setattr(C, "doctor", lambda *a, **k: (["all good"], 0))
    assert S.do_status("neuron", "python") == 0


def test_status_with_problems_returns_1(monkeypatch):
    monkeypatch.setattr(C, "doctor", lambda *a, **k: (["broken"], 3))
    assert S.do_status("neuron", "python") == 1


def test_repair_invokes_doctor_with_fix(monkeypatch):
    seen = {}

    def fake_doctor(slug, python_exe, fix=False):
        seen["fix"] = fix
        return (["repaired"], 0)

    monkeypatch.setattr(C, "doctor", fake_doctor)
    assert S.do_repair("neuron", "python") == 0
    assert seen["fix"] is True


def test_install_reports_doctor_problems(monkeypatch):
    monkeypatch.setattr(C, "register_all", lambda slug, py: [])
    monkeypatch.setattr(C, "doctor", lambda *a, **k: (["x"], 1))
    # non-interactive (yes=True) so no prompts; 1 problem -> exit 1
    assert S.do_install("neuron", "python", yes=True) == 1


def test_main_status_dispatch(monkeypatch):
    monkeypatch.setattr(C, "doctor", lambda *a, **k: ([], 0))
    monkeypatch.setattr(C, "default_server_python", lambda slug: "python")
    assert S.main(["--status"]) == 0
