"""Entry point for ``python -m neuron`` and the ``neuron`` console script.

Default (no subcommand) runs the MCP stdio server, so existing launchers that call
``python -m neuron`` (e.g. bridge.py) keep working unchanged. Subcommands:
  ``neuron init ...``        — client wiring (no heavy server import).
  ``neuron register ...``    — register the MCP server in AI clients (Piano 05 B1).
  ``neuron doctor ...``      — diagnose/repair client registrations (Piano 05 B6).
  ``neuron consolidate ...`` — merge near-duplicates + archive orphans (E1.4).
  ``neuron setup / manage``  — lifecycle + day-to-day management (ADR-007).
  ``neuron bridge ...``      — expose the stdio server over HTTP (remote connectors).
  ``neuron connect ...``     — connect & test a Turso Cloud DB, then save to .env.
  ``neuron console ...``     — read-only graph diagnostics (--watch to follow).
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
    if len(sys.argv) > 1 and sys.argv[1] == "bridge":
        from neuron.bridge import main as bridge_main   # stdio→HTTP bridge for remote connectors
        raise SystemExit(bridge_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "connect":
        from neuron.connect import main as connect_main  # Turso Cloud onboarding (test then save)
        raise SystemExit(connect_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "console":
        from neuron.console import main as console_main  # read-only graph diagnostics
        raise SystemExit(console_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "tunnel":
        from neuron.tunnel import main as tunnel_main    # cloudflared public HTTPS tunnel
        raise SystemExit(tunnel_main(sys.argv[2:]))
    # T68: client-agnostic isolation flags. Some MCP hosts (OpenCode) don't
    # pass `env` to child processes at all, so a test/sandbox store couldn't be
    # isolated via NS_GRAPHS_DIR. Flags travel in the command array — which
    # EVERY client passes — and are applied BEFORE neuron.server is imported
    # (server reads NS_GRAPHS_DIR at import; db reads TURSO_* at its import).
    #   --graphs-dir PATH   store location (sets NS_GRAPHS_DIR)
    #   --local             force the local tier: drops TURSO_* creds
    #                       (wherever they came from, .env included)
    #   --slug NAME         identity override (sets NEURON_SLUG)
    import os
    args = sys.argv[1:]
    def _take(flag):
        if flag in args:
            i = args.index(flag)
            if i + 1 < len(args):
                v = args[i + 1]; del args[i:i + 2]; return v
            del args[i]
        return None
    _gd, _slug = _take("--graphs-dir"), _take("--slug")
    if "--local" in args:
        args.remove("--local")
        os.environ["NEURON_NO_DOTENV"] = "1"
        os.environ.pop("TURSO_DATABASE_URL", None)
        os.environ.pop("TURSO_AUTH_TOKEN", None)
    if _gd:
        os.environ["NS_GRAPHS_DIR"] = _gd
    if _slug:
        os.environ["NEURON_SLUG"] = _slug
    import asyncio
    from neuron.server import main
    asyncio.run(main())


if __name__ == "__main__":
    cli()
