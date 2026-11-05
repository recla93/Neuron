# 📥 Installing Neuron

> Part of **[Neuron](README.md)** — persistent semantic memory for AI. New here? Start with the
> [README](README.md); this file is the full install reference.

Several ways to install, from easiest to most manual. If the automated installer
fails, the **Manual installation** and **Troubleshooting** sections below get
you running by hand.

**TL;DR**

- **Windows** → run `install.ps1`, then use the **Gray Matter GUI** Desktop shortcut (the unified control center).
- **macOS / Linux** → `python3 -m venv .venv && source .venv/bin/activate && pip install ".[dev]"` → `python -m neuron`.
- **Register with a client** → `neuron register` (interactive, cross-platform) — or let the Windows installer do it.

Neuron is a local **stdio MCP server**: a client launches it as a subprocess
(`python -m neuron`). There is no daemon and no network port. "Installing" means
(1) getting the `neuron` package into a Python environment, and (2) registering
the launch command with your MCP client.

---

## Prerequisites

- **Python 3.10–3.14** on your `PATH` (`python --version`).
- **Either a working `pip`** (the default), **or [`uv`](https://astral.sh/uv)**. The
  Windows installer falls back to `uv` when the base Python's `python -m venv` produces
  a venv without a usable pip — `uv` creates the venv and installs with no pip at all.
  Install it (pip-free) with the one-liner:

  ```powershell
  irm https://astral.sh/uv/install.ps1 | iex
  ```

---

## 1. Automated install (unified — all platforms)

Two files, same behavior everywhere. Both are **thin launchers for the unified
Gray Matter installer** (`gray_matter/install.*`), because Gray Matter is the
brain that manages every tool:

```powershell
.\install.ps1          # Windows
```

```bash
sh install.sh          # macOS / Linux
```

What the unified installer does, in order:

1. Verifies Python 3.10–3.14 (prefers the `py` launcher on Windows).
2. **Mode selector** (interactive only, Enter = Full suite):
   - **Full suite** (default) — GM + Neuron + NeuRAG
   - **Solo Neuron** — standalone, registers directly in clients
   - **Details** — shows what you lose without GM, then asks again
3. Creates ONE shared venv (`%LOCALAPPDATA%\gray-matter\.venv` /
   `~/.local/share/gray-matter/.venv`).
4. Installs Gray Matter + every tool found next to it (this repo via
   `GM_PEER_DIR`; Neuron/NeuRAG siblings in a full-suite checkout). `pyturso`
   comes from the **pre-built wheels** in `vendor/` via `--find-links` — no
   C/Rust compiler needed.
5. Runs `gray-matter install`: registers ONLY the gateway in your MCP clients
   (`.bak` backup next to each config), deploys session hooks/plugins, writes
   the install manifest.
6. Creates the **Gray Matter GUI** desktop shortcut and opens the control
   center, where you manage tools, registration, tests and maintenance.

Opt-outs: `--no-gm` or `GM_OPTIN=0` skip the mode selector and install
standalone. `GM_NO_NEURON=1` / `GM_NO_NEURAG=1` skip a peer.

> **Why a vendored pyturso wheel?** PyPI ships `pyturso` wheels for macOS and
> Linux but **not** for Windows (`win_amd64`). Without the vendored wheel, a
> plain `pip install` on Windows compiles `pyturso` from Rust source, which
> needs Rust + MSVC + the Windows SDK. The release workflow builds that wheel
> once on CI so you don't have to.

### Control center (GUI)

The control center is now the **Gray Matter GUI** — one hub for Neuron, NeuRAG
and the gateway. The unified installer (`install.sh` / `install.ps1`, thin
launchers for `gray_matter/install.*`) creates a desktop shortcut
(**Gray Matter GUI**) and opens it at the end of the install. Any time after:
`gray-matter gui`.

The legacy Tkinter hub is still available for standalone debugging via the
`neuron-gui` entry point (`[project.gui-scripts]` in `pyproject.toml`), but it
is no longer installed as a shortcut.

### Building the `vendor\` wheels (maintainers)

`install.ps1` looks for pre-built `pyturso` wheels in a `vendor\` folder next to
it, so that end users never need a compiler. The layout is just a folder with
the wheel(s) in it:

```
Neuron-master\
├─ install.ps1
├─ vendor\
│   └─ pyturso-0.8.3-cp310-abi3-win_amd64.whl
└─ ...
```

> **One wheel for every Python 3.10–3.14.** Since 0.7 pyturso is built
> `abi3-py310` (PyO3 stable ABI), so the old per-version set (`cp310` …
> `cp314`) is gone.

To rebuild it (needs Rust + the Visual Studio **Build Tools** — C++ x64 tools and
the Windows SDK, ~3 GB; the full IDE is not needed):

```powershell
python -m pip wheel "pyturso==0.8.3" --no-deps --no-binary pyturso -w vendor
```

Any Python >= 3.10 produces the same abi3 wheel. The `build-pyturso-win` job of
the release workflow rebuilds it on every tag and attaches it to the Release.

When you bump `pyturso`, change the pin in **both** `pyproject.toml` and the
`build-pyturso-win` job in `.github/workflows/release.yml`, then rebuild.

> **Arm64 / WoA (Windows on Arm):** The `vendor\` folder only ships `win_amd64`
> wheels. On an Arm64 Windows device (Surface Pro X, etc.) pip must compile
> `pyturso` from source via x64 emulation, which needs the full Rust + MSVC
> toolchain. Prebuilt `win_arm64` wheels are not currently distributed — PRs
> welcome (add a `build-pyturso-win-arm64` job in `release.yml`).

> **Vendor scaling:** Each Python minor version × platform combo needs its own
> wheel (e.g. `cp313` + `win_amd64`, `cp312` + `win_arm64`, ...). The `vendor\`
> approach does not scale beyond the few combos the maintainers pre-build. For a
> broader platform matrix, push `pyturso` to PyPI with `win_arm64` wheels, or
> switch to an engine that distributes prebuilt wheels for every supported
> platform.

---

## 2. Install from a wheel by hand (any OS)

If you have `neuron-<version>-py3-none-any.whl` (and, on Windows, the
`pyturso-*-win_amd64.whl` files from the release):

```bash
# 1. Create and activate a venv
python -m venv .venv
# Windows:        .venv\Scripts\activate
# Linux/macOS:    source .venv/bin/activate

# 2a. Linux/macOS — pyturso has prebuilt wheels on PyPI, this just works:
pip install neuron-<version>-py3-none-any.whl

# 2b. Windows — point pip at the vendored pyturso wheels so it doesn't compile:
pip install --find-links .\vendor neuron-<version>-py3-none-any.whl

# 3. Verify
python -c "import neuron; print(neuron.__version__)"
python -m neuron        # starts the MCP server on stdio (Ctrl-C to stop)
```

There is nothing extra to copy: a new graph starts empty and fills with what you
store.

---

## 3. Install from source (developers)

```bash
git clone <repo> && cd neuron
python -m venv .venv
# activate it (see above)
pip install -e ".[dev]"                        # Linux/macOS: just works
pip install -e ".[dev]" --find-links vendor    # Windows: use the prebuilt pyturso wheel
python -m pytest tests/ -v
python -m neuron
```

On Windows a plain `pip install -e ".[dev]"` **will compile `pyturso`** from Rust
source — it stalls for minutes at *"Preparing metadata (pyproject.toml)"* and looks
frozen. Pass **`--find-links vendor`** (as above) so pip installs the prebuilt
`win_amd64` wheel instead; without it you need the full Rust + MSVC toolchain (see
Troubleshooting). On Windows the easiest path is `scripts\run_tests.ps1`, which does
this for you. On Linux/macOS it just works.

---

## Registering the MCP server with a client

### The easy way — `neuron register` (cross-platform)

Once the package is installed, the built-in registration CLI wires Neuron into every
supported client on **Windows, macOS and Linux** — non-destructive merge, a backup before
every change, and verify-after-write:

```bash
neuron register            # interactive: pick your client(s)
neuron register --all      # register everywhere it can find
neuron doctor              # check what's registered / spot problems
neuron doctor --fix        # stop orphaned servers, clean duplicates
```

The unified installer already registers the **gateway** for you in every
detected client (`gray-matter install`). Use `neuron register` only for a
STANDALONE Neuron (no Gray Matter) — the two models are alternatives, not
additive: the gateway evicts standalone `neuron5`/`neurag` entries by design.

### By hand

The launch command is just your venv's Python running `-m neuron`.

**Claude Desktop** — `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "neuron": {
      "command": "C:\\Users\\<you>\\AppData\\Local\\Programs\\neuron\\.venv\\Scripts\\python.exe",
      "args": ["-m", "neuron"]
    }
  }
}
```

**Cursor** — `~/.cursor/mcp.json`: same `mcpServers` shape as above.

**OpenCode** — `~/.config/opencode/opencode.json`: use the `mcp` block shown in
the README. Restart the client after editing its config.

Ready-made snippets for Claude Code, Cline/Roocode, VS Code, Windsurf, Zed,
Continue.dev, Cody, Amazon Q, Perplexity and ChatGPT are in the `clients/`
folder and in [docs/DEVELOPER.md](docs/DEVELOPER.md#mcp-client-configuration).

---

## Troubleshooting

### `pip` tries to compile pyturso / "error: Microsoft Visual C++ ... required"
You're on Windows without a matching prebuilt wheel. Either:
- pass `--find-links <vendor>` pointing at the `pyturso-*-win_amd64.whl` from the
  release (preferred), **or**
- install the minimal toolchain and let it compile:
  ```powershell
  # Rust
  winget install Rustlang.Rustup    # or download rustup-init.exe from https://win.rustup.rs
  # Minimal MSVC C++ build tools (NOT the full Visual Studio):
  # download vs_BuildTools.exe from https://aka.ms/vs/17/release/vs_BuildTools.exe then:
  .\vs_BuildTools.exe --quiet --wait --norestart `
    --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
    --add Microsoft.VisualStudio.Component.Windows11SDK.22621
  ```
  Open a **new** terminal afterwards so `cargo`/`cl` are on `PATH`, then retry.

