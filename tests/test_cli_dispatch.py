"""Regressioni della CLI di Neuron: aiuto, refusi e chiusura pulita."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from neuron.__main__ import COMMANDS, _usage

_SRC = str(Path(__file__).resolve().parents[1] / "src")


def _run(*args: str) -> subprocess.CompletedProcess:
    """`neuron <args>` in un processo figlio, con stdin chiuso.

    stdin chiuso è il punto: se il comando cade nel ramo "server MCP" legge EOF
    e termina, invece di restare appeso — così il test non può bloccarsi.

    PYTHONPATH punta a `src` di QUESTO albero perché il figlio non vede il
    conftest di root, che è ciò che mette `neuron/src` in testa a sys.path per i
    test in-process. Senza questa riga `import neuron` nel figlio risolveva
    qualunque copia fosse installata altrove — misurato il 2026-08-17: un
    checkout 5.4.1 in PycharmProjects, che precede la guardia sui comandi
    sconosciuti, mentre l'albero sotto test era 6.4.0. I quattro test qui sotto
    fallivano tutti su codice che nessuno di noi stava modificando, e un test
    che non decide da dove importa misura ciò che trova, non ciò che vuoi.
    """
    env = {**os.environ,
           "PYTHONPATH": os.pathsep.join(
               p for p in (_SRC, os.environ.get("PYTHONPATH", "")) if p)}
    return subprocess.run(
        [sys.executable, "-m", "neuron", *args],
        capture_output=True, text=True, timeout=180,
        stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace", env=env,
    )


def test_usage_lists_every_command():
    """L'aiuto si costruisce da COMMANDS: una riga lì deve bastare, come per la
    GUI (gray_matter/catalog.py legge la stessa tabella)."""
    text = _usage()
    for name in COMMANDS:
        assert name in text, f"'{name}' manca nell'aiuto"


@pytest.mark.parametrize("flag", ["--help", "-h", "help"])
def test_help_prints_usage_and_exits_zero(flag):
    """Il bug: `neuron --help` non era un comando noto, quindi cadeva nel ramo
    "avvia il server MCP" e restava in ascolto su stdio senza stampare NULLA.
    La cosa più ovvia che un utente digita per prima."""
    r = _run(flag)
    assert r.returncode == 0
    assert "usage: neuron" in r.stdout
    assert "consolidate" in r.stdout        # una riga vera dalla tabella


def test_unknown_command_fails_loudly():
    """Stessa buca dei refusi: `neuron staus` partiva come server invece di
    dire che il comando non esiste."""
    r = _run("staus")
    assert r.returncode == 2
    assert "staus" in r.stderr
    assert "usage: neuron" in r.stderr
    assert not r.stdout.strip(), "un refuso non deve produrre output su stdout"


def test_prewarm_cancellation_is_not_reported_as_a_crash():
    """`t.exception()` su un task CANCELLATO ri-solleva CancelledError invece di
    restituirla — e CancelledError deriva da BaseException, quindi scappava
    anche all'`except Exception` del prewarm. Ogni chiusura pulita (il client
    stacca lo stdio mentre il modello carica ancora) stampava un traceback
    asyncio come se il server fosse morto male.

    Riproduce il callback esatto di server.py senza avviare il server.
    """
    seen: list[BaseException] = []

    async def _scenario() -> None:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _l, ctx: seen.append(ctx.get("exception")))

        async def _prewarm() -> None:
            try:
                await asyncio.sleep(30)          # sta ancora "scaldando"
            except Exception:                    # NON cattura CancelledError
                pass

        task = asyncio.ensure_future(_prewarm())
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        await asyncio.sleep(0)
        task.cancel()                            # chiusura pulita
        await asyncio.sleep(0.05)

    asyncio.run(_scenario())
    assert seen == [], f"la cancellazione ha prodotto un errore asyncio: {seen}"
