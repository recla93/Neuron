"""The recall set (neuron/bench/recall.json) stays measurable as it is edited.

A broken reference or two facts sharing a signature would not fail the bench:
it would quietly count the wrong thing.
"""
import json
import pathlib

SET = pathlib.Path(__file__).resolve().parents[1] / "bench" / "recall.json"
KINDS = {"progress", "decision", "fix", "fact", "idea", "user", "habit"}   # ADR-015 D3
SIG = 40   # bench/run.py SIG


def test_the_recall_set_is_consistent():
    data = json.loads(SET.read_text(encoding="utf-8"))
    facts = {f["id"]: f for f in data["facts"]}
    assert len(facts) == len(data["facts"]), "duplicate fact id"
    assert {f["kind"] for f in facts.values()} <= KINDS
    sigs = [f["text"][:SIG] for f in facts.values()]
    assert len(set(sigs)) == len(sigs), "two facts share a signature"
    for f in facts.values():
        assert 1 <= len(f["concepts"]) <= 5, f["id"]
        assert len(f["text"]) <= 400, f["id"]
    ids = [m["id"] for m in data["messages"] + data["off_topic"]]
    assert len(set(ids)) == len(ids), "duplicate message id"
    for m in data["messages"]:
        assert m["expect"] and set(m["expect"]) <= facts.keys(), m["id"]