### `pyturso` wheel doesn't match my Python
`vendor/` holds one abi3 wheel (`win_amd64`) that installs on any CPython
3.10–3.14. Below 3.10, or on another architecture (e.g. ARM64), there is no
matching wheel and pip falls back to compiling. Install a supported CPython, or
install the Build Tools.

### `ModuleNotFoundError: No module named 'fastembed'` (or `mcp`)
The venv install didn't complete. Re-run inside the activated venv:
```bash
pip install --find-links vendor mcp fastembed "pyturso==0.8.3"
```

### The client doesn't see Neuron after install
Restart the client (Claude Desktop/Cursor/etc.) — MCP servers are read at
startup. Verify the command works standalone first:
```bash
"<venv>/Scripts/python.exe" -m neuron     # should start and wait on stdio
```

### Cloud (cross-machine) memory
By default everything is a single local `.db`. For Turso cloud:
```bash
pip install "neuron[cloud]"
set TURSO_DATABASE_URL=libsql://your-db.turso.io
set TURSO_AUTH_TOKEN=...
```
See the README "Storage" section.

---

## Uninstall

**Recommended — the unified path** (interactive on the memory, preview first):

```bash
gray-matter uninstall --dry-run   # preview: reap → deregister → hooks → code → data prompts
gray-matter uninstall             # asks BEFORE touching graphs/knowledge/bridges
sh uninstall.sh                   # same thing (thin launcher, macOS/Linux)
```

