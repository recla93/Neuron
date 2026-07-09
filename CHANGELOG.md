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

## [5.0.2] "Synapse" — 2026-07-09

Installer hardening release. All bug fixes, no behavior change to the server or
memory model — from real-world Windows installs (Luca's field reports) plus a
review pass over every "Add to your AI" path. No re-embed or data migration needed.

### Fixed
- **Client configs could be overwritten with the literal `null`.** `Register-Mcp`
  (`install.ps1`) fed an empty/0-byte `claude_desktop_config.json` / `mcp.json`
  into `ConvertFrom-Json`, which returns `$null` on some PowerShell versions;
  that `$null` was then re-serialized to the 4-byte string `null`, clobbering
  the file. Empty files now start from a fresh object; a parsed-but-non-object
  result is left untouched with a by-hand instruction.
- **UTF-8 BOM broke Claude Code's `settings.json`, and the "fix" broke installs
  outright.** Every JSON/`.env` write used `-Encoding UTF8`, which in Windows
  PowerShell 5.1 prepends a BOM; Claude Code's `JSON.parse` chokes on the leading
  BOM byte (`SyntaxError: Unexpected token`). The obvious next step is
  `-Encoding utf8NoBOM`, but that token was added in PowerShell 7 — 5.1
  (the default host on every stock Windows box) rejects it with "Cannot bind
  parameter 'Encoding'", so every JSON/`.env` write in `Save-Json`,
  `Update-EnvFile`, `Scrub-Env`, `Register-Mcp` and `uninstall.ps1` failed
  silently, which is what turned "install dir not present" and half-cleaned
  uninstalls into the visible symptoms Luca hit. Every writer now goes through
  a new `Write-Utf8NoBom` helper backed by
  `[System.IO.File]::WriteAllText(..., [System.Text.UTF8Encoding]::new($false))` —
  same bytes on both 5.1 and 7+, no BOM.
- **`check.ps1` reported a good install as broken.** It looked for the venv under
  the repo (`$SrcDir\.venv`) instead of the real install dir
  (`%LOCALAPPDATA%\Programs\<slug>\.venv`), so `.venv`/mcp/fastembed/pyturso
  all showed missing right after a clean install. Now resolves the install dir
  via `_neuron_paths.ps1` like every other script.
- **Microsoft Store Python silently broke installs.** The Store build runs under a
  virtualized filesystem, so a venv created under the install dir can be redirected
  into the package's `LocalCache`, invisible to every other process ("installs fine,
  then nothing finds its own folders"). `install.ps1`/`check.ps1` now detect a
  `...\WindowsApps\...` interpreter and refuse it with a clear remedy; uninstall
  cleans up any leftover Store-Python shadow copy of the install.
- **Background processes failed to start** (`bridge`, cloudflared tunnel, manual
  server start). `Start-Process -RedirectStandardInput 'NUL'` doesn't understand
  the Windows `NUL` device — it resolves `NUL` as a relative filename and throws
  `FileNotFoundException`. Now redirects stdin from a real empty file under
  `%TEMP%\neuron5` (same immediate-EOF effect, works on every host).
- **`Save-Json` could report success after a silent rollback.** It didn't return a
  status and only verified "is it valid JSON", not "did our entry survive". Claude
  Code's deep `~/.claude.json` was truncated by `ConvertTo-Json -Depth 20` (real
  data → the literal `System.Collections.Hashtable`) — still valid JSON, so verify
  passed, but the `mcpServers` entry was gone. Depth raised to 100; `Save-Json`
  now returns `$true`/`$false`, logs the exact failure reason, and saves the failed
  output to `<path>.neuron-failed-write`. New `Assert-JsonKey` re-reads the file
  after every write and confirms the entry is really on disk.
- **Plugin/hook installers didn't verify where they landed.** The OpenCode plugin
  dir is now derived from `opencode.json`'s location (correct on non-standard
  installs) instead of hardcoded; both installers verify the file exists at its
  target after copy and that the config entry survived the JSON round-trip, and
  return a real success/failure status.
- **"Add to your AI" always showed a green `[DONE]`** even when the registration
  failed. All six client branches now aggregate every step's real result (MCP
  write + on-disk verification + plugin/hook install) and switch the banner to a
  clear failure message with manual copy-paste steps when anything didn't complete.
- **Uninstall printed a wall of red `Access denied` errors.** Enumerating processes
  to find who holds the install dir open threw `Win32Exception` on every
  system process the user can't inspect; `-ErrorAction SilentlyContinue` on
  `Get-Process` doesn't cover the later `$_.Path` access. Wrapped the property
  access in a per-item `try/catch`, and moved the pause before the reinstall
  prompt so nothing scrolls away.
