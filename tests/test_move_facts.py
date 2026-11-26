"""scripts/move_facts.py: a fact changes graph with its concepts and links, and
leaves the source; a dry run writes nothing."""
import importlib.util
import pathlib

from neuron.registry import GraphRegistry

_SPEC = importlib.util.spec_from_file_location(
    "move_facts", pathlib.Path(__file__).parents[1] / "scripts" / "move_facts.py")
M = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(M)


def _seed(tmp_path):
    reg = GraphRegistry(str(tmp_path))
    g = reg.get("backend")
    h = g.add_fact("habit", "Ask before a real usage test.", ["usage test"], 5)["id"]
    d = g.add_fact("decision", "Contexts come from the git remote.", ["context"], 6)["id"]
    assert g.add_fact_link(d, h, "disputes") == ""
    reg.save("backend")
    return h, d


def test_dry_run_writes_nothing(tmp_path):
    h, _d = _seed(tmp_path)
    r = M.move(GraphRegistry(str(tmp_path)), "backend", "user", {"habit"}, set(), apply=False)
    assert r["facts"] == 1 and "applied" not in r
    assert h in GraphRegistry(str(tmp_path)).get("backend").facts


def test_apply_moves_fact_concepts_and_links(tmp_path):
    h, d = _seed(tmp_path)
    r = M.move(GraphRegistry(str(tmp_path)), "backend", "user", {"habit"}, set(), apply=True)
    assert r["applied"] and r["links_to_facts_left_behind"] == [d]
    fresh = GraphRegistry(str(tmp_path))
    user, back = fresh.get("user"), fresh.get("backend")
    assert set(user.facts) == {h} and user.facts[h]["concepts"] == ["usage test"]
    assert h not in back.facts and d in back.facts
    assert any(e["id"] == h for e in user.episodes.get("usage test", []))


def test_rename_context_moves_rows_file_and_pointer(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "rename_context", pathlib.Path(__file__).parents[1] / "scripts" / "rename_context.py")
    R = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(R)
    h, d = _seed(tmp_path)
    reg = GraphRegistry(str(tmp_path))
    reg.switch("backend")
    reg.get("backend/python")                       # a child context: not renamed
    reg.get("backend/python").add_fact("fact", "child fact", ["python"], 1)
    reg.save("backend/python")
    dry = R.rename(str(tmp_path), "backend", "graymatter", apply=False)
    assert dry["rows"]["facts"] == 2 and dry["pointer"] and "applied" not in dry
    assert R.rename(str(tmp_path), "backend", "graymatter", apply=True)["applied"]
    fresh = GraphRegistry(str(tmp_path))
    assert fresh.active == "graymatter"
    assert {h, d} <= set(fresh.get("graymatter").facts)
    assert not (tmp_path / "graph_backend.db").exists()
    assert len(fresh.get("backend/python").facts) == 1
    assert "error" in R.rename(str(tmp_path), "backend", "graymatter", apply=True)


def test_a_moved_fact_does_not_come_back_from_legacy_episodes(tmp_path):
    from neuron import db as _db
    h, _d = _seed(tmp_path)
    conn = _db.connect(str(tmp_path / "graph_backend.db"))
    conn.execute("INSERT INTO episodes (context, keyword, turn, text) VALUES (?,?,?,?)",
                 ("backend", "usage test", 5, "Ask before a real usage test."))
    conn.commit()
    conn.close()
    M.move(GraphRegistry(str(tmp_path)), "backend", "user", {"habit"}, set(), apply=True)
    back = GraphRegistry(str(tmp_path)).get("backend")
    assert not [f for f in back.facts.values() if f["text"] == "Ask before a real usage test."]


def test_copy_nodes_shares_by_copy_and_retires_only_unused(tmp_path):
    from neuron.models import Node, Link, VECTOR_DIM
    reg = GraphRegistry(str(tmp_path))
    src, dst = reg.get("graymatter"), reg.get("mocoder")
    for kw, sal in (("release", 3), ("vsix", 2)):
        src.add_node(Node(kw, 1, kw, "general", "neutral", salience=sal, vector=[0.1] * VECTOR_DIM))
    src.add_link(Link("release", "vsix", "deepening", "medium", "", 1, 1))
    src.add_fact("decision", "GM release order: gate first.", ["release"], 2)        # stays
    reg.save("graymatter")
    dst.add_fact("progress", "mocoder vsix built for 1.0.", ["release", "vsix"], 1)  # moved earlier
    reg.save("mocoder")
    dry = M.copy_nodes(GraphRegistry(str(tmp_path)), "graymatter", "mocoder", retire=True, apply=False)
    assert dry == {"from": "graymatter", "to": "mocoder", "nodes": 2, "links": 1, "retired": 1}
    assert M.copy_nodes(GraphRegistry(str(tmp_path)), "graymatter", "mocoder", retire=True, apply=True)["applied"]
    fresh = GraphRegistry(str(tmp_path))
    m, g = fresh.get("mocoder"), fresh.get("graymatter")
    assert {n.keyword for n in m.nodes} == {"release", "vsix"} and len(m.links) == 1
    assert m.get_node("release").salience == 3 and m.get_node("release").vector
    assert {n.keyword for n in g.nodes} == {"release"} and not g.links   # shared stays, unused goes
