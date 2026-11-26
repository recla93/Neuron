"""Move facts from one context's graph to another, with their concepts and links.

Facts are append-only inside a graph; this is the one way to give a fact a new
home (2026-11-26: the user's habits move to the shared `user` graph; a project's
facts strayed into other contexts). Run it with the daemon STOPPED: a running
Neuron holds the graphs in memory and would write the moved rows back.

    python scripts/move_facts.py --kinds user,habit --to user            # dry run
    python scripts/move_facts.py --kinds user,habit --to user --apply
    python scripts/move_facts.py --ids 1a2b3c4d,5e6f7a8b --from llm --to backend --apply
    python scripts/move_facts.py --nodes --from graymatter,llm --to mocoder --retire --apply

The source rows are deleted only after the target graph is saved. A backup of
every touched graph file is taken first (db.snapshot, tag `pre-move`).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def purge_episodes(conn, ctx: str, texts: set) -> int:
    """Delete the legacy episode rows of `ctx` whose text is in `texts`."""
    texts = sorted(t for t in texts if t)
    if not texts:
        return 0
    try:
        q = ",".join("?" * len(texts))
        return conn.execute(f"DELETE FROM episodes WHERE context=? AND text IN ({q})", [ctx, *texts]).rowcount
    except Exception:  # noqa: BLE001 — a store without the legacy table has nothing to purge
        return 0


def pick(graph, kinds: set, ids: set) -> list:
    return [f for f in graph.facts.values()
            if (kinds and f["kind"] in kinds) or (ids and f["id"] in ids)]


def move(reg, src_ctx: str, dst_ctx: str, kinds: set, ids: set, apply: bool) -> dict:
    """Copy the picked facts of `src_ctx` into `dst_ctx`, save it, then delete them
    from `src_ctx`'s file. Returns a report; writes nothing unless `apply`."""
    from neuron import db as _db
    src, dst = reg.get(src_ctx), reg.get(dst_ctx)
    moved = pick(src, kinds, ids)
    mids = {f["id"] for f in moved}
    clash = sorted(mids & set(dst.facts))
    links = {lk for lk in src.fact_links if lk[0] in mids or lk[1] in mids}
    dangling = sorted({d if s in mids else s for s, d, _k in links} - mids)
    report = {"from": src_ctx, "to": dst_ctx, "facts": len(moved),
              "active": sum(f["status"] == "active" for f in moved),
              "links": len(links), "links_to_facts_left_behind": dangling, "id_clash": clash}
    if not apply or not moved or clash:
        return report
    for path in {reg._db_path(src_ctx), reg._db_path(dst_ctx)}:
        if os.path.isfile(path):
            _db.snapshot(path, tag="pre-move")
    for f in moved:
        f2 = {**f, "concepts": list(f["concepts"])}
        dst.facts[f2["id"]] = f2
        dst._new_facts.add(f2["id"])
        dst._new_concepts.update((f2["id"], kw) for kw in f2["concepts"])
        if f2["status"] == "active":
            for kw in f2["concepts"]:
                dst._index_one(kw, f2)
    dst.fact_links |= links
    dst._new_fact_links |= links
    dst._dirty = True
    reg.save(dst_ctx)
    conn = _db.connect(reg._db_path(src_ctx))
    q = ",".join("?" * len(mids))
    args = [src_ctx, *sorted(mids)]
    conn.execute(f"DELETE FROM fact_concepts WHERE context=? AND fact_id IN ({q})", args)
    conn.execute(f"DELETE FROM fact_links WHERE context=? AND (src IN ({q}) OR dst IN ({q}))",
                 [src_ctx, *sorted(mids), *sorted(mids)])
    conn.execute(f"DELETE FROM facts WHERE context=? AND id IN ({q})", args)
    # The legacy `episodes` rows of a moved fact go too: a load turns every
    # episode text that is not a fact back into a fact (Graph.import_episodes),
    # so the moved fact came back in the source with a new id (2026-11-26).
    purge_episodes(conn, src_ctx, {f["text"] for f in moved})
    conn.commit()
    conn.close()
    report["applied"] = True
    return report


