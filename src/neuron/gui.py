"""`neuron gui` — Tkinter visual hub with dark theme.

Windowed entry point (see ``[project.gui-scripts]`` in pyproject) so a
double-clickable ``neuron-gui``/``neuron-gui.exe`` opens this instead of
a terminal.  Buttons run safe operations in the panel; stdin-driven tools
open a readable terminal only when they genuinely need interactive input.

Layout: sidebar (collapsible sections) + streaming output + status bar.
Tkinter is stdlib, so this adds no dependency.  Falls back to
``neuron manage`` on headless boxes.
"""
from __future__ import annotations

import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import tkinter as tk

__all__ = ["main"]

# Public URL of a Cloudflare quick tunnel (printed by cloudflared / neuron tunnel).
_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# Subcommands the command console accepts (mirrors neuron.__main__ dispatch).
_KNOWN_SUBCOMMANDS = {
    "register", "doctor", "setup", "manage", "consolidate", "bridge",
    "connect", "console", "tunnel", "init",
}


# ---------------------------------------------------------------------------
# Colour palette — single source of truth
# ---------------------------------------------------------------------------

_BG = "#1a1b26"
_SURFACE = "#24283b"
_HOVER = "#414868"
_ACCENT = "#7aa2f7"
_FG = "#c0caf5"
_DIM = "#565f89"
_OUT_BG = "#16161e"
_RED = "#f7768e"
_GREEN = "#9ece6a"
_YELLOW = "#e0af68"
_SEP = "#32344a"
_CARD = "#20243a"
_CARD_EDGE = "#303653"
_FONT = "Segoe UI"


