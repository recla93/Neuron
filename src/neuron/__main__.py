"""Entry point for ``python -m neuron`` and the ``neuron`` console script.

Default (no subcommand) runs the MCP stdio server, so existing launchers that call
``python -m neuron`` (e.g. bridge.py) keep working unchanged. Subcommands:
  ``neuron init ...``        — client wiring (no heavy server import).
  ``neuron register ...``    — register the MCP server in AI clients (Piano 05 B1).
  ``neuron doctor ...``      — diagnose/repair client registrations (Piano 05 B6).
  ``neuron consolidate ...`` — merge near-duplicates + archive orphans (E1.4).
"""

import sys


def _consolidate_cli(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="neuron consolidate",
                                 description="Consolida lo store: merge near-duplicati + archivio orfani.")
    ap.add_argument("--context", help="solo questo contesto (default: tutti)")
    ap.add_argument("--no-merge", action="store_true", help="non fondere i near-duplicati")
    ap.add_argument("--no-drop-orphans", action="store_true", help="non archiviare gli orfani")
    ap.add_argument("--sim-threshold", type=float, default=0.85, help="soglia coseno per il merge")
    args = ap.parse_args(argv)

    from neuron.server import _g  # registry con l'embedder già registrato
    contexts = [args.context] if args.context else [c["context"] for c in _g.list_contexts()]
    if not contexts:
        print("Nessun contesto da consolidare.")
        return 0

    tot_m = tot_d = 0
    for ctx in contexts:
        g = _g.get(ctx)
        rep = g.consolidate(
            sim_threshold=(2.0 if args.no_merge else args.sim_threshold),
            drop_orphans=not args.no_drop_orphans,
        )
        _g.save(ctx)
        merged = sum(1 for r in rep if "kept" in r)
        dropped = sum(1 for r in rep if "dropped" in r)
        tot_m += merged; tot_d += dropped
        print(f"  {ctx}: merged={merged} dropped={dropped} nodes={len(g.nodes)} links={len(g.links)}")
    print(f"Totale: merged={tot_m} dropped={tot_d}")
    return 0


def cli() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from neuron.init import main as init_main
        raise SystemExit(init_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] in ("register", "doctor"):
        from neuron.clients import cli as clients_cli   # stdlib-only, no server import
        raise SystemExit(clients_cli(sys.argv[1:]))
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        from neuron.setup import main as setup_main     # T63: universal lifecycle CLI
        raise SystemExit(setup_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "manage":
        from neuron.manage import main as manage_main   # T63 fase 2: management CLI
        raise SystemExit(manage_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "consolidate":
        raise SystemExit(_consolidate_cli(sys.argv[2:]))
    import asyncio
    from neuron.server import main
    asyncio.run(main())


if __name__ == "__main__":
    cli()