def copy_nodes(reg, src_ctx: str, dst_ctx: str, retire: bool, apply: bool) -> dict:
    """Give `dst_ctx` the concept nodes of its facts that live in `src_ctx`.

    A node is SHARED: the same concept indexes facts that moved and facts that
    stayed, so it is copied, never moved, with its own salience from then on.
    The links between copied nodes come along. With `retire`, a copied node no
    active fact of `src_ctx` uses any more goes to the source's graveyard: left
    there it only pulls another project's concept into this one's recall."""
    import dataclasses
    src, dst = reg.get(src_ctx), reg.get(dst_ctx)
    wanted = {src._norm(c) for f in dst.facts.values() for c in f["concepts"]}
    have = {n.keyword for n in dst.nodes}
    copy = [n for n in src.nodes if n.keyword in wanted and n.keyword not in have]
    kws = {n.keyword for n in copy} | (have & {n.keyword for n in src.nodes})
    links = [lk for lk in src.links if lk.source in kws and lk.target in kws
             and (lk.source not in have or lk.target not in have)]
    used = {src._norm(c) for f in src.facts.values() if f["status"] == "active" for c in f["concepts"]}
    # Every node of dst's concepts, copied now or on an earlier pass: copies come
    # first for all targets, then the retire pass (a node two targets need must
    # not leave the source before the second one copied it).
    gone = sorted(n.keyword for n in src.nodes if n.keyword in wanted and n.keyword not in used) if retire else []
    report = {"from": src_ctx, "to": dst_ctx, "nodes": len(copy), "links": len(links), "retired": len(gone)}
    if not apply or not (copy or links or gone):
        return report
    for path in {reg._db_path(src_ctx), reg._db_path(dst_ctx)}:
        if os.path.isfile(path):
            from neuron import db as _db
            _db.snapshot(path, tag="pre-nodes")
    if gone and not all(dst.get_node(kw) or kw in {n.keyword for n in copy} for kw in gone):
        raise RuntimeError("a node to retire is not in the target: copy before retiring")
    for n in copy:
        dst.add_node(dataclasses.replace(n, entities=list(n.entities or []), tags=list(n.tags or []),
                                         references=[dict(r) for r in (n.references or [])],
                                         vector=list(n.vector) if n.vector else None))
    for lk in links:
        dst.add_link(dataclasses.replace(lk))
    if copy or links:
        reg.save(dst_ctx)
    if gone:
        drop = set(gone)
        for kw in gone:
            nd = src._node_map.get(kw)
            src._graveyard.append({"keyword": kw, "salience": nd.salience if nd else 0,
                                   "domain": nd.domain if nd else "",
                                   "reason": f"moved with its facts to {dst_ctx}", "turn": src.turn_count})
            src._record_removed_node(kw)
        for lk in [lk for lk in src.links if lk.source in drop or lk.target in drop]:
            src._record_removed_link(lk)
        src.links = [lk for lk in src.links if lk.source not in drop and lk.target not in drop]
        src.nodes = [n for n in src.nodes if n.keyword not in drop]
        src._rebuild_node_map()
        src._dirty = True
        reg.save(src_ctx)
    report["applied"] = True
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--to", required=True, help="target context")
    ap.add_argument("--from", dest="src", default="", help="source contexts, comma-separated (default: all others)")
    ap.add_argument("--kinds", default="", help="fact kinds to move, comma-separated")
    ap.add_argument("--ids", default="", help="fact ids to move, comma-separated")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--nodes", action="store_true",
                    help="copy the concept nodes of --to's facts from the sources (no facts move)")
    ap.add_argument("--retire", action="store_true",
                    help="with --nodes: graveyard the copied nodes no source fact uses any more")
    a = ap.parse_args(argv)
    if a.nodes:
        from neuron import config
        from neuron.registry import GraphRegistry
        reg = GraphRegistry(graphs_dir=config.graphs_dir())
        dst = a.to.lower().strip("/")
        for s in [x.strip().lower().strip("/") for x in a.src.split(",") if x.strip()]:
            print(copy_nodes(reg, s, dst, a.retire, a.apply))
        if not a.apply:
            print("dry run: nothing written (add --apply)")
        return 0
    kinds = {k.strip() for k in a.kinds.split(",") if k.strip()}
    ids = {i.strip().lstrip("#") for i in a.ids.split(",") if i.strip()}
    if not (kinds or ids):
        ap.error("give --kinds or --ids")
    from neuron import config
    from neuron.registry import GraphRegistry
    reg = GraphRegistry(graphs_dir=config.graphs_dir())
    dst = a.to.lower().strip("/")
    sources = ([s.strip().lower().strip("/") for s in a.src.split(",") if s.strip()]
               or [c for c in reg._contexts_on_disk() if c != dst])
    for s in sources:
        r = move(reg, s, dst, kinds, ids, a.apply)
        if r["facts"]:
            print(r)
    if not a.apply:
        print("dry run: nothing written (add --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
