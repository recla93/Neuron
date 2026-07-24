"""Icona desktop per il control center — cross-OS, best-effort, idempotente.

Copia tool-local (keep-in-sync con gray_matter/shortcut.py e neurag/shortcut.py):
serve a Neuron STANDALONE, quando Gray Matter non è installato e quindi
`gray_matter.shortcut` non è importabile — l'installer standalone e `neuron gui
--shortcut-only` creano comunque l'icona. L'icona punta a `neuron gui`, che
bootstrappa GM al primo click.

Non solleva mai: un fallimento non deve impedire l'apertura della GUI. Idempotente
via un marker nel venv, così non rispawna PowerShell a ogni avvio.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def ensure_desktop_shortcut(tool: str, label: str, module_args: "list[str]",
                            description: str = "") -> bool:
    """Crea (una volta per installazione) un'icona desktop che apre ``<python>
    module_args`` (es. ``-m neuron gui``). `tool` è la chiave per il marker.
    Ritorna True se l'icona c'è o è stata creata; False (silenzioso) altrimenti."""
    try:
        marker = Path(sys.executable).with_name(f".{tool}-gui-shortcut")
        if marker.exists():
            return True
        ok = (_windows_lnk(label, module_args, description) if os.name == "nt"
              else _mac_command(label, module_args) if sys.platform == "darwin"
              else _linux_desktop(label, module_args, description))
        if ok:
            try:
                marker.write_text("1", encoding="utf-8")
            except OSError:
                pass
        return ok
    except Exception:  # noqa: BLE001 — mai bloccare la GUI per un'icona
        return False


def _windows_lnk(label: str, module_args: "list[str]", description: str) -> bool:
    """.lnk vero via WScript.Shell (stesso approccio dell'installer GM). Target
    pythonw = nessun flash di console. Desktop via GetFolderPath (gestisce
    OneDrive redirect)."""
    pyw = Path(sys.executable).with_name("pythonw.exe")
    target = str(pyw if pyw.exists() else Path(sys.executable))
    args = " ".join(module_args)
    workdir = str(Path(sys.executable).parent)
    ps = (
        "$d=[Environment]::GetFolderPath('Desktop'); if(-not $d){exit 1}\n"
        "$ws=New-Object -ComObject WScript.Shell\n"
        f"$sc=$ws.CreateShortcut((Join-Path $d '{label}.lnk'))\n"
        f"$sc.TargetPath='{target}'\n"
        f"$sc.Arguments='{args}'\n"
        f"$sc.WorkingDirectory='{workdir}'\n"
        f"$sc.Description='{description}'\n"
        "$sc.Save()\n"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, timeout=25, creationflags=_CREATE_NO_WINDOW)
    return r.returncode == 0


def _linux_desktop(label: str, module_args: "list[str]", description: str) -> bool:
    """.desktop in ~/.local/share/applications e sul Desktop se c'è.
    ``Terminal=true`` così un eventuale bootstrap resta visibile."""
    exec_cmd = " ".join([sys.executable, *module_args])
    content = (
        "[Desktop Entry]\nType=Application\n"
        f"Name={label}\nComment={description}\nExec={exec_cmd}\n"
        "Terminal=true\nCategories=Utility;\n")
    slug = label.lower().replace(" ", "-")
    wrote = False
    apps = Path.home() / ".local" / "share" / "applications"
    try:
        apps.mkdir(parents=True, exist_ok=True)
        (apps / f"{slug}.desktop").write_text(content, encoding="utf-8")
        wrote = True
    except OSError:
        pass
    desk = Path.home() / "Desktop"
    if desk.is_dir():
        try:
            f = desk / f"{label}.desktop"
            f.write_text(content, encoding="utf-8")
            os.chmod(f, 0o755)
            wrote = True
        except OSError:
            pass
    return wrote


def _mac_command(label: str, module_args: "list[str]") -> bool:
    """.command doppio-clic sul Desktop (i .app veri servirebbero un bundle)."""
    desk = Path.home() / "Desktop"
    if not desk.is_dir():
        return False
    try:
        f = desk / f"{label}.command"
        f.write_text("#!/bin/sh\nexec " + " ".join(
            [f'"{sys.executable}"', *module_args]) + "\n", encoding="utf-8")
        os.chmod(f, 0o755)
        return True
    except OSError:
        return False
