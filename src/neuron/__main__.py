"""Entry point for ``python -m neuron`` and the ``neuron`` console script.

Default (no subcommand) runs the MCP stdio server, so existing launchers that call
``python -m neuron`` (e.g. bridge.py) keep working unchanged. ``neuron init ...``
dispatches to the client-wiring command without importing the heavy server stack.
"""

import sys


def cli() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from neuron.init import main as init_main
        raise SystemExit(init_main(sys.argv[2:]))
    import asyncio
    from neuron.server import main
    asyncio.run(main())


if __name__ == "__main__":
    cli()
