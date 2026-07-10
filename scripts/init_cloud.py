#!/usr/bin/env python3
"""One-shot initializer for a SHARED Turso Cloud store (concurrency P4).

Run this ONCE, by one person, BEFORE colleagues connect, so the schema
(tables + indexes) is migrated a single time instead of racing across clients
on their first write to a fresh shared database. Idempotent — safe to re-run.

Usage:
    python scripts/init_cloud.py

Requires TURSO_DATABASE_URL / TURSO_AUTH_TOKEN in the environment or in a local
.env (loaded automatically). On a local (non-cloud) setup the schema is created
lazily on the first save, so this script only applies to the cloud tier.
"""
from __future__ import annotations

import sys


def main() -> int:
    # Load .env first so the Turso credentials are visible before neuron.db is
    # imported (db reads them at import time).
    from neuron import _env
    _env.load_dotenv_once()

    from neuron import db as _db
    from neuron.models import Graph

    if not _db.REMOTE_TURSO:
        print(
            "init_cloud: nessuna credenziale Turso cloud rilevata "
            "(TURSO_DATABASE_URL / TURSO_AUTH_TOKEN). Questo script inizializza lo "
            "store CLOUD condiviso; in locale lo schema si crea da solo al primo "
            "salvataggio.",
            file=sys.stderr,
        )
        return 1

    try:
        # path is ignored on the remote tier — the store IS the cloud DB.
        Graph().ensure_schema("", context="default")
    except Exception as e:  # noqa: BLE001 — surface any failure to the operator
        print(f"init_cloud: inizializzazione dello schema fallita: {e}", file=sys.stderr)
        return 2

    print(f"init_cloud: schema dello store cloud pronto ({_db.ENGINE_NAME}). "
          "I colleghi possono collegarsi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
