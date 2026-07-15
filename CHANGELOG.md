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

## [Unreleased] — 5.4 line

### Changed — GUI identity and single front door
- Added the Neuron logo to the Control Center and Setup Wizard, with a modern card header,
  bundled application icon and a more spacious default window.
- Added `NeuronInstaller.exe`, a 12.8 KB Windows bootstrapper that runs the first install
  without Python, pip, Tkinter or a terminal window.
- Added a GUI Turso connection form and made `install.ps1 -Yes` genuinely non-interactive;
  the optional-provider step now defaults to skip instead of reading stdin.
- Added [docs/CORE_AUDIT.md](docs/CORE_AUDIT.md): 235 tests pass, dependencies are healthy,
  and the core persistence/search/extraction paths have been checked.
- The Desktop shortcut is now named **Neuron — Control Center** and is the documented
  entry point for setup, maintenance, Turso, Bridge/Tunnel, vault import and deploy/update.
- Removed obsolete user-facing BAT launchers; retained only `scripts/run_mcp.bat` as a
  compatibility launcher for legacy MCP client registrations.
- Refreshed README and INSTALL instructions to match the GUI-first workflow.
- The installer now detects an existing installation instead of presenting an empty
  window, and exposes **Status**, **Repair**, and **Uninstall** recovery actions.
  Uninstall keeps memory data by default; **Uninstall + memory** is an explicit purge.
- The Control Center exposes the same recovery actions and falls back to
  `neuron setup --uninstall` when the repository maintenance script is unavailable.
- Fixed the bootstrapper idle/blank state when the Desktop Control Center shortcut is
  missing or the source folder cannot be auto-detected: controls remain visible, the
  source can be selected manually, and Repair can recreate the shortcut.
- Added an explicit **Start process** button and an initial output message so the
  bootstrapper never looks idle before the first action.