**Legacy/deep cleanup — the tiered Windows uninstaller** (for old `neuron`-slug
installs and leftovers the manifest doesn't track):

```powershell
# app only: install dir + venv/deps, Start-Menu, MCP de-registration from all 6 clients
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1

# preview a FULL wipe without changing anything
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -All -DryRun

# full wipe: app + memory data + .env secrets + model cache
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -All
```

Flags: `-Data` (memory store), `-Secrets` (scrub `.env`), `-Cache` (fastembed model
cache), `-All` (= all three), `-DryRun`, `-Yes` (no prompts). For a v4 install pass
`-Slug neuron` (or set `NEURON_SLUG`).

**Everything an install can leave behind** (so you can remove it all yourself if you
prefer): install dir + venv `%LOCALAPPDATA%\Programs\neuron5`; **memory store
`%LOCALAPPDATA%\neuron5\graphs`** (separate from the install so reinstalls don't wipe
it — the classic thing manual uninstalls miss); repo `graphs\*.db`; `.env` (Turso
token + API keys, in cleartext); the **fastembed/HuggingFace model cache** (~80MB,
under `%LOCALAPPDATA%\Temp\fastembed_cache` and `~\.cache\huggingface`); the Start-Menu
shortcut; the `neuron5` entry in each of the 6 client configs (Claude Desktop, Claude
Code, Cursor, VS Code, OpenCode, Zed). On-demand fallback tools (Rust, MSVC Build Tools,
`uv`, `cloudflared`) and your Turso **cloud** DB are never removed automatically.
