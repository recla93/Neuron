"""`neuron gui` — Tkinter visual hub with dark theme.

Windowed entry point (see ``[project.gui-scripts]`` in pyproject) so a
double-clickable ``neuron-gui``/``neuron-gui.exe`` opens this instead of
a terminal.  Every button drives ``neuron <subcommand>`` inside the same
process — no external consoles.

Layout: sidebar (collapsible sections) + streaming output + status bar.
Tkinter is stdlib, so this adds no dependency.  Falls back to
``neuron manage`` on headless boxes.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk

__all__ = ["main"]


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
    fg: str = _FG,
    **kw: object,
) -> tk.Widget:
    """Create a themed widget — one place to change defaults."""
    return cls(parent, bg=bg, fg=fg, **kw)


# ---------------------------------------------------------------------------
# Command registry — single source of truth for sidebar content
# ---------------------------------------------------------------------------

_COMMANDS: dict[str, list[tuple[str, list[str]]]] = {
    "Setup": [
        ("Register", ["register", "--all", "--yes"]),
        ("Doctor", ["doctor"]),
        ("Install", ["setup"]),
        ("Deploy", ["setup", "--deploy"]),
    ],
    "Manage": [
        ("Overview", ["manage", "--overview"]),
        ("Status", ["setup", "--status"]),
        ("Export", ["manage", "--export", "graph-export.json"]),
        ("Consolidate", ["consolidate"]),
        ("Visualize", ["manage", "--visualize"]),
    ],
    "Tools": [
        ("Console", ["console"]),
        ("Graph", ["manage", "--visualize"]),
        ("Tests", ["setup", "--test"]),
        ("Bench", ["manage", "--bench"]),
    ],
    "Cloud": [
        ("Connect", ["connect", "--check-only"]),
        ("Bridge", ["bridge"]),
        ("Tunnel", ["tunnel"]),
    ],
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
            self.child.pack(fill="x")
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
        self._proc: subprocess.Popen[str] | None = None
        self._q: queue.Queue[str] = queue.Queue()
        self._history: list[str] = []

        root.title("Neuron")
        root.minsize(780, 520)
        root.configure(bg=_BG)
        _cfg(root, bg=_BG)

        self._apply_theme()
        self._build_header()
        self._build_body()
        self._build_statusbar()
        self._refresh_context()

    # -- theme ---------------------------------------------------------------

    def _apply_theme(self) -> None:
        try:
            self._root.tk.call("ttk::style", "theme", "use", "clam")
        except tk.TclError:
            pass
        try:
            s = tk.Ttk.Style()
            s.theme_use("clam")
            s.configure(".", background=_BG, foreground=_FG, fieldbackground=_BG)
            s.configure("TButton", background=_HOVER, foreground=_FG,
                        borderwidth=0, padding=5)
            s.map("TButton",
                   background=[("active", _ACCENT), ("disabled", _BG)],
                   foreground=[("disabled", _DIM)])
        except Exception:
            pass

    # -- header --------------------------------------------------------------

    def _build_header(self) -> None:
        hdr = _make(self._root, tk.Frame, bg=_BG)
        hdr.pack(fill="x", padx=16, pady=(14, 0))

        logo = _make(hdr, tk.Label, bg=_BG, fg=_ACCENT,
                     text="NEURON", font=("Consolas", 22, "bold"))
        logo.pack(side="left")

        sub = _make(hdr, tk.Label, bg=_BG, fg=_DIM,
                    text="semantic memory for your AI",
                    font=("Segoe UI", 10))
        sub.pack(side="left", padx=(12, 0), pady=(6, 0))

        self._ver_lbl = _make(hdr, tk.Label, bg=_BG, fg=_DIM,
                              text="", font=("Consolas", 9))
        self._ver_lbl.pack(side="right", pady=(8, 0))
        self._show_version()

    # -- body (sidebar + output) ---------------------------------------------

    def _build_body(self) -> None:
        body = _make(self._root, tk.Frame, bg=_BG)
        body.pack(fill="both", expand=True, padx=8, pady=8)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_sidebar(body)
        self._build_output(body)

    def _build_sidebar(self, parent: tk.Frame) -> None:
        wrap = _make(parent, tk.Frame, bg=_BG, width=170)
        wrap.grid(row=0, column=0, sticky="ns", padx=(0, 6))
        wrap.grid_propagate(False)

        canvas = tk.Canvas(wrap, bg=_BG, highlightthickness=0, width=160)
        vsb = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = _make(canvas, tk.Frame, bg=_BG)

        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)

        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def _on_mousewheel(event: tk.Event) -> None:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        for section, items in _COMMANDS.items():
            sec = _Section(inner, section)
            for label, args in items:
                btn = _make(sec.child, tk.Button, bg=_HOVER, fg=_FG,
                            text=label, font=("Segoe UI", 9),
                            activebackground=_ACCENT, activeforeground=_BG,
                            relief="flat", anchor="w", padx=10, pady=2,
                            command=lambda a=args, l=label:
                            self._run(a, display=l))
                btn.pack(fill="x", padx=4, pady=1)
                btn.bind("<Enter>",
                         lambda _e, b=btn: b.configure(bg=_ACCENT))
                btn.bind("<Leave>",
                         lambda _e, b=btn: b.configure(bg=_HOVER))

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

    def _set_status(self, state: str, text: str) -> None:
        colour_map = {"ready": _ACCENT, "running": _YELLOW,
                      "error": _RED, "stopped": _RED}
        _cfg(self._status_dot, fg=colour_map.get(state, _DIM))
        _cfg(self._status_lbl, text=f" {text}")

    def _refresh_context(self) -> None:
        def _fetch() -> str:
            try:
                r = subprocess.run(
                    [sys.executable or "python", "-m", "neuron", "status"],
                    capture_output=True, text=True, timeout=10,
                )
                for line in (r.stdout or "").splitlines():
                    if "context" in line.lower():
                        return line.strip()
            except Exception:
                pass
            return "Context: default"
        t = threading.Thread(target=_fetch, daemon=True)

        def _apply() -> None:
            if t.is_alive():
                self._root.after(100, _apply)
                return
            _cfg(self._ctx_lbl, text=t.join())
        t.start()
        self._root.after(100, _apply)

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
            "Pick an action from the sidebar.\n"
            "Read-only commands show output here.\n"
            "Ctrl+L to clear, Ctrl+C to copy.\n",
            tag="dim",
        )

    def _write(self, text: str, *, tag: str | None = None) -> None:
        self._out.insert("end", text, tag or ())
        self._out.see("end")

    # -- command execution ---------------------------------------------------

    def _run(self, args: list[str], *, display: str | None = None) -> None:
        if self._proc and self._proc.poll() is None:
            self._write("\n[!] A command is already running — stop it first.\n",
                        tag="err")
            return

        cmd_str = "$ neuron " + " ".join(args)
        self._clear_output()
        self._write(f"{cmd_str}\n\n", tag="cmd")
        self._set_status("running", display or args[0])

        cmd = [sys.executable or "python", "-m", "neuron", *args]

        def _target() -> None:
            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
                assert self._proc.stdout is not None
                for line in self._proc.stdout:
                    self._q.put(line)
                self._proc.wait()
            except Exception as exc:
                self._q.put(f"[gui] {exc}\n")
            finally:
                self._q.put("__DONE__")

        threading.Thread(target=_target, daemon=True).start()
        self._root.after(80, self._poll_queue)

    def _poll_queue(self) -> None:
        try:
            while True:
                line = self._q.get_nowait()
                if line == "__DONE__":
                    self._on_command_done()
                    return
                self._write(line)
        except queue.Empty:
            pass
        self._root.after(80, self._poll_queue)

    def _on_command_done(self) -> None:
        if self._proc:
            rc = self._proc.returncode
            if rc and rc != 0:
                self._write(f"\n[exit {rc}]\n", tag="err")
                self._set_status("error", f"exit {rc}")
            else:
                self._write("\n", tag="ok")
                self._set_status("ready", "Done")
        self._proc = None

    def _stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._write("\n[stopped by user]\n", tag="err")
            self._set_status("stopped", "Stopped")
            self._proc = None
        else:
            self._set_status("ready", "Ready")


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

    root = tk.Tk()
    app = _App(root)

    if os.environ.get("NEURON_GUI_SELFTEST"):
        root.after(300, root.destroy)

    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
