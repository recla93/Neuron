"""DEPRECATED — replaced by scripts/import_vault.py.

The old seed_vault.py was hardcoded to one personal Obsidian vault
(C:\\Users\\recla\\... / D:\\Desktop\\...) and wrote to the legacy
knowledge/ path without generating embeddings.

Use the clean, path-agnostic replacement instead:

    set NEURON_VAULT=C:\\path\\to\\your\\vault      (Windows)
    export NEURON_VAULT=/path/to/your/vault        (Linux/macOS)
    python scripts/import_vault.py

    # or explicitly:
    python scripts/import_vault.py --vault <path> --out ./knowledge/base_knowledge.db

import_vault.py:
  - takes the vault root from NEURON_VAULT / --vault (no hardcoded paths),
  - writes to a configurable --out (default ./knowledge/base_knowledge.db),
  - generates 384-dim fastembed vectors inline when available.

The output DB is LOCAL. Copy it to src/neuron/data/base_knowledge.db only when
you deliberately want it shipped as the public seed.
"""

import sys


def main() -> None:
    sys.exit(
        "seed_vault.py is deprecated. Use scripts/import_vault.py instead "
        "(set NEURON_VAULT or pass --vault). See this file's docstring."
    )


if __name__ == "__main__":
    main()
