"""Propose where the inbox's facts belong. Proposes only: moving is yours.

A session of no project writes to the inbox (2026-11-27), so its facts are
never in the wrong graph; this reads them and, for each, names the graph whose
facts it resembles most, when that graph wins by a clear margin. Nothing is
written: it prints the `move_facts.py` commands to run once you agree.

    python scripts/triage_inbox.py                  # proposals
    python scripts/triage_inbox.py --min 0.55 --margin 0.05

Score of a graph for a fact: the mean cosine of its 3 closest active facts
(one lucky neighbour is not a home), mpnet or whatever model is active.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def propose(reg, inbox: str, skip: set, embed, min_score: float, margin: float) -> list:
    from neuron.search import _fact_text
    homes = {}
    for ctx in reg._contexts_on_disk():
        if ctx in skip or ctx == inbox:
            continue
        facts = [f for f in reg.get(ctx).facts.values() if f["status"] == "active"]
        if facts:
            homes[ctx] = [embed(_fact_text(f)) for f in facts]
    out = []
    for f in reg.get(inbox).facts.values():
        if f["status"] != "active":
            continue
        q = embed(_fact_text(f))
        scores = sorted(((sum(sorted((sum(a * b for a, b in zip(q, v)) for v in vs), reverse=True)[:3])
                          / min(3, len(vs)), ctx) for ctx, vs in homes.items()), reverse=True)
        best = scores[0] if scores else (0.0, "")
        second = scores[1][0] if len(scores) > 1 else 0.0
        to = best[1] if best[0] >= min_score and best[0] - second >= margin else ""
        out.append({"id": f["id"], "text": f["text"], "to": to,
                    "best": [(c, round(s, 3)) for s, c in scores[:2]]})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--inbox", default=os.environ.get("NEURON_INBOX_CONTEXT") or "inbox")
    ap.add_argument("--min", type=float, default=0.50, help="lowest score to propose a home")
    ap.add_argument("--margin", type=float, default=0.05, help="lead over the second graph")
    a = ap.parse_args(argv)
    from neuron import config
    from neuron.registry import GraphRegistry
    from neuron.search import _get_embedding
    reg = GraphRegistry(graphs_dir=config.graphs_dir())
    skip = {os.environ.get("NEURON_USER_CONTEXT") or "user"}
    rows = propose(reg, a.inbox.lower(), skip, _get_embedding, a.min, a.margin)
    by = {}
    for r in rows:
        print(f"#{r['id']} -> {r['to'] or 'stays (no clear home)'}  {r['best']}  {r['text'][:80]}")
        if r["to"]:
            by.setdefault(r["to"], []).append(r["id"])
    for to, ids in sorted(by.items()):
        print(f"python scripts/move_facts.py --from {a.inbox} --to {to} --ids {','.join(ids)} --apply")
    print(f"{len(rows)} facts in '{a.inbox}', {sum(len(v) for v in by.values())} with a proposed home; nothing moved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
