"""End-to-end smoke test against a real Turso Cloud DB (T11 Fase 3 helper).

Run AFTER `scripts/connect_turso.py` has saved working credentials to .env:

    python scripts/smoke_cloud.py

It loads .env itself (see docs/DEVELOPER.md), confirms the
resolved engine is the cloud, then exercises the real remote path: writes two
different contexts to the shared tables, reloads each, and checks they stay
isolated (no cross-context bleed, no wipe). It cleans up the smoke contexts it
creates. Exit 0 = all good, 1 = a problem (details printed).

This validates on the real network what tests/test_core.py proves against
sqlite: incremental upsert + the context column, over the Turso HTTP transport.
"""
from __future__ import annotations

import os
import sys


def _load_env(path: str = ".env") -> None:
    """Minimal, dependency-free .env loader. Real environment wins over the file
    (setdefault), matching standard dotenv precedence."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())


def main() -> int:
    _load_env()

    # Import only AFTER env is loaded — db.py reads the credentials at import time.
    from neuron import db
    print(f"Resolved DB engine: {db.ENGINE_NAME}")
    if not db.REMOTE_TURSO:
        print("❌ Cloud is NOT active (TURSO_DATABASE_URL / TURSO_AUTH_TOKEN not both set).")
        print("   Run scripts/connect_turso.py first, or export the two vars, then retry.")
        return 1
    if db.libsql_client is None:
        print("❌ Cloud requested but 'libsql-client' is not installed:  pip install -e .[cloud]")
        return 1

    from neuron.models import Graph, Node, Link

    CTX_A, CTX_B = "smoke/alpha", "smoke/beta"
    ok = True

    def fail(msg: str) -> None:
        nonlocal ok
        ok = False
        print(f"  ❌ {msg}")

    try:
        # Write two contexts into the shared cloud tables.
        ga = Graph(); ga.turn_count = 1
        ga.add_node(Node("shared", 1, "t", "d", "neutral", salience=1))
        ga.add_node(Node("alpha_only", 1, "t", "d", "neutral", salience=2))
        ga.add_link(Link("shared", "alpha_only", "deepening", "strong", "", 1, 1))
        ga.save_sqlite("cloud", context=CTX_A)

        gb = Graph(); gb.turn_count = 1
        gb.add_node(Node("shared", 1, "t", "d", "neutral", salience=99))
        gb.add_node(Node("beta_only", 1, "t", "d", "neutral", salience=3))
        gb.save_sqlite("cloud", context=CTX_B)

        # Reload each and check isolation.
        ra = Graph(); ra.load_sqlite("cloud", context=CTX_A)
        rb = Graph(); rb.load_sqlite("cloud", context=CTX_B)
        if {n.keyword for n in ra.nodes} != {"shared", "alpha_only"}:
            fail(f"context A loaded wrong nodes: {[n.keyword for n in ra.nodes]}")
        if {n.keyword for n in rb.nodes} != {"shared", "beta_only"}:
            fail(f"context B loaded wrong nodes: {[n.keyword for n in rb.nodes]}")
        if ra.get_node("shared") and ra.get_node("shared").salience != 1:
            fail("context A's 'shared' salience bled from B")
        if rb.get_node("shared") and rb.get_node("shared").salience != 99:
            fail("context B's 'shared' salience bled from A")
        if len(ra.links) != 1:
            fail(f"context A link lost/duplicated: {len(ra.links)}")

        # Incremental save on B must not disturb A.
        rb.get_node("beta_only").salience += 5
        rb.mark_node_dirty("beta_only")
        rb.save_sqlite("cloud", context=CTX_B)
        ra2 = Graph(); ra2.load_sqlite("cloud", context=CTX_A)
        if {n.keyword for n in ra2.nodes} != {"shared", "alpha_only"}:
            fail("context A changed after an incremental save to context B")

        if ok:
            print("  ✅ two contexts coexist, stay isolated, and survive each other's saves")

        # --- Fase 2b: two concurrent writers on the SAME node both count ---
        base = Graph(); base.turn_count = 1
        base.add_node(Node("race", 1, "t", "d", "neutral", salience=5))
        base.save_sqlite("cloud", context=CTX_A)
        w1 = Graph(); w1.load_sqlite("cloud", context=CTX_A)   # baseline 5
        w2 = Graph(); w2.load_sqlite("cloud", context=CTX_A)   # baseline 5
        w1.get_node("race").salience += 2; w1.mark_node_dirty("race")
        w1.save_sqlite("cloud", context=CTX_A)
        w2.get_node("race").salience += 3; w2.mark_node_dirty("race")
        w2.save_sqlite("cloud", context=CTX_A)
        final = Graph(); final.load_sqlite("cloud", context=CTX_A)
        race_val = final.get_node("race").salience if final.get_node("race") else None
        if race_val != 10:
            fail(f"concurrent salience lost update: got {race_val}, expected 5+2+3=10")
        else:
            print("  ✅ concurrent increments on the same node both counted (atomic delta)")
    finally:
        # Clean up: empty both smoke contexts (scoped diff-delete, touches nothing else).
        for ctx in (CTX_A, CTX_B):
            try:
                g = Graph(); g.load_sqlite("cloud", context=ctx)
                g.nodes = []; g.links = []; g._rebuild_node_map()
                g.mark_full_rewrite(); g.save_sqlite("cloud", context=ctx)
            except Exception as exc:  # noqa: BLE001
                print(f"  ⚠️  cleanup of {ctx} failed: {type(exc).__name__}: {exc}")

    print("\nRESULT:", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
