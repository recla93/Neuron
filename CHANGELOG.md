# Changelog — Neuron

## v5.6.0 (2026-07-20)

### Gateway flip
- `gray-matter register --gateway` evicts neuron/neurag from all clients, registers only GM
- Singleton daemon via exclusive bind on :9876 (`SO_EXCLUSIVEADDRUSE`)
- Stdio handshake fixed: `InitializationOptions` now includes capabilities + GM instructions
- GM serves 32 tools via pass-through with real schemas (F12)

### Trust system (B1-B3)
- `Node.trust: float` column (REAL DEFAULT 0) with atomic delta `MAX(0, trust + ?)`
- `confirm(confidence)` tool: boosts trust, propagated in merge/dedup
- Trust integrated into ranking weights

### Refs table (G2)
- New `refs` table (context, keyword, path, project_id, by) with natural PK
- `store_turn` canonical refs + merge on revisited nodes + `files:` line in `pre_turn`

### Project system (G3)
- `project.py`: `.neuron/project.json` marker, relative paths, provenance tracking

### Installer unification
- Canonical install via `install.ps1` / `install.sh` delegating to GM
- `uninstall.sh` simplified
- INSTALL-AI.md (EN) + INSTALL-AI.it.md (IT) added

### Fixes
- F3: `reset` requires `confirm=true` (v5.4.2)
- F4: `prune` now has `dry_run` support
- F5: `dedup` toggle with explicit enable option
- F10: POSIX bashisms fixed in installer scripts
- G1: File refs canonicalized in `store_turn`

### Cleanup
- Removed `MINIMAX-BRAINSTORM.md` and `install-gui.sh`

## v5.5.0 (2026-07-18)

- Optional GM autoregister (opt-out `NEURON_NO_GM`)
- Forgotten `near=` mid-band serendipity selector (flash v2)

## v5.4.2 (2026-07-18)

- Reset requires confirm + dedup explicit enable
- POSIX sh launcher + macOS pipx shortcut