### Changed — code-quality pass (analysis 2026-07-14, all backward-compatible)
- **Single source of truth for paths/tunables: `neuron/config.py` (P0 #3, P1 #9).**
  `_default_graphs_dir()` / slug resolution were copy-pasted into `server.py`,
  `manage.py` and `setup.py`; they now all delegate to `config.py`. Added
  `env_int`/`env_float` helpers and made ~20 hardcoded tunables (Hebbian/drift
  cooldowns, salience decay, sleep/stage seconds, episodes caps, `MAX_NODES`,
  consolidate cadence, stimulus/topic thresholds, keyword/topic limits,
  `MAX_AUTO_LINKS`) overridable via `NEURON_*` env vars with the old values as
  defaults.
- **Error visibility (P0 #2).** ~12 silent `except Exception: pass` blocks in
  `server.py`/`registry.py`/`models.py`/`search.py`/`clients.py` now log at
  debug/warning (stderr, never the stdio JSON-RPC stream); genuinely best-effort
  paths (migrations, retries, process-kill) left as-is.
- **Shared graph health metrics (P1 #8).** `models.compute_health()` is now the
  one place strong/medium ratio, pruned ratio and nodes/turn are computed; the
  `status` and `summary` tools read from it.
- **Hot-path complexity (P1 #7).** `_score_tokens` bigram bonus is O(N) via a
  precomputed participation map (was O(keywords x tokens)); `consolidate()`
  early-terminates when <2 candidates or all nodes are protected; `engine._link`
  uses an O(1) keyword→node map; the Python cosine ceiling is documented.
- **API hygiene (P1 #15).** Added `__all__` to the public modules.
- **Dependency bounds & cleanup (P2 #12/#13/#14).** Upper bounds on `mcp` (<2.0)
  and `fastembed` (<1.0); dropped the unused `pytest-asyncio` dev dep; removed the
  stale `_to_delete/` folder (27 lock/temp files).
- **Dispatcher refactor (P0 #1).** The ~675-line `_call_tool_impl` if/elif chain in
  `server.py` is now 22 module-level `_tool_<name>(arguments, ctx, g)` handlers plus a
  `_HANDLERS` dict; the dispatcher is 7 lines. Behaviour unchanged; handlers are now
  individually unit-testable.
- **Test-mock de-duplication (P1 #5).** The ~50-line fastembed/mcp/turso mock header
  duplicated by `test_core.py` and `test_fivefix.py` is extracted into
  `tests/_mockdeps.py` (`install_mock_deps()`). Files using real deps via
  `importorskip` are untouched.

### Changed — leaner skill / handshake delivery (fewer per-session tokens)
- **Handshake compressed ~48% and de-duplicated.** The SessionStart hooks
  (Claude Code, cowork plugin, OpenCode) each carried a ~1,400-char copy of the
  full loop + anti-misuse rules — and several fire per session. They're now a
  compact ~730-char per-turn loop (ASCII-only, so no cp1252 breakage); the full
  curation rules live on demand in the skills, not in every session's context.
- **Skills consolidated 4 → 2.** `auto-context` → `playbook` (the full workflow);
  `SKILL_base` and `SKILL_full` deleted (subsets that duplicated it); `curated`
  kept separate. The signpost, `skill` tool default/enum, hooks and docs now all
  point at `playbook`. (Both the packaged `src/neuron/skills/` and the editable
  repo `skills/` copies stay byte-identical, enforced by the drift-guard test.)

### Fixed — remote store resilience (T76)
- **Turso Cloud writes stopped after an idle disconnect ("only the seed ever
  arrives").** The retry wrapper re-used the *same dead client object* on every
  attempt: once the Hrana WebSocket/HTTP session dropped, all 4 retries failed
  identically and the turn was never persisted. `RemoteTursoConnection` now
  **reconnects between retry attempts** (fresh client), and if a
  `libsql://`/`wss://` endpoint refuses or drops the sync client it **falls back
  to the stateless `https://` transport** and sticks with whichever works.
  Added `ping()` (SELECT 1 + one reconnect) for health checks. A failed save
  still leaves the graph dirty, so the next turn re-sends everything — now to a
  *live* connection.

### Added — model switch + test runner in the GUI (T82)
- **Model section.** Two one-click embedding-model presets — Multilingual
  EN+IT (~380 MB, `paraphrase-multilingual-MiniLM-L12-v2`, the default) and
  English-only (~90 MB, `all-MiniLM-L6-v2`) — written as `NS_EMBED_MODEL`
  into the same `.env` the server resolves (upsert, comments preserved; both
  models are 384-dim so no schema change). After a switch the GUI offers to
  **Re-embed Store** (also its own button): streams `scripts/reembed.py
  --all` with the new model.
- **Run Tests button.** Runs the pytest suite from the source/install root
  inside the pane; on success a green "all tests passed", on failure the
  full output stays and the GUI offers to launch **Repair** right away
  (with a pointer to Deploy Update when it's the code that's stale).
  Foreground commands now support a completion hook (`_fg_on_done`).

### Changed — Network UX polish (T81)
- **No more stray CMD windows.** The Bridge/Tunnel *grandchildren*
  (`uvx mcp-proxy`, `cloudflared`) are now launched with `CREATE_NO_WINDOW`
  too — they run as pure background processes. Stop Network (taskkill /T)
  ends them, and **closing the GUI window now also terminates the stack**
  (WM_DELETE_WINDOW handler; previously they lingered orphaned).
- **Clean success summary.** While the network starts, the full trace
  streams; the moment the tunnel URL arrives the pane is cleared and replaced
  by a compact recap (server alive, Bridge endpoint + proxy runner, Tunnel
  connector URL, copy hint). Routine INF chatter is suppressed from then on —
  warnings/errors/watchdog events still surface, and any failure flips the
  log back to full verbosity. The trace is never cleared when something
  fails.
- **Fixed mojibake (`â†’`, `âœ“`).** The GUI decoded child output with the
  legacy locale codepage while children write UTF-8: every pipe now reads
  `encoding="utf-8"` and Python children get `PYTHONUTF8=1`.
- **Intentional stops no longer print a red "exited with code 1"** — Stop
  marks the processes, which then report a plain "stopped".

### Fixed — GUI output pipeline + installer fallbacks (T80)
- **No Bridge/Tunnel output ever reached the GUI.** The queue-poll loop that
  moves child-process lines into the output pane was only scheduled by
  *foreground* commands — pressing Start Network on a fresh session started
  the processes but nobody ever drained their queues. The loop now starts
  once at boot (and the per-command schedules, each of which leaked a
  duplicate self-perpetuating loop, are gone).
- **A corrupt/odd logo PNG could kill GUI startup** — `_load_logo` caught only
  `TclError`/`OSError`, but a bad image raised `TypeError`. The logo is
  cosmetic: any failure now just skips it.
- **Import Vault / Deploy / Uninstall runners** now use `CREATE_NO_WINDOW`
  (no console flash) and `PYTHONUNBUFFERED=1` for Python children (live
  streaming instead of buffered bursts).
- **NeuronInstaller.exe: no fallback when `install.ps1` wasn't found.** The
  label said "choose the project folder" but no chooser existed and the
  Install button silently did nothing. Added a folder-picker fallback (on
  startup failure and on Install click) that validates the chosen folder
  contains `install.ps1`. Flags audit: `-Yes`/`-WithLlmProviders`
  (install.ps1) and `-Yes`/`-Data` (uninstall.ps1) all exist; the built-in
  `neuron setup --uninstall` fallback is correct. **Rebuild required:**
  `installer/build-installer.ps1` (the shipped .exe predates this fix).

### Fixed — GUI round 2 (field report, T79)
- **`connect` from the GUI died with `EOFError`.** Typed in the command
  console it ran with a piped stdin and crashed on the first `input()`. It now
  routes to a real terminal (like the Connect button), and `neuron connect`
  itself exits with a clear message instead of a traceback when stdin isn't
  interactive.
- **Console button "did nothing".** The new terminal ran `neuron console` and
  closed on exit/error before anything was readable; Windows terminals now run
  under `cmd /k` (window stays open).
- **Network sequencing + preflight.** "Start Cloud/Stop Cloud" renamed
  **Start/Stop Network** (Bridge+Tunnel are the HTTP connector stack, not
  Turso). Start now (1) checks dependencies first — mcp-proxy runner
  (uvx/uv/pipx) and cloudflared — printing exactly what's missing and the
  winget/pip command to install it; (2) starts the Bridge; (3) opens the
  Tunnel **only after** the Bridge really listens on its port (90s timeout,
  clear error otherwise). Both run *inside* the GUI (no terminal windows to
  keep open) and the log says so.
- **Collapsed sidebar sections reopened in the wrong place** (buttons dumped
  after the Stop button): re-pack now anchors to the section header.
- **New "Deploy Update" button** (Setup): runs `scripts/deploy.ps1 -Yes` and
  streams it — the GUI way to sync the repo into the active install.
- **Removed 6 unreferenced launcher/helper `.bat`s** (`Neuron-Manage`,
  `Neuron-Setup`, `scripts/check|deploy|neuron-summary|run-tests.bat`) — all
  superseded by the GUI; kept `Neuron.bat`, `Configuration.bat`,
  `Install-GUI.bat`, `run_mcp.bat`, `build-and-install.bat` (referenced by
  docs/clients).

### Changed — GUI control center (T74/T75)
- **The GUI is now a persistent control center.** A command console at the
  bottom of the output pane runs any `neuron` subcommand (manage, consolidate,
  doctor, setup…) without a terminal — with history (Up/Down) and validation;
  foreground commands no longer refuse to run while Bridge/Tunnel are up.
  New **Import Vault** button (folder picker → streams `import_vault.py`) and
  **Prune** shortcut.
- **Watchdog for Bridge + Tunnel.** "Start Cloud" marks both processes
  keep-alive: if one dies it restarts automatically with exponential backoff
  (2s→60s); Stop/Stop Cloud disables resurrection. The tunnel's public
  `*.trycloudflare.com` URL is parsed from the stream and pinned in the status
  bar — click to copy the `/mcp` connector URL.
- **`neuron tunnel` supervises cloudflared by default.** Cloudflare *quick*
  tunnels have no uptime guarantee (idle drops, edge maintenance) — that's the
  "tunnel randomly dies" report. The command now relaunches cloudflared with
  backoff and prints the new URL each time (`--once` restores the old
  single-run behaviour; quick-tunnel URLs change on every restart).

### Fixed — core precision (T78)
- **Sentiment "urgent" fired on substrings** — "down" inside *download*,
  "help" inside *helpers*, "crash" inside *crashlytics* flagged innocent turns
  as urgent. Single-word sentiment cues now match whole tokens only;
  multi-word cues ("not working") keep the full-text check (and actually work
  now — they could never match a single token before).
- **Bigram promotion had an unvalidated first pass** that could inject
  over-long or pattern-invalid compound keywords before the validated loop;
  collapsed into one validated pass. Added `works/working/worked` to the
  stoplist (verbs were becoming nodes).
- **Vector-search tiers disagreed on the relevance floor.** The Python
  fallback returned any `sim > 0` while the Turso SQL tier filters at 0.3, so
  offline installs got noise the cloud tier would never show. Both tiers now
  share the 0.3 threshold.

### Changed — unified installer / single entry point
- **Graphical Setup Wizard (`neuron gui` → Install Wizard, or `neuron gui
  --wizard`).** The installer is no longer a mechanical terminal flow: a guided
  5-step wizard (Welcome → environment checks → client selection with
  detected/not-found badges → install with progress bar + live log → summary)
  drives the registration engine (`neuron.clients`) **in-process** — structured
  results, no stdin prompts to hang on. Clients found on the machine are
  pre-selected; the embedding-model pre-download (~380 MB) is an explicit
  opt-in checkbox. Every config file is still backed up before being touched.
- **GUI hub fixes.** Sidebar buttons pointed at flags that don't exist
  (`setup --deploy`, `setup --test`, `manage --bench`) — replaced with real
  subcommands; interactive tools (`console`, `connect`) now open in a real
  terminal instead of hanging on a piped stdin; fixed the context label showing
  `None` (`Thread.join()` used as a value) and the ttk theme never applying
  (`tk.Ttk.Style` → `ttk.Style`).
- **Visual hub: `neuron gui`.** A small Tkinter launcher (stdlib, no new deps, no
  PyInstaller) is the centralized, clickable manager — quick read-only checks
  (status/overview/doctor) render inline; interactive tools (setup/manage/bridge/
  tunnel/connect/console) open in a terminal. Exposed as a windowed `neuron-gui`
  executable via `[project.gui-scripts]` (pip builds it with no console window);
  `install.ps1` now drops **Desktop + Start-Menu shortcuts** pointing at it
  (via `pythonw`), replacing the old shortcut that just launched the stdio server.
- **`python -m neuron` is now the one entry for install + management + features.**
  `bridge`, `connect` (Turso Cloud) and `console` (graph diagnostics) moved from
  `scripts/` into the package and became `neuron bridge|connect|console`
  subcommands (also surfaced in the `neuron manage` menu). `scripts/*.py` are thin
  back-compat shims. `console` now resolves paths via `neuron.config` + the packaged
  seed, so it works from a pipx install.
- **Windows installer handles Microsoft Store Python and installs Python if absent.**
  `install.ps1` previously *rejected* Store Python; it now prefers a real
  interpreter, offers to install one via **winget** when none (or only the Store
  build) is found, and otherwise falls back to using the Store Python (resolving its
  real versioned exe) with a caveat instead of hard-failing.
- **New `install.sh` for macOS/Linux** — finds or installs Python 3.10+
  (brew/apt/dnf/pacman), installs Neuron via pipx or a venv, then runs
  `neuron setup`.
- **Retired the 2,723-line `scripts/configuration.ps1`** — it duplicated
  install/register/feature logic now owned by the Python CLI. It's now a 38-line
  launcher that opens `neuron manage` (so `Configuration.bat` and existing
  shortcuts keep working). Added `neuron tunnel` (cloudflared) so the last
  Windows-only feature is cross-platform too; deleted the orphaned, dead
  `scripts/_neuron_utils.ps1` (23 KB, dot-sourced by nothing). `Neuron.bat` is now
  the one clickable hub — its **Setup** runs `install.ps1` on a fresh machine.

### Fixed
- **CI "skills packaged" check was hardcoded to the old skill count.** After
  the 4→2 skill consolidation the wheel ships 3 `.md` files but `ci.yml`
  asserted `>= 5`, turning every build red. The check now compares the wheel's
  skill set against `src/neuron/skills/` on disk — exact match, count-proof.
- **Skill docs drifted from the real tool surface.** `neuron-curated-memory`
  showed `confirm(keyword=…)` (the parameter is `keywords`, a list) and an
  example link with `link_type: "causal"` (invalid enum — `cause-effect`)
  pointing at a keyword not in the stored set (the curation gate would refuse
  it as dangling); `playbook.md`'s tool table was missing 6 tools
  (`extract`, `consolidate`, `merge`, `dedup`, `help`, `skill`). All fixed in
  both skill dirs (drift-guard kept green).
- **`neuron setup --install --yes` hung on stdin.** The pre-warm prompt guard
  (`setup.py`) always evaluated to the interactive branch, so a non-interactive
  install blocked on `input()`. `--yes` now skips the prompt (and the 380MB
  pre-download) entirely.

### Removed
- **Repo hygiene.** Deleted the deprecated `scripts/seed_vault.py` stub
  (superseded by `scripts/import_vault.py` since T10, self-referencing only)
  and the local build debris (`build/`, `dist/` with stale 5.0.x wheels,
  `UNKNOWN.egg-info/`, stray `.lock`, `__pycache__`); `.gitignore` now covers
  `/.lock`.

### Added
- **Test coverage for `neuron setup` and `neuron manage` (P1 #6).**
  `tests/test_setup.py` and `tests/test_manage.py` (12 tests) cover the path
  helper, status/repair/install exit codes and overview/export/context listing.

### Added
- **Universal lifecycle CLI: `neuron setup` (ADR-007).** Cross-platform
  install/repair/status/uninstall for macOS, Linux and Windows:
  `pipx install neuron && neuron setup`. Interactive numbered menu plus
  non-interactive flags (`--register-all/--repair/--status/--uninstall
  [--purge-data] --yes`, CI-friendly exit codes). Reuses the tested
  registration engine; new `deregister()` (JSON-safe, backup, idempotent,
  JSONC never rewritten, Codex TOML section removal). Client paths extended
  to macOS/Linux (Claude Desktop, VS Code). CI now smoke-tests
  `neuron setup --status` from the built wheel.
- **Balanced stimulus engine (T66).** Stimuli now serve BOTH recall and
  creative sparks: `stimulus_candidates` ranks by activation × novelty
  bonuses (dormancy, domain shift, tangential path) with over-familiarity
  damping (Hebbian count), tracks the best path, and the piggyback renders
  it interpretably — `🧠 java ⇢ servlet ⇢ cors (dormant 15t, →frontend)` —
  with an anti-echo cooldown that naturally rotates recall → spark.
- **`neuron manage` (ADR-007 fase 2).** Cross-platform day-to-day management:
  contexts overview (local + cloud, with counts and top concept), JSON export,
  consolidate, graph visualizer launcher, doctor — interactive menu or flags.

## [5.3.1] — 2026-07-11

### Fixed
- **Contexts never separated with the curated loop**: the domain-hysteresis
  auto-switch only lived inside `auto`, so `store_turn` (the recommended
  path) never fed the signal and everything accumulated in 'default' —
  which also starved cross-domain sparks and drift links. The hysteresis is
  now a shared helper wired into store_turn (explicit `context` still wins);
  the response surfaces the switch (`⇄ context switched → 'X'`) or the
  pending signal count.
- `store_turn` crashed with "no such table: meta" on a pristine store:
  pyturso creates an empty 0-byte file on connect, so the schemaless file
  passed the exists-check and the unguarded meta read in `load_sqlite` blew
  up the whole load. A schemaless store now loads as empty (the first save
  creates the schema), matching the missing-file semantics.
- Graph Visualizer: the 🎨 node-size slider had no effect — nodes carried a
  vis-network `value`, which switches to value-based scaling and silently
  ignores `size`. Dropped `value`; sizes are computed (salience × slider).

## [5.3.0] "Quality at the door" — 2026-07-11

Memory-quality release: the server now *enforces* good curation instead of
hoping for it, nodes carry facts (episodes) and loop compliance is measurable.
Two more modules leave the server monolith. No data migration needed (the
`episodes` table is created idempotently on first save).

### Added
- **Curation gate (T54).** `store_turn` keywords pass through
  `neuron/curation.py`: filler verbs, phrases and file paths are dropped with
  an in-context corrective note; near-duplicates are remapped onto the
  existing node (case/accents/EN-IT plural folding); link endpoints are
  canonicalized and dangling/self links refused. Soft gate: the turn goes
  through whenever at least one keyword survives.
- **Episodic payload (T56).** New `episodes(context, keyword, turn, text)`
  table; `store_turn` accepts an `episode` fact sentence, `pre_turn` returns
  the top node's recent facts (`facts: ...`). Capped per node, cleaned up with
  node removal, tolerant of legacy stores.
- **Loop-compliance telemetry (T55).** `status` reports per-session
  pre_turn/store_turn/other counts with a warning when stores outpace loads.
- **Doctor: live-process section (B6b).** `neuron doctor` also lists running
  `python -m neuron` servers with their parent app, kills orphans with
  `--fix`, flags stale-venv servers and per-app duplicates.
- **Graph Visualizer v2.** Reads through Neuron's own storage engine — so it
  now sees **Turso Cloud** too (the old version was raw-sqlite, local only) —
  exports every context with episodes/facts, and renders a redesigned
  interactive HTML: salience-sized domain-colored nodes, Hebbian-thickened
  edges, drift-link styling, dormant fading, hot-node halo, neighborhood
  highlight, search, domain/type filter chips, insights panel (hubs, most
  salient, dormant, strongest synapses, cross-context bridges), heartbeat
  pulse and a time-travel Replay slider that shows the memory growing.
  Includes an Obsidian-style 🎨 appearance editor: node/link/label size and
  physics sliders plus per-domain color pickers, persisted in the browser.
- **Menu:** `pytest -q` entry in the test menu; "Deploy update" entry
  (`deploy.ps1 -RunTests -Yes`) in Install/Update; "Claude Cowork
  (neuron-guard plugin)" in Add-to-AI — packages the plugin with
  forward-slash zip entries and guides the drag-into-chat install (the
  Settings uploader only accepts marketplaces).

### Changed
- **server.py modularization (T57, phases 1-3).** `extraction.py`
  (SemanticExtractor + lexicons) and `funnel.py` (signpost, skill registry)
  extracted verbatim with full re-export — ~2550 → ~2150 lines, test-suite
  untouched. `search`/`stimulus` remain (ADR-006).
- **Signpost compressed to 810 chars** (with live status ≤ ~912, cap 1000):
  anti-misuse rules included telegraphically; full rules live in the gate,
  opener, skills and client hooks.

### Fixed
- Numpy-array truthiness crash in the vector-search fallback (`pre_turn`
  "truth value of an array is ambiguous").
- A2 search-cache key collisions after garbage collection (weakref guard).
- `neuron doctor`/`register` crashed with UnicodeEncodeError on cp1252
  consoles (the → glyph) when run with a non-venv python — same UTF-8
  self-guard the other CLI scripts already had.

## [5.2.0] "Piano 05" — 2026-07-10

Core efficiency + centralized installer. The memory engine now writes only
active links per turn (A1), caches vector searches within a tool call (A2),
pre-warms the embedding model on startup (A3), and trims the loop hint to
one-shot (A4). The installer and client configs gain verify-after-write with
rollback (B5), a JSONC/MSIX-aware Python registration engine (B1-B4), and
section-aware Codex TOML merging. A new CoWork plugin delivers the Neuron
handshake to Claude Code sessions. No data migration needed.

### Added
- **`neuron register` / `neuron doctor` CLI.** Central Python engine
  (`src/neuron/clients.py`) for registering Neuron in every supported AI
  client: Claude Desktop (MSIX-aware), Claude Code (`claude mcp add`), Cursor,
  VS Code, Zed, Codex CLI. Non-destructive merge, verify-after-write, backup
  before every mutation, JSONC-safe manual snippets.
- **CoWork plugin for Claude Code** (`clients/cowork-plugin/`). Delivers the
  Neuron handshake via the standard plugin hook system; keeps the session
  start dependency-free (no neuron import, no venv).
- **Anti-misuse rules in handshake.** All client hooks now include explicit
  curation rules (3-5 concept nouns, typed links, no self-links, no secrets)
  to prevent shared-memory pollution from models that over-store.
- **Pre-warm embedding model (A3).** `_get_embedder()` runs in a worker thread
  during MCP server startup so the first `pre_turn` of a session doesn't pay
  the ~3s model load penalty.

### Changed
- **Link writes O(total) → O(active) per turn (A1).** `models.py` only marks
  links dirty when their `last_active_turn` changes; `inactive_turns` is
  derived at load time from the invariant `turn_count - last_active_turn`.
  On Turso Cloud this eliminates O(L) network rows per turn.
- **Vector search memoized per tool call (A2).** `_search_embeddings` caches
  results for the duration of one `call_tool` invocation; within `store_turn`
  the chain `auto_link → _build_context_window` no longer re-runs the same
  searches. Cache is invalidated on every new tool call.
- **Loop hint one-shot (A4).** The `→ next: fold this context…` teaching line
  is appended only once per process lifetime; subsequent tool calls skip it.
  When staged or live stimulus is present, a shorter tail is used instead.
- **Server identity = install slug (A6).** The MCP `server_name` now reads
  the installed slug (`neuron5` for v5, `neuron` for v4) instead of the
  hardcoded string `"neuron"`.
- **fastembed vectors coerced to `list[float]` (A5).** `_embed_one` now yields
  plain Python lists instead of numpy arrays, preventing downstream
  "truth value of an array is ambiguous" errors on truthiness checks and
  JSON export.
- **Codex TOML section-aware merge.** `Register-CodexMcp` in `install.ps1`
  now replaces only the `[mcp_servers.<slug>]` section, preserving every
  other server in the user's `config.toml`. Backup + verify + rollback.
- **`Register-McpNested` verify-after-write (B5).** JSON configs are
  re-read after writing; on failure the previous backup is restored.

### Fixed
- **Codex installer clobbered `config.toml`.** (continuation of 5.1.1 fix)
  `Register-CodexMcp` now merges non-destructively instead of overwriting.
- **VS Code `Register-McpNested` had no rollback.** A JSON-roundtrip failure
  after writing `settings.json` could leave a broken config; now restores
  the backup automatically.

## [5.1.1] — 2026-07-10

Bugfix release: client-config and installer fixes discovered after 5.1.0 shipped.
No changes to the memory engine or data — a reinstall (to refresh the deployed
`…\Programs\neuron5` copy) plus a client restart is enough.

### Fixed
- **OpenCode plugin never loaded.** `clients/opencode-plugin/neuron-handshake.mjs`
  used `export default`, which OpenCode doesn't pick up; it now uses a named export
  so the handshake actually runs.
- **Example configs pointed at the pre-v5 slug.** `clients/*.example.json` used the
  MCP key `neuron` and install path `…\Programs\neuron`; both now use `neuron5`
  (key and path). The Python module invocation stays `-m neuron`.
- **Codex installer clobbered `config.toml`.** `scripts/configuration.ps1` overwrote
  the user's existing `~/.codex/config.toml`; it now merges non-destructively, writes
  `hooks.json` with the correct schema (`type`/`command`), and sets the
  `[features] codex_hooks = true` flag.
- **Zed config used the wrong shape.** The Zed entry (in `configuration.ps1` and
  `clients/zed.example.json`) used a nested object; it now uses the flat
  `command`/`args` format Zed expects.

## [5.1.0] "Synapse" — 2026-07-10

Consolidation of the **FiveFix** work: correctness and precision fixes to the
search / embedding / extraction path, performance optimizations, and — the
headline of this release — concurrency hardening so a team can build a **shared
knowledge base on Turso Cloud** without corrupting or clobbering each other's
memory. No data migration or re-embed needed; existing single-user installs keep
working unchanged.

Behaviour changes to be aware of (all backward-compatible, no migration):
- Vector-search similarities on the Python fallback tier are now a true cosine
  in [0, 1] (they were an unbounded dot product), so results and the thresholds
  that depend on them behave correctly — expect different (better-calibrated)
  associations than 5.0.x.
- On a shared **remote** store, per-user session state (turn counter, staged
  stimulus, last-topic) moves to a local sidecar; each user's `turn_count` reads
  its empty sidecar once and restarts from 0 (one-time, harmless). Local
  single-user stores are unchanged.
- New env switches: `NS_ALLOW_SHARED_RECONCILE` (opt-in for coordinated
  consolidation on a shared store). New one-shot setup: `scripts/init_cloud.py`.

### Fixed
- **DB toggle in Configuration.bat was deleting credential lines.** The old toggle
  hardcoded cloud/local key names, deduplicated lines, and created placeholders —
  any deviation (duplicates, missing keys) caused data loss or "nothing to toggle".
  The toggle now simply flips the `#` prefix on every `TURSO_*` line in `.env`:
  no hardcoding, no line deletion, no re-entry needed.
- **DB check didn't reflect toggle state.** `check_cloud_config.py` read
  `os.environ` which was stale after a toggle; it now reads the `.env` file
  directly so the active mode is always accurate.
- **Vector similarity was a raw dot product, not a cosine, on the Python fallback
  tier.** `_search_embeddings` and `_refine_domain` scored non-normalized
  fastembed vectors with a bare dot product, so "similarities" ran well above 1
  (Neuron's own `vector_search` reported values up to ~9). Every downstream
  threshold (auto-link 0.30, weight tiers 0.45/0.65, dormant 0.38, cross-domain
  0.48) is calibrated for a true cosine in [0, 1], so on the fallback tier they
  were all trivially cleared — everything linked, every weight became "strong",
  flashes always fired. Both paths now compute a true cosine, consistent with the
  Turso `vector_distance_cos` tier.
- **The seed graph shadowed the user's live graph in vector search.**
  `_search_embeddings` returned on the FIRST non-empty DB, so whenever the seed
  (`base_knowledge.db`) matched anything the active graph's vectors were never
  searched on the Turso tier (and the two tiers disagreed: the Python fallback
  searched only the active graph). It now merges results across seed + active,
  keeping the max similarity per keyword.
- **Accented Italian words were truncated to garbage stems.** The ASCII-only
  tokenizer cut "città"→"citt", "perché"→"perch", "così"→"cos",
  "università"→"universit", leaking those stems as keywords, while the
  ASCII-folded stopwords ("piu"/"gia"/"puo") never matched "più"/"già"/"può".
  Extraction now folds accents before matching: real nouns survive clean
  ("città"→"citta") and accented connectors ("più", "perché", "così", "però",
  "cioè") are filtered.
- **`.env` diagnostic only inspected the first `TURSO_` line.** An unconditional
  `break` meant a malformed `TURSO_AUTH_TOKEN` was never reported when
  `TURSO_DATABASE_URL` happened to load. It now checks every `TURSO_` line.

### Optimized
- **Embedding cache.** `_get_embedding` is memoized per (embedder, text). Within
  a single turn the same keywords were re-embedded by `_refine_domain`,
  `_auto_link` (one query per keyword), the cross-domain loop and
  `_build_context_window`; they now hit the model once each.
- **Seed connection reuse.** The immutable `base_knowledge.db` connection is
  cached for the session instead of reopened on every search call. The writable
  active graph DB is deliberately still opened per-call to avoid stale reads.

### Changed
- Removed the unused Ollama/LLM extraction path from the server (`_llm_extract`,
  the `use_llm` param, `NS_LLM_*`). Server-side extraction is heuristic
  (0-token); richer extraction is the calling LLM's job via `store_turn`. The
  standalone CLI engine (`engine.py`) keeps its own LLM path.

### Added
- `.gitattributes` normalizing line endings (LF for code/docs, CRLF preserved for
  `.bat`/`.cmd`/`.ps1`) to end the CRLF↔LF diff churn and prevent recurrence —
  git normalizes at `add` time regardless of editor.
- `tests/test_fivefix.py` — regression tests for the cosine range, seed+active
  merge, seed connection reuse, accent folding, the embedding cache and
  credential sanitization.

### Concurrency (shared Turso cloud, multi-writer) — P1
- **Per-user session state no longer bleeds through the shared `meta` table.**
  On a shared cloud store the `meta` table has no per-user/per-context key, so
  every save wrote one global `turn_count` / `session_id` / `staged_stimulus` /
  `last_topic` / `last_keywords` — colleagues overwrote each other's turn count
  and could receive each other's "while-you-were-away" stimulus. Session state
  now persists to a **local per-user sidecar** (`graph_<context>.session.json`,
  next to the local graph dir, like `_cross_links.json`); only the shared,
  must-agree settings `embed_model` / `embed_dim` stay in the store's `meta`.
  Local single-writer stores are unchanged (session state stays in `meta`).
  One-time effect on an existing shared cloud DB: each user's `turn_count` reads
  its empty sidecar once and restarts from 0 (harmless).
- Regression tests in `test_fivefix.py::TestSessionSidecar` (meta split on the
  remote tier, sidecar round-trip, local behaviour unchanged).
- **Atomic save on the remote tier (P2).** `RemoteTursoConnection` now supports a
  real transaction: `begin()` buffers the write statements and `commit()` flushes
  them as ONE libSQL `batch()` (all-or-nothing), so a colleague loading mid-save
  can no longer observe a half-applied graph. Reads inside the transaction still
  execute immediately (reconcile's "which rows exist" SELECT). Schema/DDL runs
  outside the transaction, once per process. Local tier unchanged.
- **Reconcile guard on a shared store (P3).** A consolidate/merge triggers a
  diff-delete that can drop rows another colleague added since load. On a shared
  remote store this is now downgraded to an additive write (the merge's upserts
  still land, nobody else's rows are deleted) unless run as coordinated
  maintenance with `NS_ALLOW_SHARED_RECONCILE=1`.
- **One-shot cloud initializer (P4).** `scripts/init_cloud.py` +
  `Graph.ensure_schema()` migrate the shared cloud schema ONCE up front, so lazy
  per-client migration never races on a fresh database. Run it before colleagues
  connect.
- **Embed-model write guard + retry/backoff (P5).** A save into a shared store
  whose declared `embed_model` differs from the active one now skips vector
  writes (incompatible spaces) instead of poisoning the store, and doesn't
  clobber the store's model. Remote client creation and each `batch()` are
  wrapped in bounded exponential-backoff retries so a transient network blip
  no longer silently loses a turn. Tests: `TestRemoteTransaction`,
  `TestRetryAndModelGuard`, `TestSharedReconcileGuard`, `TestEnsureSchema`.

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
- **Embedding-model switcher** in