- **Duplicate v4+v5 registrations are now flagged.** Upgrading from the old
  `neuron` slug to `neuron5` left both entries registered side by side (duplicate
  `mcp__neuron__*` and `mcp__neuron5__*` tools); the installer detects a leftover
  `neuron` entry and points at how to remove it (never deletes automatically).
- **Signpost trimmed** to stay under the 1000-char CI budget (`SIGNPOST_BASE`).

## [5.0.1] "Synapse" — 2026-07-09

### Changed
- Single configuration launcher. The transitional side-by-side setup shipped in
  5.0.0 (`Neuron5Config.bat` + `scripts/neuron5-config.ps1` as v5-only twins of
  the v4 launcher) is collapsed: the v4 `Configuration.bat` /
  `scripts/configuration.ps1` are removed, and the v5 launcher takes those
  canonical names. Behavior and MCP registration key (`neuron5`) are unchanged;
  README/INSTALL/DEVELOPER already point at `Configuration.bat`, so
  double-clicking it now runs the v5 configurator directly.

## [5.0.0] "Synapse" — 2026-07-09

The "brain" release: Neuron stops being a tagged store and becomes an associative
memory — Hebbian link reinforcement, salience-aware ranking, spreading activation.
MAJOR because the default embedding model changes (existing stores must re-embed).
Merged from `feat/neuron-bomb` into `master`; the pre-merge 4.0.0 "Stimulus" line is
preserved on the `4.x` branch.

### Added
- **Sleep-mode + pre-staging** (E3.3/E3.4): when a context is loaded after being idle >30 min, Neuron
  consolidates it (if `NS_CONSOLIDATE_AUTO`) and pre-computes the top stimulus, stored in `meta`.
  `pre_turn` serves that "while you were away" stimulus once if still fresh — a warm start that works
  around MCP's lack of push. `Graph.sleep_maybe()` / `take_staged_stimulus()`.
- **Cross-context drift links** (E3.1/E3.2): when a node from another *visited* context surfaces
  alongside the current keywords, Neuron forms an implicit `drift` link (no rationale, born
  tangential, cooldown 5, pruned after 3 idle turns, reinforced via the Hebbian counter). They stay
  out of the normal views and surface only on a deep `get_context(depth≥3)` query — implicit
  cross-domain bridges, opt-in. `Graph.form_drift_link()`, `Link.target_context`.
- **Piggyback stimulus** (E2.5): `store_turn` and `pre_turn` append a compact one-line associative
  stimulus (top spreading-activation node), capped to ~40 tokens and suppressed below an activation
  floor — continuous stimulation without MCP push. Token budget documented in ADR-003 (E2.6).
- **Hebbian reinforcement** (E2.1): links whose endpoints co-occur in a turn accrue a
  `co_activation_count` (≤1 per 2 turns) and get promoted `tangential→medium→strong` at 3/8 —
  associations that keep firing together wire together. `Graph.reinforce_coactivation()`.
- **Unified flashes** (E2.4): the three heuristics (dormant / cross-domain / creative leap) now feed
  one selector — `spreading_activation` scores them and only the top-2 by activation are emitted,
  ordered by relevance instead of a fixed dump of three.
- **Spreading activation** (E2.3): `Graph.spreading_activation()` propagates activation k hops
  from seed keywords along links, weighted by (Hebbian) link strength × node salience × per-hop
  decay — surfaces the strongest association even without a direct vector match. Wired in E2.4.
- **Composite salience-aware retrieval** (E2.2): `get_context` ranks nodes by
  `sim·0.5 + salience·0.3 + recency·0.2` (`RANK_WEIGHTS`, tunable) — retrieve what matters, not
  only what matches. Auto-consolidation now protects high-salience nodes from being merged.
- **Configurable embedding model** via `NS_EMBED_MODEL`; `VECTOR_DIM` from `NS_EMBED_DIM`,
  dimension guard on first embed (E0.1). Re-embed script `scripts/reembed.py` (E0.3) and
  model↔store coherence guard at load (E0.2). Benchmark harness `scripts/bench_embed.py` (EX.2).
- **Consolidation**: `Graph.consolidate()` merges near-duplicate nodes (cosine > 0.85) and drops
  orphans into a recoverable `_graveyard`; MCP tool + `neuron consolidate` CLI + `NS_CONSOLIDATE_AUTO` (E1).
- Cheap vector fallback: missing vectors embedded once, cached and persisted (E1.1).
- **Auto-handshake for AI clients** (installer): OpenCode gets a plugin
  (`experimental.chat.system.transform`) and Claude Code a `SessionStart` hook —
  both push Neuron's opening instructions into context on every turn/session
  automatically, instead of relying on the model to remember to call `help`.
  Installed/removed per-client from `Neuron5Config.bat`; other clients keep the
  existing server-side `instructions` handshake.
