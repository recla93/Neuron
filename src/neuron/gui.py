"""`neuron gui` — a tiny Tkinter launcher: the centralized visual hub.

Windowed entry point (see ``[project.gui-scripts]`` in pyproject) so a
double-clickable ``neuron-gui``/``neuron-gui.exe`` opens this instead of a
terminal — no ``.bat``, no bundled interpreter, no PyInstaller. Every button
just drives the same ``neuron <subcommand>`` we already ship:

  * quick, read-only actions (status / overview / doctor) render inline;
  * interactive or long-running ones (setup / manage / bridge / tunnel /
    connect / console) open in their own terminal so they keep a real stdin.

Tkinter is stdlib, so this adds no dependency. On a headless box (no display)
it falls back to the text menu of ``neuron manage``.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys

__all__ = ["main"]


def _py() -> str:
    return sys.executable or "python"


def _run_capture(args: list[str]) -> str:
    """Run `neuron <args>` and return combined stdout+stderr (for inline panes)."""
    try:
        r = subprocess.run([_py(), "-m", "neuron", *args],
                           capture_output=True, text=True, timeout=90)
        return (r.stdout or "") + (r.stderr or "") or "(no output)"
    except Exception as e:  # pragma: no cover - defensive
        return f"error running 'neuron {' '.join(args)}': {e}"


def _run_in_terminal(args: list[str]) -> None:
    """Launch `neuron <args>` in a NEW terminal window (best-effort, per-OS)."""
    cmd = [_py(), "-m", "neuron", *args]
    if os.name == "nt":
        subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        script = " ".join(shlex.quote(c) for c in cmd)
        subprocess.Popen(["osascript", "-e",
                          f'tell application "Terminal" to do script "{script}"'])
        return
    for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"],
                 ["konsole", "-e"], ["xterm", "-e"]):
        if shutil.which(term[0]):
            subprocess.Popen(term + cmd)
            return
    subprocess.Popen(cmd)  # last resort: inherit this process's console


def main(argv: "list[str] | None" = None) -> int:
    try:
        import tkinter as tk
        from tkinter import scrolledtext
    except Exception:
        print("Tkinter is not available here — falling back to `neuron manage`.")
        from neuron.manage import main as manage_main
        return manage_main([])

    root = tk.Tk()
    root.title("Neuron")
    root.minsize(560, 420)

    tk.Label(root, text="Neuron", font=("Segoe UI", 16, "bold")).pack(pady=(12, 0))
    tk.Label(root, text="semantic memory for your AI — install, manage, connect",
             fg="gray").pack(pady=(0, 8))

    out = scrolledtext.ScrolledText(root, height=12, wrap="word")

    def show(text: str) -> None:
        out.delete("1.0", "end")
        out.insert("1.0", text)

    # Quick, read-only: capture output into the pane.
    quick = [
        ("Status", ["setup", "--status"]),
        ("Overview", ["manage", "--overview"]),
        ("Doctor", ["doctor"]),
    ]
    # Interactive / long-running: open a terminal.
    actions = [
        ("Setup / Register", ["setup"]),
        ("Manage", ["manage"]),
        ("Connect Cloud", ["connect"]),
        ("HTTP Bridge", ["bridge"]),
        ("Tunnel", ["tunnel"]),
        ("Console", ["console"]),
    ]

    row1 = tk.Frame(root); row1.pack(pady=4)
    for label, args in quick:
        tk.Button(row1, text=label, width=14,
                  command=lambda a=args, l=label: show(f"$ neuron {' '.join(a)}\n\n" + _run_capture(a))
                  ).pack(side="left", padx=4)

    row2 = tk.Frame(root); row2.pack(pady=4)
    for i, (label, args) in enumerate(actions):
        tk.Button(row2, text=label, width=14,
                  command=lambda a=args: _run_in_terminal(a)
                  ).grid(row=i // 3, column=i % 3, padx=4, pady=4)

    out.pack(fill="both", expand=True, padx=10, pady=(8, 10))
    show("Pick an action. Read-only checks show here; interactive tools open a terminal.")

    # Self-check hook: build the window then exit immediately (no human needed).
    if os.environ.get("NEURON_GUI_SELFTEST"):
        root.after(300, root.destroy)

    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
