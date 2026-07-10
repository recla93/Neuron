# Installing Neuron

Three ways to install, from easiest to most manual. If the automated installer
fails, the **Manual installation** and **Troubleshooting** sections below get
you running by hand.

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

## 1. Automated install (Windows)

**One-click (no terminal):** double-click **`Configuration.bat`** in the project
root. It opens an interactive menu (handles Windows' ExecutionPolicy for you)
that walks you through everything in order — install prerequisites → PyTurso →
full Neuron, add Neuron to your AI app, connect Turso Cloud / launch the bridge,
run tests, the Live Log Console, and a clean uninstall. This is the recommended
front door for most users.

For maintainers / source checkouts there is also:

- **`scripts\build-and-install.bat`** — the full local build chain: builds the
  `vendor\` pyturso wheel, builds the Neuron wheel, then runs the installer. Use
  this from a **source checkout** when nothing is built yet. (Builds pyturso for
  *your* Python only — it's a dev convenience, not a release builder; the full
  3.10–3.14 wheel set comes from `release.yml`.)

Or run the underlying installer directly from a terminal:

```powershell
.\install.ps1
```

What it does, in order:

1. Verifies Python is 3.10–3.14.
2. Creates a venv at `%LOCALAPPDATA%\Programs\neuron\.venv`.
3. `pip install`s the Neuron wheel, pointing pip at the **pre-built `pyturso`
   wheel** in `.\vendor` via `--find-links` — so **no C/Rust compiler is needed**.
4. **Only if that fails** (e.g. an unsupported Python version), it installs the
   *minimal* MSVC C++ build tools + Rust and compiles `pyturso` from source.
5. Registers the server with Claude Desktop and Cursor, and adds a Start Menu
   shortcut.

Useful flags:

- `-skipLlmProviders` — don't prompt for the optional standalone-chat LLM packages.
- `-ForceCompile` — skip the prebuilt wheel and compile `pyturso` from source
  (needs the toolchain; mainly for debugging the fallback path).

> **Why a vendored pyturso wheel?** PyPI ships `pyturso` wheels for macOS and
> Linux but **not** for Windows (`win_amd64`). Without the vendored wheel, a
> plain `pip install` on Windows compiles `pyturso` from Rust source, which
> needs Rust + MSVC + the Windows SDK. The release workflow builds that wheel
> once on CI so you don't have to.

### Building the `vendor\` wheels (maintainers)

`install.ps1` looks for pre-built `pyturso` wheels in a `vendor\` folder next to
it, so that end users never need a compiler. The layout is just a folder with
the wheel(s) in it:

```
Neuron-master\
├─ install.ps1
├─ vendor\
│   ├─ pyturso-0.6.1-cp310-cp310-win_amd64.whl
│   ├─ pyturso-0.6.1-cp311-cp311-win_amd64.whl
│   ├─ pyturso-0.6.1-cp312-cp312-win_amd64.whl
│   └─ pyturso-0.6.1-cp313-cp313-win_amd64.whl
└─ ...
```

To build a wheel for **your** Python version (needs Rust + MSVC installed):

```powershell
python -m pip wheel "pyturso==0.6.1" --no-deps --find-links vendor -w vendor
```

(`--find-links vendor` makes pip prefer an already-vendored wheel matching your Python's
ABI over compiling from source, if one happens to already be there — harmless either way,
but avoids an unnecessary compile when a wheel already exists.)

This drops `pyturso-0.6.1-cp<XY>-cp<XY>-win_amd64.whl` into `vendor\`, where
`<XY>` is your Python minor version (e.g. `cp313` for Python 3.13).

> **One wheel = one Python version.** A `cp313` wheel only installs on Python
> 3.13; a 3.12 user needs `cp312`, etc. For a real release you need the full set
> (3.10–3.14). You don't build those by hand — the `release.yml` GitHub workflow
> compiles all four on `windows-latest` runners (which already have the
> toolchain) and attaches them to the Release automatically. The single local
> build above is mainly for **testing the no-compile install path** on your own
> machine before publishing.

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

The seed knowledge DB (`neuron/data/base_knowledge.db`) ships **inside** the
wheel, so there is nothing extra to copy.

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

`install.ps1` auto-registers Claude Desktop and Cursor. For everything else, add
the launch command manually. The command is your venv's Python running
`-m neuron`.

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
A tagged release bundles prebuilt `vendor/` wheels for the full Python 3.10–3.14
range (`win_amd64`); a source checkout vendors only the maintainers' versions
(currently `cp313`/`cp314`). On any Python minor version without a matching wheel
— or a different architecture (e.g. ARM64) — pip falls back to compiling. Install
a supported CPython, or build the toolchain.

### `ModuleNotFoundError: No module named 'fastembed'` (or `mcp`)
The venv install didn't complete. Re-run inside the activated venv:
```bash
pip install mcp fastembed "pyturso==0.6.1"
```

### The seed knowledge DB is missing / `base_knowledge.db not found`
The wheel bundles it at `neuron/data/base_knowledge.db`. A fresh checkout ships
only a placeholder there; Neuron runs fine without a real seed (it just starts
with no pre-loaded knowledge and learns from your conversations).

To build a real seed from an Obsidian vault, use the import tool — it takes the
vault root from `NEURON_VAULT` (or `--vault`) and writes a local DB:

```bash
export NEURON_VAULT=/path/to/your/vault      # Windows: set NEURON_VAULT=C:\path\to\vault
python scripts/import_vault.py               # -> ./knowledge/base_knowledge.db
```

If `fastembed` is installed, 384-dim vectors are generated inline so semantic
search works immediately. The output DB is **local** — copy it to
`src/neuron/data/base_knowledge.db` only when you deliberately want it shipped as
the public seed.

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
See the README "Database engine" section.

---

## Uninstall

**Recommended — the tiered uninstaller** (you choose how much to remove; nothing
destructive by default, and it can preview first):

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