- **Start/Stop MCP server** menu in the installer, plus a fully granular
  uninstall: five independent opt-in toggles (MCP de-registration, client
  plugins/hooks, data wipe, secret scrub, cache wipe) instead of two blanket
  yes/no prompts, with an explicit "left in place" summary. Registration and
  removal paths are resolved from `%USERPROFILE%`/`%LOCALAPPDATA%`, so both
  work identically on any Windows account.
- **Embedding-model switcher** in the installer: pick the multilingual default
  (~380MB) or a lightweight English-only fallback (~90MB) from a menu. Writes
  `NS_EMBED_MODEL` directly into each already-registered client's MCP entry
  (not just `.env`, which most clients never read), offers a pre-warm and an
  optional `scripts/reembed.py --all` run so existing data stays searchable
  after the switch.

### Fixed
- **Installer manual "Start" froze the config menu**: `Invoke-StartServer`
  launched Neuron without redirecting its stdin, so the detached MCP process
  (which blocks reading stdin waiting for a client) ended up sharing the
  console's input with the interactive menu — every keystroke went to the
  child instead of the menu's `ReadKey`. Fixed by redirecting the child's
  stdin to the null device (`-RedirectStandardInput 'NUL'`), applied
  defensively to the other two background-process launch sites too
  (cloudflared tunnel, bridge).

### Changed
- **Default `NS_EMBED_MODEL` → `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`**
  (384-dim, EN+IT). Bench on real hardware: IT recall 0.89→1.00, same dim (no schema change),
  faster (ADR-001, E0.4). English-only workloads can pin `all-MiniLM-L6-v2` via env.
  **Breaking:** stores embedded with the old model are re-embedded on load — run `scripts/reembed.py`.

## [Unreleased]

_Next up, after 4.0.0 ships:_
- An optional local-LLM (Ollama) validator layer on top of the 0-token heuristic,
  configurable from `Configuration.bat`.

## [4.0.0] — unreleased (release target after a full fix + test pass)

The first 4.x release: a stabilization and installer overhaul built on the 3.3.x
codebase. MAJOR because default data locations and shipped behavior changed
(see **Changed** / **Removed**). Everything below — installer, `help`, heuristic
cleanup, bridge Plan-Bs, the crash fixes — is part of 4.0.0; there is no 4.0.x/4.1
split until this ships.

### Added
- **`Configuration.bat`** — one interactive hub for everything: install/update,
  "Add Neuron to your AI" (with a copy-paste tutorial per client — Claude
  Desktop/Code, Cursor, VS Code, OpenCode, Zed, ChatGPT/bridge), Bridge & Cloud
  Turso, tests, the live graph console, a clean uninstall, and a seed-DB guide.
- **`help` tool** — lists every Neuron command with a one-line explanation, grouped
  (per-turn loop / search / contexts / upkeep / data); `status` ends with a pointer
  to it, so the human (not just the model) sees what each feature does.
- **Curated-memory skill** (`skills/neuron-curated-memory/SKILL.md`) — teaches any
  MCP client to use Neuron well: load context before answering, then save a *curated*
  turn (3-5 concept keywords, never verbs/filler, typed links, no self-links).
  Install as a Claude skill (copy the folder into `~/.claude/skills/`) or point a
  client's instructions at the file.
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
- **The MCP server no longer crashes when cloud creds are set but the `cloud` extra
  isn't installed** — `db.py` warns and falls back to the local engine instead of a
  `ModuleNotFoundError: libsql_client` at import (this killed the bridge preflight).
- **Bridge Plan-B pre-flight** — it needs a runner for `mcp-proxy` (uv/uvx/pipx) and
  offers to install `uv` if missing; if cloud creds are set but `libsql-client` isn't,
  it offers to install it, otherwise serves the local engine — launching Neuron with
  the cloud creds suppressed (`NEURON_NO_DOTENV`) so it starts even against an older
  installed `db.py`. (libsql is only for the cloud tier; the bridge never needs it.)
- **Heuristic extraction no longer promotes Italian action verbs / connectors to
  graph nodes** (`usiamo`, `riduciamo`, `disegnare`, `adottiamo`, `passiamo`, `via`,
  …) — the IT+EN stoplist was extended (esp. the "noi" `-iamo` form). 0-token,
  deterministic: `Usiamo FastAPI con Redis, riduciamo la latenza` → `[fastapi, redis,
  latenza, …]` instead of the verbs.
- **Self-links can no longer be created** (`react --analogy--> react`, incl. case
  variants like `React`/`react`) — a central guard in `Graph.add_link` rejects
  `source == target` on every path (auto-link, store, semantic flash).
- **Live Graph Console stops with `q`/`Esc`** instead of `Ctrl+C`, which used to tear
  down the whole `Configuration.bat`.
- **Add-to-AI leads with a clear "[DONE] added automatically" banner**; the by-hand
  steps are marked reference-only.
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
