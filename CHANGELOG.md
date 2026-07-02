# Changelog

All notable changes to Neuron are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and Neuron
follows [Semantic Versioning](https://semver.org/):

- **MAJOR** — breaking or behavior-changing releases (e.g. default data locations move).
- **MINOR** — backward-compatible features.
- **PATCH** — backward-compatible bug fixes.

The single source of truth for the version is `src/neuron/__init__.py`
(`__version__`); `pyproject.toml` reads it dynamically and the MCP server reports
it. Bump it in the same change that introduces the work. Tagging `vX.Y.Z` triggers
`release.yml`, which builds the prebuilt PyTurso wheels and publishes a GitHub
Release.

## [Unreleased] — 4.1.0 (in progress)

### Added
- **`help` tool** — lists every Neuron command with a one-line explanation, grouped
  (per-turn loop / search / contexts / upkeep / data). `status` now ends with a
  pointer to it, so the human (not just the model) can see what each feature does.

### Fixed
- **The MCP server no longer crashes when cloud creds are set but the `cloud`
  extra isn't installed.** `db.py` caught the bridge on a fresh install
  (`ModuleNotFoundError: libsql_client`): it now warns and falls back to the local
  engine instead of failing to import.
- **Live Graph Console can be stopped with `q`/`Esc`** instead of `Ctrl+C` (which
  tore down the whole `Configuration.bat`). It polls for the key during the refresh
  interval, so quitting is instant.
- **Bridge has Plan-B pre-flight checks.** It needs a runner for `mcp-proxy`
  (uv/uvx/pipx) — if none is found it offers to install `uv`. And if Turso cloud
  creds are set but `libsql-client` isn't installed, it offers to install it,
  otherwise serves the local engine. (libsql is only for the cloud tier — the
  bridge itself never needs it.)
- **Add-to-AI now leads with a clear "[DONE] added automatically" banner** so it's
  obvious the config was written for you; the by-hand steps are marked reference-only.
- **Heuristic extraction no longer promotes Italian action verbs / connectors to
  graph nodes** (`usiamo`, `riduciamo`, `disegnare`, `adottiamo`, `passiamo`,
  `via`, …). The IT+EN stoplist was extended with the common conjugations
  (especially the "noi" `-iamo` form). Still 0-token and deterministic —
  `Usiamo FastAPI con Redis, riduciamo la latenza` now extracts
  `[fastapi, redis, latenza, …]` instead of the verbs.
- **Self-links can no longer be created** (`react --analogy--> react`, including
  case variants like `React`/`react`): a central guard in `Graph.add_link` rejects
  `source == target` for *every* path (auto-link, store, semantic flash).

### Planned
- A curated-memory skill so MCP clients use Neuron correctly (quality up, tokens down).
- `status` points to `/help`; a `help` tool documents every command in one line each.
- Optional local-LLM (Ollama) validator layer, configurable from `Configuration.bat`.

## [4.0.0] — unreleased (release target after a full fix + test pass)

The first 4.x release: a stabilization and installer overhaul built on the 3.3.x
codebase. MAJOR because default data locations and shipped behavior changed
(see **Changed** / **Removed**).

### Added
- **`Configuration.bat`** — one interactive hub for everything: install/update,
  "Add Neuron to your AI" (with a copy-paste tutorial per client — Claude
  Desktop/Code, Cursor, VS Code, OpenCode, Zed, ChatGPT/bridge), Bridge & Cloud
  Turso, tests, the live graph console, a clean uninstall, and a seed-DB guide.
- **Complete prebuilt PyTurso wheel matrix (CPython 3.10–3.14)** in `vendor/` — every
  supported Python installs fully offline, no Rust/MSVC compiler needed.
- **Embedding-model pre-warm** at the end of install (skippable, offline-safe) so the
  first real use is instant.
- **Install logging** — every install run is captured to
  `%LOCALAPPDATA%\Programs\neuron\logs\`, so errors that scroll off are recoverable.

### Changed
- **Graphs persist to a stable per-user location by default** —
  `%LOCALAPPDATA%\neuron\graphs` on Windows, `$XDG_DATA_HOME/neuron/graphs`
  elsewhere — surviving restarts **and** reinstalls. Override with `NS_GRAPHS_DIR`.
  (The old default was package-relative and could resolve inside the venv.)
- Install consolidated into a single menu: **FULL / Dependencies / PyTurso**; FULL
  doubles as the update path (`pip --upgrade`, and an older bundled wheel never
  shadows newer source).
- The MCP server now reports `neuron.__version__` instead of a hardcoded string.

### Fixed
- **Vector tools crashed** (`vector_search` / `find_candidates` / `auto` / `pre_turn`)
  with `I/O error: short read on page 1` when the shipped seed was a truncated stub.
  The seed is now validated (real SQLite, ≥ 512 bytes) and any DB/engine error falls
  back to the Python path instead of crashing.
- **`_refine_domain` always raised `NameError`** (`_pack_vector` → `pack_vector`) —
  domain refinement was silently dead.
- **New contexts crashed on first save** with `open: NotFound` — `turso.connect()`
  needs the parent directory to exist; it is now created for both engines.
- **`check.ps1` crashed** when `rustup` wasn't installed, and wrongly flagged
  Rust/MSVC as failures when PyTurso already worked from a wheel — the toolchain is
  now reported as "not needed" and every external-tool call is guarded.
- **`UnicodeEncodeError`** on default Windows consoles (cp1252) in several helper
  scripts — output is UTF-8 both in the hub and at the source.
- **Menu flicker** in `Configuration.bat` — the arrow menu redraws in place instead
  of clearing the screen on every keypress.

### Removed
- The shipped 26-byte `base_knowledge.db` seed stub. Neuron now ships **without** a
  seed (it works empty); build your own via the "Seed knowledge DB" guide or
  `scripts/import_vault.py`.