def _load_logo(master: tk.Misc, max_size: int = 46) -> tk.PhotoImage | None:
    """Load the bundled identity mark without adding an image dependency."""
    try:
        from pathlib import Path
        source = Path(__file__).with_name("data") / "neuron-logo.png"
        if not source.exists():
            return None
        image = tk.PhotoImage(master=master, file=str(source))
        factor = max(1, max(image.width(), image.height()) // max_size)
        return image.subsample(factor, factor) if factor > 1 else image
    except Exception:
        # The logo is cosmetic — NO failure here may stop the GUI from opening
        # (a corrupt PNG raised TypeError, not TclError, and killed startup).
        return None


def _cfg(widget: tk.Widget, **kw: str) -> None:
    """Set widget config, suppress errors on unsupported platforms."""
    try:
        widget.configure(**kw)
    except tk.TclError:
        pass


def _make(
    parent: tk.Widget,
    cls: type[tk.Widget],
    *,
    bg: str = _BG,
    fg: str | None = None,
    **kw: object,
) -> tk.Widget:
    """Create a themed widget — one place to change defaults."""
    opts: dict[str, object] = {"bg": bg, **kw}
    if fg is not None:
        opts["fg"] = fg
    return cls(parent, **opts)


# ---------------------------------------------------------------------------
# Tooltip — hover description for sidebar buttons
# ---------------------------------------------------------------------------

class _Tooltip:
    """Show a small popup on hover with the command description."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget = widget
        self._text = text
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event: tk.Event) -> None:
        if self._tip or not self._text:
            return
        x = self._widget.winfo_rootx() + 12
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.configure(bg=_HOVER)
        lbl = tk.Label(tw, text=self._text, bg=_HOVER, fg=_FG,
                       font=("Segoe UI", 8), padx=6, pady=3, wraplength=240,
                       justify="left")
        lbl.pack()

    def _hide(self, _event: tk.Event) -> None:
        if self._tip:
            self._tip.destroy()
            self._tip = None


# ---------------------------------------------------------------------------
# Command registry — single source of truth for sidebar content
# ---------------------------------------------------------------------------

# Entries are (label, args, description).  args=None marks a special action
# dispatched by name in _build_sidebar (wizard, network start/stop, terminal).
_COMMANDS: dict[str, list[tuple[str, list[str] | None, str]]] = {
    "Setup": [
        ("Install Wizard", None,
         "Guided step-by-step setup: checks, client selection, install"),
        ("Register", ["register", "--client", "all"],
         "Register Neuron in all detected AI clients"),
        ("Doctor", ["doctor"],
         "Diagnose client registrations and running servers"),
        ("Repair", ["setup", "--repair"],
         "Doctor with automatic fixes applied"),
        ("Status", ["setup", "--status"],
         "Show the active installation and registered clients"),
        ("Uninstall Neuron", None,
         "Remove the installation while keeping local memory data"),
        ("Uninstall + memory", None,
         "Remove Neuron and permanently delete local memory data"),
        ("Deploy Update", None,
         "Sync this source tree into the active install (deploy.ps1) — "
         "use after pulling/patching the repo"),
    ],
    "Manage": [
        ("Overview", ["manage", "--overview"],
         "Show graph stats: nodes, links, episodes, health"),
        ("Status", ["setup", "--status"],
         "Check install status and version"),
        ("Export", ["manage", "--export", "graph-export.json"],
         "Export graph to JSON file"),
        ("Consolidate", ["manage", "--consolidate"],
         "Merge duplicate nodes, archive low-salience orphans"),
        ("Repair Links", ["manage", "--repair-links"],
         "Remove dangling links (source/target not in nodes table)"),
        ("Visualize", ["manage", "--visualize"],
         "Generate interactive HTML graph visualizer"),
    ],
    "Tools": [
        ("Console", None,
         "Live graph console (opens in a terminal — interactive)"),
        ("Import Vault", None,
         "Import an Obsidian/markdown vault into the knowledge DB"),
        ("Prune", ["manage", "--consolidate"],
         "Consolidate + prune expired tangential links"),
        ("Run Tests", None,
         "Run the pytest suite; offers Repair if anything fails"),
    ],
    "Model": [
        ("Multilingual", None,
         "Embedding model for EN+IT (~380 MB): "
         "paraphrase-multilingual-MiniLM-L12-v2 — the default"),
        ("English-only", None,
         "Lightweight English embedding model (~90 MB): all-MiniLM-L6-v2"),
        ("Re-embed Store", None,
         "Regenerate vectors of every graph with the active model "
         "(needed after a model switch)"),
    ],
    "Turso": [
        ("Check Cloud", ["connect", "--check-only"],
         "Check Turso Cloud DB connection readiness"),
        ("Check Config", None,
         "Offline check: Turso env vars, schema, .env file"),
        ("Connect", None,
         "Configure Turso Cloud credentials (opens in a terminal)"),
        ("Init Cloud", None,
         "Initialize Turso Cloud schema (one-shot, for shared DB)"),
        ("Switch to Local", ["connect", "--use-local"],
         "Use the local DB — comments out the Turso creds in .env. "
         "Restart the server to apply."),
        ("Switch to Cloud", ["connect", "--use-cloud"],
         "Use Turso Cloud — re-enables the saved credentials. "
         "Restart the server to apply."),
    ],
    "Network": [
        ("Start Network", None,
         "Bridge (HTTP) + Tunnel (cloudflared): checks dependencies, starts "
         "the Bridge, waits until it's ready, then opens the Tunnel"),
        ("Stop Network", None,
         "Stop Bridge + Tunnel (and their watchdog)"),
    ],
}

# Special sidebar actions that open an interactive terminal instead of
# streaming in-pane (the underlying subcommand reads stdin).
_TERMINAL_ACTIONS: dict[str, list[str]] = {
    "Console": ["console"],
}


# ---------------------------------------------------------------------------
# Collapsible section (sidebar)
# ---------------------------------------------------------------------------

class _Section:
    """A collapsible group in the sidebar: header + hidden child frame."""

    def __init__(self, parent: tk.Frame, title: str) -> None:
        self._open = True
        self._arrow_label: tk.Label | None = None

        self.header = _make(parent, tk.Frame, bg=_BG)
        self.header.pack(fill="x", padx=6, pady=(8, 2))

        self.arrow = _make(self.header, tk.Label, bg=_BG, fg=_ACCENT,
                           text="▾", font=("Consolas", 9), width=2)
        self.arrow.pack(side="left")

        self.title = _make(self.header, tk.Label, bg=_BG, fg=_DIM,
                           text=title.upper(), font=("Segoe UI", 9, "bold"))
        self.title.pack(side="left")

        self.child = _make(parent, tk.Frame, bg=_BG)
        self.child.pack(fill="x")

        for w in (self.header, self.arrow, self.title):
            w.bind("<Button-1>", lambda _e: self.toggle())

    def toggle(self) -> None:
        self._open = not self._open
        if self._open:
            # Re-pack right below OUR header — a bare pack() appends to the END
            # of the parent, which dumped reopened buttons after the Stop button.
            self.child.pack(fill="x", after=self.header)
            self.arrow.configure(text="▾")
        else:
            self.child.pack_forget()
            self.arrow.configure(text="▸")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class _App:
    """Main GUI window — owns the event loop and all widgets."""

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._queues: dict[str, queue.Queue[str]] = {}
        self._history: list[str] = []
        # T74/T75 — control-center state: which bg processes to keep alive
        # (watchdog restarts them), their launch args, restart counters and
        # the command-console history.
        self._keepalive: set[str] = set()
        self._bg_args: dict[str, list[str]] = {}
        self._restarts: dict[str, int] = {}
        self._cmd_history: list[str] = []
        self._hist_idx: int = 0
        # T81 — network log state: "off" → "starting" (full trace) → "quiet"
        # (summary shown, routine INF lines suppressed). Any failure flips back
        # to verbose so the full trace is visible exactly when it matters.
        self._net_state: str = "off"
        self._net_port: int = 8000
        self._bridge_via: str = ""
        self._stopping: set[str] = set()
        # T82 — optional callback invoked with the exit code when the current
        # foreground command finishes (used by Run Tests → offer Repair).
        self._fg_on_done = None
        self._cloud_btn: tk.Button | None = None

        root.title("Neuron · Control Center")
        root.minsize(900, 600)
        root.geometry("1080x700")
        root.configure(bg=_BG)
        _cfg(root, bg=_BG)

        self._apply_theme()
        self._logo_image = _load_logo(root, 52)
        if self._logo_image is not None:
            try:
                root.iconphoto(True, self._logo_image)
            except tk.TclError:
                pass
        self._build_header()
        self._build_body()
        self._build_statusbar()
        self._refresh_context()
        # ONE queue-poll loop for the whole app, started at boot (T80). It used
        # to be scheduled only by foreground commands, so pressing Start Network
        # first meant Bridge/Tunnel output NEVER reached the pane — and every
        # extra schedule spawned a duplicate self-perpetuating loop.
        self._root.after(80, self._poll_queue)
        # Closing the window terminates the background stack (as the Network
        # log promises) — otherwise Bridge/Tunnel would linger orphaned.
        try:
            root.protocol("WM_DELETE_WINDOW", self._on_close)
        except Exception:
            pass

    def _on_close(self) -> None:
        for name, proc in list(self._procs.items()):
            if proc.poll() is None:
                try:
                    if os.name == "nt":
                        subprocess.call(
                            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            creationflags=0x08000000)
                    else:
                        proc.terminate()
                except Exception:
                    pass
        self._root.destroy()

    # -- theme ---------------------------------------------------------------

    def _apply_theme(self) -> None:
        try:
            self._root.tk.call("ttk::style", "theme", "use", "clam")
        except tk.TclError:
            pass
        try:
            from tkinter import ttk
            s = ttk.Style(self._root)
            s.theme_use("clam")
            s.configure(".", background=_BG, foreground=_FG, fieldbackground=_BG)
            s.configure("TButton", background=_HOVER, foreground=_FG,
                        borderwidth=0, padding=5)
            s.map("TButton",
                  background=[("active", _ACCENT), ("disabled", _BG)],
                  foreground=[("disabled", _DIM)])
            s.configure("Neuron.Horizontal.TProgressbar",
                        background=_ACCENT, troughcolor=_SURFACE,
                        borderwidth=0, lightcolor=_ACCENT, darkcolor=_ACCENT)
        except Exception:
            pass

    # -- header --------------------------------------------------------------

    def _build_header(self) -> None:
        hdr = _make(self._root, tk.Frame, bg=_CARD,
                    highlightbackground=_CARD_EDGE, highlightthickness=1)
        hdr.pack(fill="x", padx=14, pady=(14, 8), ipady=8)

        if self._logo_image is not None:
            _make(hdr, tk.Label, bg=_CARD, image=self._logo_image).pack(
                side="left", padx=(14, 10))
        title = _make(hdr, tk.Label, bg=_CARD, fg=_FG,
                      text="Neuron", font=(_FONT, 21, "bold"))
        title.pack(side="left")

        sub = _make(hdr, tk.Label, bg=_CARD, fg=_DIM,
                    text="  CONTROL CENTER  ·  persistent memory for your AI",
                    font=(_FONT, 9, "bold"))
        sub.pack(side="left", padx=(10, 0), pady=(7, 0))

        self._ver_lbl = _make(hdr, tk.Label, bg=_BG, fg=_DIM,
                              text="", font=("Consolas", 9))
        self._ver_lbl.pack(side="right", padx=14, pady=(8, 0))
        self._show_version()

    # -- body (sidebar + output) ---------------------------------------------

    def _build_body(self) -> None:
        body = _make(self._root, tk.Frame, bg=_BG)
        body.pack(fill="both", expand=True, padx=14, pady=2)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_sidebar(body)
        self._build_output(body)

    def _build_sidebar(self, parent: tk.Frame) -> None:
        wrap = _make(parent, tk.Frame, bg=_CARD,
                     highlightbackground=_CARD_EDGE, highlightthickness=1,
                     width=190)
        wrap.grid(row=0, column=0, sticky="ns", padx=(0, 6))
        wrap.grid_propagate(False)

        canvas = tk.Canvas(wrap, bg=_CARD, highlightthickness=0, width=180)
        vsb = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = _make(canvas, tk.Frame, bg=_CARD)

        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)

        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def _on_mousewheel(event: tk.Event) -> None:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        special: dict[str, object] = {
            "Install Wizard": self._open_wizard,
            "Import Vault": self._import_vault,
            "Deploy Update": self._deploy_update,
            "Uninstall Neuron": lambda: self._uninstall(False),
            "Uninstall + memory": lambda: self._uninstall(True),
            "Start Network": self._cloud_start,
            "Stop Network": self._cloud_stop,
            "Connect": self._open_turso_dialog,
            "Check Config": self._check_cloud_config,
            "Init Cloud": self._init_cloud,
            "Run Tests": self._run_tests,
            "Multilingual": lambda: self._set_embed_model(
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                "Multilingual EN+IT (~380 MB)"),
            "English-only": lambda: self._set_embed_model(
                "sentence-transformers/all-MiniLM-L6-v2",
                "English-only (~90 MB)"),
            "Re-embed Store": self._reembed_store,
        }
        for name, targs in _TERMINAL_ACTIONS.items():
            special[name] = lambda a=targs, l=name: self._run_terminal(a, display=l)

        for section, items in _COMMANDS.items():
            sec = _Section(inner, section)
            turso_ok = self._turso_configured()
            for label, args, desc in items:
                if args is None:
                    handler = special.get(label, self._cloud_stop)
                else:
                    handler = lambda a=args, l=label: self._run(a, display=l)
                # 'Switch to Cloud' is only usable once credentials are saved;
                # disable it (with an explanatory hover) until then.
                tip = desc
                disabled = False
                if label == "Switch to Cloud" and not turso_ok:
                    disabled = True
                    tip = ("Disabilitato: credenziali Turso non configurate — "
                           "usa prima 'Connect' per salvarle.")
                btn = _make(sec.child, tk.Button, bg=_HOVER, fg=_FG,
                            text=label, font=("Segoe UI", 9),
                            activebackground=_ACCENT, activeforeground=_BG,
                            relief="flat", anchor="w", padx=10, pady=2,
                            command=handler,
                            state=("disabled" if disabled else "normal"),
                            disabledforeground=_DIM)
                btn.pack(fill="x", padx=4, pady=1)
                if label == "Switch to Cloud":
                    self._cloud_btn = btn
                if not disabled:
                    btn.bind("<Enter>",
                             lambda _e, b=btn: b.configure(bg=_ACCENT))
                    btn.bind("<Leave>",
                             lambda _e, b=btn: b.configure(bg=_HOVER))
                _Tooltip(btn, tip)

        sep = _make(inner, tk.Frame, bg=_SEP, height=1)
        sep.pack(fill="x", padx=6, pady=8)

        stop = _make(inner, tk.Button, bg=_RED, fg="#1a1b26",
                     text="■  Stop", font=("Segoe UI", 9, "bold"),
                     activebackground="#d86078", relief="flat",
                     command=self._stop)
        stop.pack(fill="x", padx=8, pady=(0, 8))
        stop.bind("<Enter>",
                  lambda _e: stop.configure(bg="#d86078"))
        stop.bind("<Leave>",
                  lambda _e: stop.configure(bg=_RED))
        _Tooltip(stop, "Terminate the running command")

    def _build_output(self, parent: tk.Frame) -> None:
        out_frame = _make(parent, tk.Frame, bg=_OUT_BG)
        out_frame.grid(row=0, column=1, sticky="nsew")
        out_frame.columnconfigure(0, weight=1)
        out_frame.rowconfigure(0, weight=1)

        self._out = tk.Text(
            out_frame, bg=_OUT_BG, fg=_FG, insertbackground=_FG,
            font=("Consolas", 10), wrap="word", relief="flat",
            borderwidth=0, highlightthickness=0, padx=10, pady=8,
            selectbackground=_ACCENT, selectforeground="#1a1b26",
        )
        self._out.grid(row=0, column=0, sticky="nsew")

        sb = tk.Scrollbar(out_frame, command=self._out.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._out.configure(yscrollcommand=sb.set)

        self._out.tag_configure("cmd", foreground=_ACCENT)
        self._out.tag_configure("err", foreground=_RED)
        self._out.tag_configure("ok", foreground=_GREEN)
        self._out.tag_configure("dim", foreground=_DIM)

        self._out.bind("<Control-l>", lambda _e: self._clear_output())
        self._out.bind("<Control-c>",
                       lambda _e: self._out.event_generate("<<Copy>>"))

        # -- command console (T74): type any `neuron …` subcommand, Enter runs it.
        cmd_bar = _make(out_frame, tk.Frame, bg=_SURFACE)
        cmd_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        _make(cmd_bar, tk.Label, bg=_SURFACE, fg=_ACCENT, text=" neuron ▸",
              font=("Consolas", 10, "bold")).pack(side="left", padx=(8, 2))
        self._cmd_entry = tk.Entry(
            cmd_bar, bg=_SURFACE, fg=_FG, insertbackground=_FG,
            relief="flat", font=("Consolas", 10),
            highlightthickness=0)
        self._cmd_entry.pack(side="left", fill="x", expand=True, padx=(0, 4),
                             pady=5, ipady=2)
        self._cmd_entry.bind("<Return>", lambda _e: self._run_command_line())
        self._cmd_entry.bind("<Up>", lambda _e: self._history_nav(-1))
        self._cmd_entry.bind("<Down>", lambda _e: self._history_nav(+1))
        run_btn = _make(cmd_bar, tk.Button, bg=_HOVER, fg=_FG, text="Run",
                        font=("Segoe UI", 8, "bold"), relief="flat",
                        activebackground=_ACCENT, padx=10,
                        command=self._run_command_line)
        run_btn.pack(side="right", padx=(0, 6), pady=4)
        _Tooltip(self._cmd_entry,
                 "Any neuron subcommand: manage --overview, consolidate, "
                 "setup --status, doctor … (Up/Down = history)")

        self._clear_output()

    # -- status bar ----------------------------------------------------------

    def _build_statusbar(self) -> None:
        _make(self._root, tk.Frame, bg=_SEP, height=1).pack(fill="x")

        bar = _make(self._root, tk.Frame, bg=_BG)
        bar.pack(fill="x", padx=10, pady=(2, 6))

        self._status_dot = _make(bar, tk.Label, bg=_BG, fg=_ACCENT,
                                 text="●", font=("Consolas", 10))
        self._status_dot.pack(side="left")

        self._status_lbl = _make(bar, tk.Label, bg=_BG, fg=_FG,
                                 text=" Ready", font=("Segoe UI", 9))
        self._status_lbl.pack(side="left")

        self._status_right = _make(bar, tk.Frame, bg=_BG)
        self._status_right.pack(side="right")

        self._ctx_lbl = _make(self._status_right, tk.Label, bg=_BG, fg=_DIM,
                              text="Context: —", font=("Consolas", 9))
        self._ctx_lbl.pack(side="right", padx=(12, 0))

        # Tunnel URL (T75): filled when cloudflared prints its public URL;
        # click to copy the /mcp connector URL to the clipboard.
        self._tunnel_lbl = _make(self._status_right, tk.Label, bg=_BG, fg=_GREEN,
                                 text="", font=("Consolas", 9), cursor="hand2")
        self._tunnel_lbl.pack(side="right", padx=(12, 0))
        self._tunnel_lbl.bind("<Button-1>", lambda _e: self._copy_tunnel_url())
        self._tunnel_url: str = ""

    def _copy_tunnel_url(self) -> None:
        if not self._tunnel_url:
            return
        try:
            self._root.clipboard_clear()
            self._root.clipboard_append(self._tunnel_url + "/mcp")
            self._write(f"\n[tunnel] copied to clipboard: {self._tunnel_url}/mcp\n",
                        tag="ok")
        except Exception:
            pass

    def _set_status(self, state: str, text: str) -> None:
        colour_map = {"ready": _ACCENT, "running": _YELLOW,
                      "error": _RED, "stopped": _RED}
        _cfg(self._status_dot, fg=colour_map.get(state, _DIM))
        _cfg(self._status_lbl, text=f" {text}")

    def _refresh_context(self) -> None:
        result: list[str] = []

        def _fetch() -> None:
            text = "Context: default"
            try:
                r = subprocess.run(
                    [sys.executable or "python", "-m", "neuron", "status"],
                    capture_output=True, text=True, timeout=10,
                )
                for line in (r.stdout or "").splitlines():
                    if "context" in line.lower():
                        text = line.strip()
                        break
            except Exception:
                pass
            result.append(text)

        t = threading.Thread(target=_fetch, daemon=True)

        def _apply() -> None:
            if not result:
                self._root.after(150, _apply)
                return
            _cfg(self._ctx_lbl, text=result[0])
        t.start()
        self._root.after(150, _apply)

    def _show_version(self) -> None:
        try:
            from neuron import __version__
            _cfg(self._ver_lbl, text=f"v{__version__}")
        except ImportError:
            _cfg(self._ver_lbl, text="dev")

    # -- output pane ---------------------------------------------------------

    def _clear_output(self) -> None:
        self._out.delete("1.0", "end")
        self._write(
            "Pick an action from the sidebar, or type a command below.\n"
            "Read-only commands show output here.\n"
            "Ctrl+L to clear, Ctrl+C to copy.\n",
            tag="dim",
        )

    def _write(self, text: str, *, tag: str | None = None) -> None:
        self._out.insert("end", text, tag or ())
        self._out.see("end")

    # -- command execution ---------------------------------------------------

    def _start_bg(self, name: str, args: list[str]) -> None:
        """Start a named background process (Bridge, Tunnel)."""
        if name in self._procs and self._procs[name].poll() is None:
            self._write(f"[{name}] already running.\n", tag="dim")
            return
        cmd = [sys.executable or "python", "-m", "neuron", *args]
        q: queue.Queue[str] = queue.Queue()
        self._queues[name] = q
        self._bg_args[name] = list(args)

        def _target() -> None:
            try:
                creation_flags = 0
                if os.name == "nt":
                    creation_flags = 0x08000000  # CREATE_NO_WINDOW
                env = os.environ.copy()
                env["PYTHONUNBUFFERED"] = "1"
                env["PYTHONUTF8"] = "1"
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, encoding="utf-8", errors="replace",
                    creationflags=creation_flags, env=env,
                )
                self._procs[name] = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    q.put(line)
                proc.wait()
            except Exception as exc:
                q.put(f"[{name}] {exc}\n")
            finally:
                q.put(f"__DONE__{name}")

        threading.Thread(target=_target, daemon=True).start()
        self._write(f"[{name}] started.\n", tag="ok")
        self._update_status()

    def _run(self, args: list[str], *, display: str | None = None) -> None:
        """Run a foreground command (streams to the output pane).

        Background processes (Bridge/Tunnel) keep running — the control center
        stays usable while the network stack is up (T74). Only one foreground
        command at a time.
        """
        fg = self._procs.get("__fg__")
        if fg is not None and fg.poll() is None:
            self._write("\n[!] a command is already running — wait for it "
                        "or press Stop.\n", tag="err")
            return

        cmd_str = "$ neuron " + " ".join(args)
        self._clear_output()
        self._write(f"{cmd_str}\n\n", tag="cmd")
        self._set_status("running", display or args[0])

        cmd = [sys.executable or "python", "-m", "neuron", *args]
        q: queue.Queue[str] = queue.Queue()
        self._queues["__fg__"] = q

        def _target() -> None:
            try:
                creation_flags = 0
                if os.name == "nt":
                    creation_flags = 0x08000000  # CREATE_NO_WINDOW
                env = os.environ.copy()
                env["PYTHONUNBUFFERED"] = "1"
                env["PYTHONUTF8"] = "1"
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, encoding="utf-8", errors="replace",
                    creationflags=creation_flags, env=env,
                )
                self._procs["__fg__"] = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    q.put(line)
                proc.wait()
            except Exception as exc:
                q.put(f"[gui] {exc}\n")
            finally:
                q.put("__DONE____fg__")

        threading.Thread(target=_target, daemon=True).start()
        # (the single app-wide poll loop, started in __init__, drains the queue)

    def _poll_queue(self) -> None:
        """Route messages from all queues to the output pane."""
        for name, q in list(self._queues.items()):
            try:
                while True:
                    line = q.get_nowait()
                    if line.startswith("__DONE__"):
                        finished = line.replace("__DONE__", "")
                        self._on_process_done(finished)
                        continue
                    if name == "__fg__":
                        self._write(line)
                    else:
                        self._route_bg_line(name, line)
            except queue.Empty:
                pass
        self._root.after(80, self._poll_queue)

    def _route_bg_line(self, name: str, line: str) -> None:
        """Show a background-process line, with success-summary handling (T81).

        While the network is "starting" every line streams (full trace). The
        moment the tunnel URL appears, the trace is CLEARED and replaced by a
        compact summary; from then on routine INF chatter is suppressed and
        only warnings/errors/watchdog events come through. Any failure flips
        back to verbose, so the full trace is there exactly when needed.
        """
        # Remember how the bridge is being served (for the summary line).
        if name == "Bridge" and "Starting bridge via:" in line:
            self._bridge_via = line.split("Starting bridge via:", 1)[1].strip()

        m = _TUNNEL_URL_RE.search(line)
        new_url = bool(m and m.group(0) != self._tunnel_url)
        if new_url:
            self._tunnel_url = m.group(0)
            short = self._tunnel_url.replace("https://", "")
            _cfg(self._tunnel_lbl, text=f"⛅ {short}/mcp (click = copy)")

        if self._net_state == "quiet" and name in ("Bridge", "Tunnel"):
            if new_url:   # watchdog reopened the tunnel → fresh URL matters
                self._write(f"[{name}] new connector URL: "
                            f"{self._tunnel_url}/mcp\n", tag="ok")
                return
            lowered = line.lower()
            noisy = (" inf " in lowered or lowered.startswith("20")
                     or not line.strip())
            if noisy and not any(k in lowered for k in
                                 ("err", "warn", "fail", "watchdog",
                                  "exited", "stopped", "reopening")):
                return                       # suppress routine chatter
            self._write(f"[{name}] {line}")
            return

        self._write(f"[{name}] {line}")
        if new_url and self._net_state == "starting":
            self._show_network_summary()
        elif new_url:
            self._write(f"[{name}] connector URL: "
                        f"{self._tunnel_url}/mcp\n", tag="ok")

    def _show_network_summary(self) -> None:
        """Success: clear the trace, show only what the user needs (T81)."""
        self._net_state = "quiet"
        self._out.delete("1.0", "end")
        self._write("NETWORK UP\n\n", tag="ok")
        self._write("  ✓ Neuron server   alive (preflight OK)\n", tag="ok")
        via = f"   (via {self._bridge_via})" if self._bridge_via else ""
        self._write(f"  ✓ Bridge          http://127.0.0.1:{self._net_port}"
                    f"/mcp{via}\n", tag="ok")
        self._write(f"  ✓ Tunnel          {self._tunnel_url}/mcp\n\n", tag="ok")
        self._write("  MCP connector URL (Streamable HTTP, not /sse):\n", tag="dim")
        self._write(f"    {self._tunnel_url}/mcp\n\n", tag="cmd")
        self._write("  Click the ⛅ link in the status bar to copy it.\n"
                    "  Watchdog ON — if the tunnel drops it reopens (the URL "
                    "changes: update the connector).\n"
                    "  Routine log lines are hidden now; anything unusual "
                    "will show up here.\n"
                    "  Stop Network shuts both down; closing this window "
                    "does too.\n", tag="dim")
        self._set_status("running", "Network: UP")

    def _on_process_done(self, name: str) -> None:
        proc = self._procs.pop(name, None)
        self._queues.pop(name, None)
        intentional = name in self._stopping
        self._stopping.discard(name)
        if proc:
            rc = proc.returncode
            if rc and rc != 0 and not intentional:
                if self._net_state == "quiet":
                    self._net_state = "starting"   # verbose again on failure
                self._write(f"[{name}] exited with code {rc}.\n", tag="err")
            else:
                self._write(f"[{name}] stopped.\n", tag="dim")
        if name == "__fg__":
            self._set_status("ready", "Done")
            cb, self._fg_on_done = self._fg_on_done, None
            if cb is not None and proc is not None:
                try:
                    cb(proc.returncode or 0)
                except Exception as exc:
                    self._write(f"\n[!] post-command hook failed: {exc}\n",
                                tag="err")
        elif name in self._keepalive:
            # T75 — watchdog: the process died but the user wants it up.
            # Restart with growing delay (rapid-crash loops back off to 60s).
            n = self._restarts.get(name, 0) + 1
            self._restarts[name] = n
            delay_ms = min(2000 * (2 ** min(n - 1, 5)), 60000)
            self._write(f"[{name}] watchdog: restarting in "
                        f"{delay_ms // 1000}s (restart #{n})…\n", tag="dim")

            def _revive(nm: str = name) -> None:
                if nm in self._keepalive and (
                        nm not in self._procs or self._procs[nm].poll() is not None):
                    self._start_bg(nm, self._bg_args.get(nm, [nm.lower()]))
            self._root.after(delay_ms, _revive)
        self._update_status()

    # -- network stack (Bridge + Tunnel, T75/T79) --------------------------------

    def _network_preflight(self) -> bool:
        """Check the Network stack's external dependencies BEFORE starting.

        The user must know what's missing and how to get it — no silent
        failures. Returns True when everything needed is available."""
        import shutil as _sh
        ok = True
        self._write("Checking dependencies:\n", tag="dim")
        runner = next((r for r in ("mcp-proxy", "uvx", "uv", "pipx")
                       if _sh.which(r)), None)
        if runner:
            self._write(f"  ✓ mcp-proxy runner: {runner}\n", tag="ok")
        else:
            ok = False
            self._write(
                "  ✗ no way to run mcp-proxy (needed by the Bridge).\n"
                "    Install one of:\n"
                "      winget install --id astral-sh.uv        (recommended)\n"
                "      pip install pipx\n", tag="err")
        if _sh.which("cloudflared"):
            self._write("  ✓ cloudflared (Tunnel)\n", tag="ok")
        else:
            ok = False
            self._write(
                "  ✗ cloudflared not found (needed by the Tunnel).\n"
                "    Install it:\n"
                "      winget install --id Cloudflare.cloudflared\n", tag="err")
        return ok

    def _cloud_start(self) -> None:
        self._clear_output()
        self._write("$ Starting Network (Bridge + Tunnel, watchdog ON)\n\n",
                    tag="cmd")
        if not self._network_preflight():
            self._write("\nInstall the missing dependencies above, then press "
                        "Start Network again.\n", tag="err")
            self._set_status("error", "Network: missing dependencies")
            return
        self._write(
            "\nBoth processes run INSIDE this window (no terminals to keep "
            "open); closing the GUI stops them. Watchdog restarts them if "
            "they drop.\n\n", tag="dim")
        self._keepalive.update(("Bridge", "Tunnel"))
        self._restarts.pop("Bridge", None)
        self._restarts.pop("Tunnel", None)
        self._net_state = "starting"      # full trace until the URL arrives
        self._bridge_via = ""
        self._start_bg("Bridge", ["bridge"])
        self._set_status("running", "Network: starting Bridge…")
        # Start the Tunnel ONLY once the Bridge actually listens on its port —
        # tunnelling an empty port just hands out a broken connector URL.
        threading.Thread(target=self._await_bridge_then_tunnel,
                         daemon=True).start()

    def _await_bridge_then_tunnel(self, port: int = 8000,
                                  timeout: float = 90.0) -> None:
        import socket
        import time as _t
        deadline = _t.monotonic() + timeout
        while _t.monotonic() < deadline:
            if "Tunnel" not in self._keepalive:      # user pressed Stop meanwhile
                return
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1.5):
                    pass
                self._root.after(0, lambda: (
                    self._write(f"\n[Bridge] ready on 127.0.0.1:{port} — "
                                "starting Tunnel…\n", tag="ok"),
                    self._start_bg("Tunnel", ["tunnel"]),
                    self._set_status("running", "Network: Bridge + Tunnel")))
                return
            except OSError:
                _t.sleep(1.0)
        self._root.after(0, lambda: (
            self._write(f"\n[!] Bridge did not open port {port} within "
                        f"{int(timeout)}s — Tunnel NOT started. Check the "
                        "[Bridge] lines above for the error.\n", tag="err"),
            self._keepalive.discard("Tunnel"),
            self._set_status("error", "Network: Bridge failed")))

    def _cloud_stop(self) -> None:
        self._write("\nStopping Network...\n", tag="cmd")
        self._net_state = "off"
        self._stopping.update(("Bridge", "Tunnel"))
        self._keepalive.discard("Bridge")
        self._keepalive.discard("Tunnel")
        for name in ("Bridge", "Tunnel"):
            proc = self._procs.get(name)
            if proc and proc.poll() is None:
                try:
                    if os.name == "nt":
                        subprocess.call(["taskkill", "/F", "/T", "/PID", str(proc.pid)])
                    else:
                        proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
                self._write(f"[{name}] terminated.\n", tag="err")
        self._tunnel_url = ""
        _cfg(self._tunnel_lbl, text="")
        self._update_status()

    def _stop(self) -> None:
        self._keepalive.clear()     # user asked: no watchdog resurrection
        self._net_state = "off"
        self._stopping.update(n for n, p in self._procs.items()
                              if p.poll() is None)
        stopped = False
        for name, proc in list(self._procs.items()):
            if proc.poll() is None:
                try:
                    if os.name == "nt":
                        subprocess.call(["taskkill", "/F", "/T", "/PID", str(proc.pid)])
                    else:
                        proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
                stopped = True
        if stopped:
            self._write("\n[all stopped]\n", tag="err")
            self._set_status("stopped", "Stopped")
        self._procs.clear()
        self._queues.clear()
        self._tunnel_url = ""
        _cfg(self._tunnel_lbl, text="")
        self._update_status()

    def _update_status(self) -> None:
        active = [n for n, p in self._procs.items() if p.poll() is None]
        if active:
            self._set_status("running", f"Running: {', '.join(active)}")
        else:
            self._set_status("ready", "Ready")

    # -- command console (T74) --------------------------------------------------

    def _run_command_line(self) -> None:
        """Execute the typed `neuron …` subcommand from the console bar."""
        raw = self._cmd_entry.get().strip()
        if not raw:
            return
        if raw.lower().startswith("neuron "):
            raw = raw[7:].strip()          # tolerate a pasted full command
        try:
            args = shlex.split(raw)
        except ValueError as exc:
            self._write(f"\n[!] cannot parse command: {exc}\n", tag="err")
            return
        if not args or args[0] not in _KNOWN_SUBCOMMANDS:
            self._write(
                f"\n[!] unknown subcommand {args[0] if args else '?'} — try: "
                + ", ".join(sorted(_KNOWN_SUBCOMMANDS)) + "\n", tag="err")
            return
        self._cmd_history.append(raw)
        self._hist_idx = len(self._cmd_history)
        self._cmd_entry.delete(0, "end")
        if args[0] == "connect":
            self._open_turso_dialog()
        elif args[0] == "console":
            # stdin-driven tools: a piped run dies with EOFError on the first
            # input() — they need a real terminal.
            self._run_terminal(args, display=args[0])
        elif args[0] in ("bridge", "tunnel"):
            self._keepalive.add(args[0].capitalize())
            self._start_bg(args[0].capitalize(), args)
        else:
            self._run(args, display=args[0])

    def _history_nav(self, step: int) -> None:
        if not self._cmd_history:
            return
        self._hist_idx = max(0, min(len(self._cmd_history),
                                    self._hist_idx + step))
        self._cmd_entry.delete(0, "end")
        if self._hist_idx < len(self._cmd_history):
            self._cmd_entry.insert(0, self._cmd_history[self._hist_idx])

    # -- import vault (T74) -------------------------------------------------------

    def _import_vault(self) -> None:
        """Pick a vault folder with a dialog and stream the import script."""
        script = self._find_script("import_vault.py")
        if not script:
            self._write("\n[!] import_vault.py not found (expected in the "
                        "install's scripts/ folder).\n", tag="err")
            return
        try:
            from tkinter import filedialog
            vault = filedialog.askdirectory(
                parent=self._root, title="Choose the vault folder to import")
        except Exception as exc:
            self._write(f"\n[!] folder dialog failed: {exc}\n", tag="err")
            return
        if not vault:
            return
        self._clear_output()
        self._write(f"$ import_vault --vault {vault}\n\n", tag="cmd")
        self._set_status("running", "Import Vault")

        q: queue.Queue[str] = queue.Queue()
        self._queues["__fg__"] = q
        cmd = [sys.executable or "python", script, "--vault", vault]

        def _target() -> None:
            try:
                creation_flags = 0x08000000 if os.name == "nt" else 0
                env = os.environ.copy()
                env["PYTHONUNBUFFERED"] = "1"
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, creationflags=creation_flags, env=env)
                self._procs["__fg__"] = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    q.put(line)
                proc.wait()
            except Exception as exc:
                q.put(f"[import] {exc}\n")
            finally:
                q.put("__DONE____fg__")

        threading.Thread(target=_target, daemon=True).start()

    # -- deploy update (T79) -------------------------------------------------------

    def _deploy_update(self) -> None:
        """Sync the source tree into the active install via scripts/deploy.ps1."""
        if sys.platform != "win32":
            self._write("\n[!] Deploy Update is Windows-only (deploy.ps1). On "
                        "macOS/Linux reinstall with install.sh.\n", tag="err")
            return
        script = self._find_script("deploy.ps1")
        if not script:
            self._write("\n[!] scripts/deploy.ps1 not found. It ships only in the "
                        "source repo (not the installed wheel). Set NEURON_REPO to "
                        "your checkout, e.g.\n"
                        "      setx NEURON_REPO \"C:\\path\\to\\neuron-project\"\n"
                        "    then reopen the GUI — or run deploy.ps1 from the repo "
                        "directly.\n", tag="err")
            return
        fg = self._procs.get("__fg__")
        if fg is not None and fg.poll() is None:
            self._write("\n[!] a command is already running.\n", tag="err")
            return
        self._clear_output()
        self._write(f"$ deploy.ps1 -Yes   ({script})\n\n", tag="cmd")
        self._set_status("running", "Deploy Update")

        q: queue.Queue[str] = queue.Queue()
        self._queues["__fg__"] = q
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", script, "-Yes"]

        def _target() -> None:
            try:
                creation_flags = 0x08000000 if os.name == "nt" else 0
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, creationflags=creation_flags)
                self._procs["__fg__"] = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    q.put(line)
                proc.wait()
            except Exception as exc:
                q.put(f"[deploy] {exc}\n")
            finally:
                q.put("__DONE____fg__")

        threading.Thread(target=_target, daemon=True).start()

    def _uninstall(self, purge_data: bool = False) -> None:
        """Remove Neuron from the GUI, with a safe data-preserving default."""
        from tkinter import messagebox

        warning = ("Remove Neuron, its registrations, and all local memory data?\n\n"
                   "This cannot be undone.") if purge_data else (
                       "Remove Neuron and its registrations?\n\n"
                       "Local memory data will be kept.")
        if not messagebox.askyesno("Neuron uninstall", warning, parent=self._root):
            return

        script = self._find_script("uninstall.ps1") if sys.platform == "win32" else None
        if script:
            cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", script, "-Yes"]
            if purge_data:
                cmd.append("-Data")
            display = "Uninstall + memory" if purge_data else "Uninstall Neuron"
            self._run_external(cmd, display, "$ " + " ".join(cmd) + "\n\n")
            return

        # An installed copy may not carry the repository maintenance scripts.
        # The built-in command still unregisters Neuron and can purge its data.
        cmd = [sys.executable or "python", "-m", "neuron", "setup", "--uninstall", "--yes"]
        if purge_data:
            cmd.append("--purge-data")
        self._run_external(cmd, "Uninstall (built-in fallback)",
                           "$ " + " ".join(cmd) + "\n\n")

    # -- embedding model switch (T82) ----------------------------------------------

    _ENV_KEY = "NS_EMBED_MODEL"

    def _resolve_env_file(self) -> str:
        """The .env the server will actually read (same logic as neuron._env),
        falling back to <root>/.env next to scripts/."""
        try:
            from neuron._env import _find_env_file
            found = _find_env_file()
            if found:
                return found
        except Exception:
            pass
        script = self._find_script("reembed.py")
        root = os.path.dirname(os.path.dirname(script)) if script else os.getcwd()
        return os.path.join(root, ".env")

    def _set_embed_model(self, model: str, label: str) -> None:
        """Write NS_EMBED_MODEL into the .env (upsert, comments preserved)."""
        env_path = self._resolve_env_file()
        try:
            lines: list[str] = []
            if os.path.isfile(env_path):
                with open(env_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
            replaced = False
            for i, ln in enumerate(lines):
                if ln.strip().lstrip("#").strip().startswith(self._ENV_KEY + "="):
                    lines[i] = f"{self._ENV_KEY}={model}"
                    replaced = True
                    break
            if not replaced:
                lines.append(f"{self._ENV_KEY}={model}")
            with open(env_path, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
        except OSError as exc:
            self._write(f"\n[!] cannot write {env_path}: {exc}\n", tag="err")
            return
        self._clear_output()
        self._write(f"Embedding model → {label}\n", tag="ok")
        self._write(f"  {self._ENV_KEY}={model}\n", tag="cmd")
        self._write(f"  written to: {env_path}\n\n", tag="dim")
        self._write("Both models are 384-dim, so no schema change — but "
                    "vectors from different models don't mix:\n"
                    "existing stores keep working, new vectors use the new "
                    "model only after a re-embed.\n"
                    "Restart your AI apps to pick the change up.\n\n", tag="dim")
        from tkinter import messagebox
        if messagebox.askyesno(
                "Re-embed now?",
                "Regenerate the vectors of every graph with the new model "
                "now?\n(The model downloads on first use if not cached.)",
                parent=self._root):
            self._reembed_store(model=model)

    def _reembed_store(self, model: str | None = None) -> None:
        """Run scripts/reembed.py --all, streaming into the pane."""
        script = self._find_script("reembed.py")
        if not script:
            self._write("\n[!] reembed.py not found (expected in scripts/).\n",
                        tag="err")
            return
        env_extra = {"NS_EMBED_MODEL": model} if model else None
        cmd = [sys.executable or "python", script, "--all"]
        self._run_external(cmd, "Re-embed Store",
                           "$ reembed.py --all\n\n", env_extra=env_extra)

    # -- test runner (T82) -----------------------------------------------------------

    def _run_tests(self) -> None:
        """Run the pytest suite; on failure offer to launch Repair."""
        script = self._find_script("reembed.py")   # anchor: <root>/scripts/
        root = os.path.dirname(os.path.dirname(script)) if script else None
        tests = os.path.join(root, "tests") if root else None
        if not tests or not os.path.isdir(tests):
            self._write("\n[!] tests/ folder not found — run from a source "
                        "checkout or a deployed install (deploy copies "
                        "tests/).\n", tag="err")
            return

        def _after(rc: int) -> None:
            if rc == 0:
                self._write("\n✓ All tests passed.\n", tag="ok")
                return
            self._write("\n✗ Some tests FAILED (see above).\n", tag="err")
            from tkinter import messagebox
            if messagebox.askyesno(
                    "Tests failed",
                    "Some tests failed.\n\nRun Repair now (doctor --fix: "
                    "checks registrations and running servers)?\n\n"
                    "If the code itself is broken, use Deploy Update to "
                    "re-sync the install instead.",
                    parent=self._root):
                self._run(["setup", "--repair"], display="Repair")

        cmd = [sys.executable or "python", "-m", "pytest", "tests", "-q",
               "--color=no"]
        self._run_external(cmd, "Run Tests", "$ pytest tests -q\n\n",
                           cwd=root, on_done=_after)

    def _run_external(self, cmd: list[str], display: str, header: str, *,
                      cwd: "str | None" = None,
                      env_extra: "dict[str, str] | None" = None,
                      on_done=None) -> None:
        """Run a maintenance action and stream its output into the GUI."""
        fg = self._procs.get("__fg__")
        if fg is not None and fg.poll() is None:
            self._write("\n[!] a command is already running.\n", tag="err")
            return
        self._clear_output()
        self._write(header, tag="cmd")
        self._set_status("running", display)
        self._fg_on_done = on_done
        q: queue.Queue[str] = queue.Queue()
        self._queues["__fg__"] = q

        def _target() -> None:
            try:
                creation_flags = 0x08000000 if os.name == "nt" else 0
                env = os.environ.copy()
                env["PYTHONUNBUFFERED"] = "1"
                env["PYTHONUTF8"] = "1"
                if env_extra:
                    env.update(env_extra)
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, encoding="utf-8", errors="replace",
                    creationflags=creation_flags, env=env, cwd=cwd)
                self._procs["__fg__"] = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    q.put(line)
                proc.wait()
            except Exception as exc:
                q.put(f"[{display}] {exc}\n")
            finally:
                q.put("__DONE____fg__")

        threading.Thread(target=_target, daemon=True).start()

    @staticmethod
    def _find_script(name: str) -> str | None:
        """Locate a repo/install script.

        Search order: ``NEURON_REPO`` (explicit source checkout, same override
        used by ``manage.do_visualize`` and ``generate_graph_html``), then the
        repo layout relative to the package, then the install dir. Maintainer-only
        scripts like ``deploy.ps1`` ship in the repo, not the wheel, so an
        *installed* GUI needs ``NEURON_REPO`` set to reach them."""
        import neuron
        pkg = os.path.dirname(os.path.abspath(neuron.__file__))
        repo = os.environ.get("NEURON_REPO", "")
        candidates = [
            os.path.join(repo, "scripts", name) if repo else "",
            os.path.join(os.path.dirname(os.path.dirname(pkg)), "scripts", name),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                         os.environ.get("NEURON_SLUG", "neuron5"),
                         "scripts", name),
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        return None

    @staticmethod
    def _turso_configured() -> bool:
        """True if Turso cloud credentials are saved (active OR commented in
        .env, or live in the environment) — i.e. 'Switch to Cloud' is usable.
        Evaluated when the sidebar is built."""
        try:
            from neuron.connect import cloud_creds_present
            from neuron._env import _find_env_file
            return cloud_creds_present(_find_env_file() or ".env")
        except Exception:
            return False

    # -- interactive commands (need a real terminal) ---------------------------

    def _run_terminal(self, args: list[str], *, display: str = "") -> None:
        """Open `neuron <args>` in a new terminal window (stdin-driven tools)."""
        cmd = [sys.executable or "python", "-m", "neuron", *args]
        try:
            if sys.platform == "win32":
                # cmd /k keeps the window open after exit, so the user can
                # read errors instead of seeing a flash-and-close ("Console
                # doesn't work").
                subprocess.Popen(["cmd", "/k", *cmd],
                                 creationflags=subprocess.CREATE_NEW_CONSOLE)
            elif sys.platform == "darwin":
                script = " ".join(cmd).replace('"', '\\"')
                subprocess.Popen(["osascript", "-e",
                                  f'tell app "Terminal" to do script "{script}"'])
            else:
                for term in ("x-terminal-emulator", "gnome-terminal", "konsole",
                             "xterm"):
                    import shutil as _sh
                    if _sh.which(term):
                        subprocess.Popen([term, "-e", *cmd])
                        break
                else:
                    self._write("\n[!] No terminal emulator found — run "
                                f"`neuron {' '.join(args)}` manually.\n", tag="err")
                    return
            self._write(f"\n[{display or args[0]}] opened in a new terminal.\n",
                        tag="ok")
        except Exception as exc:
            self._write(f"\n[!] Could not open terminal: {exc}\n", tag="err")

    # -- setup wizard ----------------------------------------------------------

    def _open_wizard(self) -> None:
        _Wizard(self._root, on_log=self._write)

    def _open_turso_dialog(self) -> None:
        _TursoDialog(self._root, on_log=self._write, on_saved=self._refresh_cloud_btn)

    def _refresh_cloud_btn(self) -> None:
        """Re-enable the 'Switch to Cloud' button after credentials are saved."""
        if self._cloud_btn is None:
            return
        if self._turso_configured():
            self._cloud_btn.configure(state="normal")
            self._cloud_btn.bind("<Enter>", lambda _e, b=self._cloud_btn: b.configure(bg=_ACCENT))
            self._cloud_btn.bind("<Leave>", lambda _e, b=self._cloud_btn: b.configure(bg=_HOVER))

    def _check_cloud_config(self) -> None:
        """Run scripts/check_cloud_config.py — offline Turso readiness check."""
        script = self._find_script("check_cloud_config.py")
        if script is None:
            self._write("\n[!] check_cloud_config.py not found (expected in "
                        "scripts/). Set NEURON_REPO if running from an install.\n",
                        tag="err")
            return
        self._write("$ check_cloud_config.py\n\n", tag="cmd")
        self._run([sys.executable, script], display="Check Config")

    def _init_cloud(self) -> None:
        """Run scripts/init_cloud.py — one-shot Turso Cloud schema init."""
        script = self._find_script("init_cloud.py")
        if script is None:
            self._write("\n[!] init_cloud.py not found (expected in "
                        "scripts/). Set NEURON_REPO if running from an install.\n",
                        tag="err")
            return
        self._write("$ init_cloud.py\n\n", tag="cmd")
        self._run([sys.executable, script], display="Init Cloud")


# ---------------------------------------------------------------------------
# Turso connection dialog — no terminal, no token in the command line
# ---------------------------------------------------------------------------

class _TursoDialog(tk.Toplevel):
    """Small guided Turso form: validate, probe, then save credentials."""

    def __init__(self, parent: tk.Misc, *, on_log=None, on_saved=None) -> None:
        super().__init__(parent, bg=_BG)
        self.title("Neuron · Turso Cloud")
        self.geometry("590x390")
        self.minsize(520, 350)
        self.transient(parent)
        self._on_log = on_log
        self._on_saved = on_saved
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._busy = False
        self._verified = False

        hdr = _make(self, tk.Frame, bg=_CARD,
                    highlightbackground=_CARD_EDGE, highlightthickness=1)
        hdr.pack(fill="x", padx=16, pady=(14, 10), ipady=8)
        _make(hdr, tk.Label, bg=_CARD, fg=_FG, text="Turso Cloud",
              font=(_FONT, 15, "bold")).pack(side="left", padx=14)
        _make(hdr, tk.Label, bg=_CARD, fg=_DIM,
              text="test first · save only after success",
              font=(_FONT, 9)).pack(side="left", padx=8, pady=(4, 0))

        body = _make(self, tk.Frame, bg=_BG)
        body.pack(fill="both", expand=True, padx=26, pady=2)
        self._url = self._field(body, "Database URL", "libsql://…")
        self._token = self._field(body, "Auth token", "paste your Turso token", secret=True)
        self._show_token = tk.BooleanVar(value=False)
        show = tk.Checkbutton(body, text="Show token", variable=self._show_token,
                              command=self._toggle_token, bg=_BG, fg=_DIM,
                              selectcolor=_SURFACE, activebackground=_BG,
                              activeforeground=_FG, highlightthickness=0)
        show.pack(anchor="w", pady=(2, 8))

        self._status = _make(body, tk.Label, bg=_BG, fg=_DIM, text="Enter both values, then test the connection.",
                             font=(_FONT, 9), anchor="w", justify="left", wraplength=520)
        self._status.pack(fill="x", pady=(2, 10))
        self._progress = None

        bar = _make(self, tk.Frame, bg=_BG)
        bar.pack(fill="x", padx=26, pady=(8, 16))
        self._check = tk.Button(bar, text="Test connection", command=self._test,
                                bg=_HOVER, fg=_FG, relief="flat", padx=14, pady=5,
                                font=(_FONT, 9, "bold"))
        self._check.pack(side="left")
        self._save = tk.Button(bar, text="Save & use Turso", command=self._save_credentials,
                               bg=_ACCENT, fg=_BG, relief="flat", padx=14, pady=5,
                               font=(_FONT, 9, "bold"), state="disabled")
        self._save.pack(side="right")

    def _field(self, parent: tk.Frame, label: str, hint: str, *, secret: bool = False) -> tk.Entry:
        _make(parent, tk.Label, bg=_BG, fg=_FG, text=label,
              font=(_FONT, 9, "bold"), anchor="w").pack(fill="x", pady=(6, 3))
        entry = tk.Entry(parent, bg=_SURFACE, fg=_FG, insertbackground=_FG,
                         relief="flat", font=("Consolas", 10),
                         highlightthickness=1, highlightbackground=_SEP,
                         highlightcolor=_ACCENT, show="•" if secret else "")
        entry.pack(fill="x", ipady=6)
        return entry

    def _toggle_token(self) -> None:
        self._token.configure(show="" if self._show_token.get() else "•")

    def _test(self) -> None:
        url, token = self._url.get().strip(), self._token.get().strip()
        try:
            from neuron.connect import validate_url
            error = validate_url(url)
        except Exception as exc:
            error = str(exc)
        if error:
            self._set_status(error, _RED)
            return
        if not token:
            self._set_status("Auth token is required.", _RED)
            return
        self._busy = True
        self._verified = False
        self._check.configure(state="disabled")
        self._save.configure(state="disabled")
        self._set_status("Testing read + write access…", _YELLOW)

        def worker() -> None:
            try:
                from neuron.connect import probe_connection
                ok, scheme, detail = probe_connection(url, token)
                self._queue.put(("result", ("ok" if ok else "bad") + "|" + (scheme or "") + "|" + detail))
            except Exception as exc:
                self._queue.put(("result", "bad||" + str(exc)))
        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._poll)

    def _poll(self) -> None:
        try:
            kind, payload = self._queue.get_nowait()
        except queue.Empty:
            if self._busy:
                self.after(100, self._poll)
            return
        if kind == "result":
            state, scheme, detail = payload.split("|", 2)
            self._busy = False
            self._check.configure(state="normal")
            self._verified = state == "ok"
            self._save.configure(state="normal" if self._verified else "disabled")
            self._set_status(("Connection verified" + (" via " + scheme if scheme else "") + ": " + detail)
                             if self._verified else "Connection failed: " + detail,
                             _GREEN if self._verified else _RED)

    def _save_credentials(self) -> None:
        if not self._verified:
            return
        try:
            from neuron.connect import update_env_file
            env_path = os.path.join(os.getcwd(), ".env")
            update_env_file(env_path, {"TURSO_DATABASE_URL": self._url.get().strip(),
                                       "TURSO_AUTH_TOKEN": self._token.get().strip()})
            self._set_status("Saved. New Neuron processes will use Turso Cloud.", _GREEN)
            if self._on_log:
                self._on_log("\n[turso] connection verified and credentials saved.\n", tag="ok")
            self._save.configure(state="disabled", text="Saved")
            if self._on_saved:
                self._on_saved()
        except Exception as exc:
            self._set_status("Could not save credentials: " + str(exc), _RED)

    def _set_status(self, text: str, colour: str) -> None:
        _cfg(self._status, text=text, fg=colour)


# ---------------------------------------------------------------------------
# Setup wizard — guided install, in-process registration engine
# ---------------------------------------------------------------------------

_STEPS = ("Welcome", "Checks", "Clients", "Install", "Done")


class _Wizard(tk.Toplevel):
    """Step-by-step guided setup.

    Drives ``neuron.clients`` *in-process* (register/doctor are stdlib-only),
    so there are no stdin prompts to hang on and results are structured.
    Only the optional model pre-warm shells out (heavy imports stay out of
    the GUI process).
    """

    def __init__(self, parent: tk.Misc, *, on_log=None) -> None:
        super().__init__(parent, bg=_BG)
        self.title("Neuron — Setup Wizard")
        self.minsize(620, 480)
        self.transient(parent)
        self._on_log = on_log
        self._step = 0
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._busy = False
        self._slug = os.environ.get("NEURON_SLUG", "neuron5")
        self._client_vars: dict[str, tk.BooleanVar] = {}
        self._prewarm_var = tk.BooleanVar(value=False)
        self._check_rows: list[tuple[str, str, str]] = []
        self._install_ok: bool | None = None

        self._build_header()
        self._body = _make(self, tk.Frame, bg=_BG)
        self._body.pack(fill="both", expand=True, padx=18, pady=(4, 0))
        self._build_footer()
        self._show_step()

    # -- chrome ---------------------------------------------------------------

    def _build_header(self) -> None:
        hdr = _make(self, tk.Frame, bg=_CARD,
                    highlightbackground=_CARD_EDGE, highlightthickness=1)
        hdr.pack(fill="x", padx=18, pady=(14, 2), ipady=5)
        self._logo_image = _load_logo(self, 34)
        if self._logo_image is not None:
            _make(hdr, tk.Label, bg=_CARD, image=self._logo_image).pack(
                side="left", padx=(10, 8))
        _make(hdr, tk.Label, bg=_CARD, fg=_FG, text="NEURON SETUP",
              font=(_FONT, 14, "bold")).pack(side="left")
        self._crumb = _make(hdr, tk.Label, bg=_BG, fg=_DIM, text="",
                            font=("Segoe UI", 9))
        self._crumb.pack(side="right", pady=(4, 0))

    def _build_footer(self) -> None:
        _make(self, tk.Frame, bg=_SEP, height=1).pack(fill="x", padx=12,
                                                      pady=(8, 0))
        bar = _make(self, tk.Frame, bg=_BG)
        bar.pack(fill="x", padx=18, pady=10)

        def _btn(text: str, cmd, *, accent: bool = False) -> tk.Button:
            b = _make(bar, tk.Button, bg=_ACCENT if accent else _HOVER,
                      fg=_BG if accent else _FG, text=text,
                      font=("Segoe UI", 9, "bold" if accent else "normal"),
                      activebackground=_HOVER, relief="flat", padx=16, pady=4,
                      command=cmd)
            return b

        self._btn_cancel = _btn("Cancel", self.destroy)
        self._btn_cancel.pack(side="left")
        self._btn_next = _btn("Next  ▸", self._next, accent=True)
        self._btn_next.pack(side="right")
        self._btn_back = _btn("◂  Back", self._back)
        self._btn_back.pack(side="right", padx=(0, 8))

    def _crumb_text(self) -> str:
        parts = []
        for i, name in enumerate(_STEPS):
            parts.append(f"● {name}" if i == self._step else f"○ {name}")
        return "   ".join(parts)

    def _clear_body(self) -> None:
        for w in self._body.winfo_children():
            w.destroy()

    def _title(self, text: str, sub: str = "") -> None:
        _make(self._body, tk.Label, bg=_BG, fg=_FG, text=text,
              font=("Segoe UI", 13, "bold"), anchor="w").pack(fill="x",
                                                              pady=(10, 2))
        if sub:
            _make(self._body, tk.Label, bg=_BG, fg=_DIM, text=sub,
                  font=("Segoe UI", 9), anchor="w", justify="left",
                  wraplength=560).pack(fill="x", pady=(0, 10))

    # -- navigation -----------------------------------------------------------

    def _next(self) -> None:
        if self._busy:
            return
        if self._step == 3 and self._install_ok is None:
            return  # install still pending — button relabelled anyway
        if self._step < len(_STEPS) - 1:
            self._step += 1
            self._show_step()
        else:
            self.destroy()

    def _back(self) -> None:
        if self._busy or self._step == 0:
            return
        self._step -= 1
        self._show_step()

    def _show_step(self) -> None:
        self._clear_body()
        _cfg(self._crumb, text=self._crumb_text())
        self._btn_back.configure(
            state="normal" if 0 < self._step < 4 else "disabled")
        self._btn_next.configure(text="Finish" if self._step == len(_STEPS) - 1
                                 else "Next  ▸")
        getattr(self, f"_step_{self._step}")()

    # -- step 0: welcome --------------------------------------------------------

    def _step_0(self) -> None:
        self._title("Welcome",
                    "This wizard sets Neuron up as persistent semantic memory "
                    "for your AI clients. It will:")
        for line in (
            "1.  Check your environment (Python, install, existing setup)",
            "2.  Let you pick which AI clients to register Neuron in",
            "3.  Register them and run a health check",
            "4.  Optionally pre-download the embedding model (~380 MB)",
        ):
            _make(self._body, tk.Label, bg=_BG, fg=_FG, text=line,
                  font=("Segoe UI", 10), anchor="w").pack(fill="x", padx=12,
                                                          pady=2)
        _make(self._body, tk.Label, bg=_BG, fg=_DIM,
              text="Nothing is written until the Install step. "
                   "Every config file is backed up before it is touched.",
              font=("Segoe UI", 9, "italic"), anchor="w",
              wraplength=560).pack(fill="x", padx=12, pady=(14, 0))

    # -- step 1: environment checks ---------------------------------------------

    def _step_1(self) -> None:
        self._title("Environment checks", "Read-only — nothing is modified.")
        self._check_frame = _make(self._body, tk.Frame, bg=_BG)
        self._check_frame.pack(fill="both", expand=True, padx=8)
        self._busy = True
        self._btn_next.configure(state="disabled")
        threading.Thread(target=self._run_checks, daemon=True).start()
        self.after(80, self._poll_checks)

    def _run_checks(self) -> None:
        rows: list[tuple[str, str, str]] = []          # (mark, label, detail)
        v = sys.version_info
        rows.append(("ok" if v >= (3, 10) else "bad",
                     f"Python {v.major}.{v.minor}.{v.micro}",
                     "" if v >= (3, 10) else "3.10+ required"))
        try:
            from neuron import clients as C
            py = C.default_server_python(self._slug)
            rows.append(("ok" if os.path.exists(py) else "warn",
                         "Server interpreter",
                         py if os.path.exists(py)
                         else f"venv missing — will use this Python ({sys.executable})"))
            detected = 0
            for name, spec in C.CLIENTS.items():
                path, _ = C.pick_existing(spec["candidates"]())
                if path:
                    detected += 1
            rows.append(("ok" if detected else "warn",
                         f"AI clients detected: {detected}",
                         "" if detected else "none found — you can still "
                         "register manually later"))
            lines, problems = C.doctor(self._slug, py if os.path.exists(py)
                                       else (sys.executable or "python"))
            rows.append(("ok" if problems == 0 else "warn",
                         "Current registrations",
                         "all healthy" if problems == 0
                         else f"{problems} problem(s) — Install will repair"))
        except Exception as exc:                        # pragma: no cover
            rows.append(("bad", "Engine check failed", str(exc)))
        self._check_rows = rows
        self._queue.put(("checks-done", ""))

    def _poll_checks(self) -> None:
        try:
            while True:
                kind, _ = self._queue.get_nowait()
                if kind == "checks-done":
                    self._render_checks()
                    self._busy = False
                    self._btn_next.configure(state="normal")
                    return
        except queue.Empty:
            pass
        if self._busy:
            self.after(80, self._poll_checks)

    def _render_checks(self) -> None:
        marks = {"ok": ("✓", _GREEN), "warn": ("⚠", _YELLOW), "bad": ("✗", _RED)}
        for mark, label, detail in self._check_rows:
            row = _make(self._check_frame, tk.Frame, bg=_BG)
            row.pack(fill="x", pady=3)
            sym, col = marks.get(mark, ("•", _DIM))
            _make(row, tk.Label, bg=_BG, fg=col, text=sym,
                  font=("Consolas", 11, "bold"), width=2).pack(side="left")
            _make(row, tk.Label, bg=_BG, fg=_FG, text=label,
                  font=("Segoe UI", 10)).pack(side="left")
            if detail:
                _make(row, tk.Label, bg=_BG, fg=_DIM, text=f"   {detail}",
                      font=("Segoe UI", 9)).pack(side="left")

    # -- step 2: client selection -------------------------------------------------

    def _step_2(self) -> None:
        self._title("Choose your AI clients",
                    "Detected clients are pre-selected. Neuron is registered "
                    "only in the ones you tick.")
        try:
            from neuron import clients as C
        except Exception as exc:                        # pragma: no cover
            _make(self._body, tk.Label, bg=_BG, fg=_RED,
                  text=f"Cannot load registration engine: {exc}",
                  font=("Segoe UI", 10)).pack(fill="x", padx=12)
            return
        grid = _make(self._body, tk.Frame, bg=_BG)
        grid.pack(fill="x", padx=8)
        for i, (name, spec) in enumerate(C.CLIENTS.items()):
            path, _ = C.pick_existing(spec["candidates"]())
            detected = path is not None
            if name not in self._client_vars:
                self._client_vars[name] = tk.BooleanVar(value=detected)
            row = _make(grid, tk.Frame, bg=_BG)
            row.grid(row=i // 2, column=i % 2, sticky="w", padx=6, pady=3)
            cb = tk.Checkbutton(
                row, text=spec["label"], variable=self._client_vars[name],
                bg=_BG, fg=_FG, selectcolor=_SURFACE, activebackground=_BG,
                activeforeground=_FG, font=("Segoe UI", 10),
                highlightthickness=0)
            cb.pack(side="left")
            _make(row, tk.Label, bg=_BG, fg=_GREEN if detected else _DIM,
                  text="detected" if detected else "not found",
                  font=("Segoe UI", 8)).pack(side="left", padx=(6, 0))
        _make(self._body, tk.Frame, bg=_SEP, height=1).pack(fill="x", padx=8,
                                                            pady=10)
        tk.Checkbutton(
            self._body, text="Pre-download the embedding model now (~380 MB, "
            "one-time — otherwise it downloads on first use)",
            variable=self._prewarm_var, bg=_BG, fg=_FG, selectcolor=_SURFACE,
            activebackground=_BG, activeforeground=_FG, font=("Segoe UI", 9),
            wraplength=540, justify="left", highlightthickness=0,
        ).pack(fill="x", padx=8)

    # -- step 3: install ------------------------------------------------------------

    def _step_3(self) -> None:
        selected = [n for n, v in self._client_vars.items() if v.get()]
        self._title("Installing",
                    f"Registering {len(selected)} client(s), then a health "
                    "check." + (" Model pre-warm at the end."
                                if self._prewarm_var.get() else ""))
        try:
            from tkinter import ttk
            self._bar = ttk.Progressbar(
                self._body, style="Neuron.Horizontal.TProgressbar",
                maximum=len(selected) + 1 + (1 if self._prewarm_var.get() else 0))
        except Exception:                               # pragma: no cover
            self._bar = None
        if self._bar is not None:
            self._bar.pack(fill="x", padx=8, pady=(0, 8))
        self._log = tk.Text(self._body, bg=_OUT_BG, fg=_FG, height=12,
                            font=("Consolas", 9), wrap="word", relief="flat",
                            padx=8, pady=6)
        self._log.pack(fill="both", expand=True, padx=8)
        self._log.tag_configure("err", foreground=_RED)
        self._log.tag_configure("ok", foreground=_GREEN)

        self._busy = True
        self._install_ok = None
        self._btn_next.configure(state="disabled")
        self._btn_cancel.configure(state="disabled")
        threading.Thread(target=self._run_install, args=(selected,),
                         daemon=True).start()
        self.after(100, self._poll_install)

    def _run_install(self, selected: list[str]) -> None:
        q = self._queue
        ok = True
        try:
            from neuron import clients as C
            py = C.default_server_python(self._slug)
            if not os.path.exists(py):
                py = sys.executable or "python"
            for name in selected:
                r = C.register(name, self._slug, py)
                ok = ok and (r.ok or r.action == "skipped")
                q.put(("log", r.line() + "\n"))
                q.put(("tick", ""))
            q.put(("log", "\nHealth check:\n"))
            lines, problems = C.doctor(self._slug, py)
            for ln in lines:
                q.put(("log", ln + "\n"))
            ok = ok and problems == 0
            q.put(("tick", ""))
            if self._prewarm_var.get():
                q.put(("log", "\nPre-downloading embedding model (~380 MB)…\n"))
                r = subprocess.run(
                    [py, "-c",
                     "from neuron.server import _get_embedder; _get_embedder(); "
                     "print('model cached')"],
                    capture_output=True, text=True, timeout=1800)
                q.put(("log", (r.stdout or "") + (r.stderr or "")))
                if r.returncode != 0:
                    q.put(("log", "[!] pre-warm failed — the model will "
                                  "download on first use.\n"))
                q.put(("tick", ""))
        except Exception as exc:
            ok = False
            q.put(("log", f"\n[!] install error: {exc}\n"))
        q.put(("install-done", "ok" if ok else "problems"))

    def _poll_install(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    tag = "err" if payload.lstrip().startswith(("[!]", "[!!]")) \
                        else ("ok" if "[OK]" in payload else None)
                    self._log.insert("end", payload, tag or ())
                    self._log.see("end")
                elif kind == "tick" and self._bar is not None:
                    self._bar.step(1)
                elif kind == "install-done":
                    self._busy = False
                    self._install_ok = payload == "ok"
                    self._btn_next.configure(state="normal")
                    self._btn_cancel.configure(state="normal")
                    if self._bar is not None:
                        self._bar["value"] = self._bar["maximum"]
                    self._log.insert(
                        "end",
                        "\nDone — everything healthy.\n" if self._install_ok
                        else "\nDone with warnings — see above (Repair in the "
                             "sidebar can fix most).\n",
                        "ok" if self._install_ok else "err")
                    self._log.see("end")
                    return
        except queue.Empty:
            pass
        if self._busy:
            self.after(100, self._poll_install)

    # -- step 4: done -------------------------------------------------------------

    def _step_4(self) -> None:
        good = bool(self._install_ok)
        self._title("Setup complete" if good else "Setup finished with warnings",
                    "Restart your AI apps so they pick up the new MCP server."
                    if good else
                    "Some steps reported problems — run Doctor or Repair from "
                    "the sidebar, or re-run this wizard.")
        for line in (
            "•  Your AI clients now share one persistent memory graph.",
            "•  Sidebar → Overview shows the graph as it grows.",
            "•  Sidebar → Visualize renders it as an interactive map.",
        ):
            _make(self._body, tk.Label, bg=_BG, fg=_FG, text=line,
                  font=("Segoe UI", 10), anchor="w").pack(fill="x", padx=12,
                                                          pady=2)
        if self._on_log:
            self._on_log("\n[wizard] setup finished — "
                         + ("all healthy.\n" if good else "with warnings.\n"),
                         tag="ok" if good else "err")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def main(argv: "list[str] | None" = None) -> int:
    try:
        import tkinter as _tk  # noqa: F401 — availability check
    except Exception:
        print("Tkinter unavailable — falling back to `neuron manage`.")
        from neuron.manage import main as _mm
        return _mm([])

    args = list(argv or [])
    root = tk.Tk()
    app = _App(root)

    if "--wizard" in args:
        root.after(200, app._open_wizard)

    if os.environ.get("NEURON_GUI_SELFTEST"):
        if os.environ.get("NEURON_GUI_SELFTEST") == "wizard":
            root.after(200, app._open_wizard)
            root.after(900, root.destroy)
        else:
            root.after(300, root.destroy)

    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main()) 