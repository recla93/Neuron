"""ChatGPT come client: bridge + tunnel, non un file di configurazione.

Gli altri sei client girano SULLA macchina e si registrano scrivendo un JSON con
il path di un interprete locale. ChatGPT no: gira altrove e raggiunge la suite
solo via HTTP pubblico. Quindi "registrarlo" vuol dire tre cose diverse —
accendere il bridge (`neuron.bridge`, Streamable HTTP su :8000), esporlo
con un tunnel, e dare all'utente l'URL da incollare nelle sue impostazioni.

Nessuna di queste puo' fallire in silenzio, e nessuna deve bloccare l'install.

Il quick tunnel (`*.trycloudflare.com`) NON e' un'alternativa: e' una demo. Non
richiede credenziali, ma il tier gratuito senza account gli mette sopra un
timer — il tunnel SCADE da solo dopo un po', e la connessione di ChatGPT muore
senza che nessuno abbia toccato niente. Per un collegamento che dura serve un
account registrato e un named tunnel. Quindi qui il quick si offre per provare,
e si dice a chiare lettere che va registrato, con link e comandi esatti.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

# --- gli unici tre valori che cambiano fra le copie tool-local ---------------
# Copia keep-in-sync con gray_matter/chatgpt.py e neurag/chatgpt.py: anche
# in standalone i peer devono offrire gli stessi client del gateway, o
# "standalone" vuol dire "senza ChatGPT". Ognuno espone il PROPRIO bridge.
TOOL_LABEL = "Neuron"
BRIDGE_MODULE = "neuron.bridge"
BRIDGE_PORT = 8000                    # Neuron=8000, NeuRAG=8001, suite intera=8002

SIGNUP_URL = "https://dash.cloudflare.com/sign-up"
CONNECTOR_DOC = "https://platform.openai.com/docs/mcp"


def _cloudflared_cert() -> Path:
    """Dove cloudflared tiene le credenziali dei named tunnel."""
    if os.name == "nt":
        return Path(os.environ.get("USERPROFILE", "~")) / ".cloudflared" / "cert.pem"
    return Path.home() / ".cloudflared" / "cert.pem"


def state() -> dict:
    """Cosa c'e' e cosa manca. Puro: non accende niente."""
    cf = shutil.which("cloudflared")
    cert = _cloudflared_cert()
    has_creds = bool(cf) and cert.exists()
    return {
        "cloudflared": cf,
        "cert_path": str(cert),
        "has_credentials": has_creds,
        # Senza credenziali si puo' comunque partire, ma solo per provare: il
        # quick tunnel ha un timer e si spegne da solo.
        "can_start": bool(cf),
        "mode": "named" if has_creds else ("quick" if cf else "none"),
        # Vero solo con un account registrato: e' l'unica configurazione in cui
        # il collegamento a ChatGPT resta su.
        "persistent": has_creds,
        "bridge_port": BRIDGE_PORT,
    }


def instructions(st: dict | None = None) -> list[str]:
    """Le righe da mostrare all'utente. Vuote quando non c'e' niente da dire."""
    s = st or state()
    if s["mode"] == "named":
        return []
    out: list[str] = []
    if not s["cloudflared"]:
        out += [
            "cloudflared non e' installato: senza, ChatGPT non puo' raggiungere la suite.",
            "  Windows:  winget install --id Cloudflare.cloudflared",
            "  macOS:    brew install cloudflared",
            "  Linux:    https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/",
        ]
        return out
    out += [
        "Nessuna credenziale Cloudflare: si parte con un quick tunnel, che serve",
        "SOLO per provare — senza account ha un timer e SCADE da solo, portandosi",
        "dietro la connessione di ChatGPT senza nessun errore visibile.",
        "Per un collegamento che dura serve registrarsi (gratis) e creare un tunnel:",
        f"  1. crea l'account:   {SIGNUP_URL}",
        "  2. autenticati:      cloudflared tunnel login",
        "  3. crea il tunnel:   cloudflared tunnel create gray-matter",
        f"     (le credenziali finiscono in {s['cert_path']})",
        f"Poi incolla l'URL del tunnel nelle impostazioni connettori: {CONNECTOR_DOC}",
    ]
    return out


def register() -> dict:
    """Risultato nella stessa forma degli altri client, cosi' l'installer e la
    GUI lo stampano senza sapere che questo e' diverso."""
    s = state()
    detail = f"bridge :{BRIDGE_PORT} + tunnel {s['mode']}"
    if s["persistent"]:
        return {"client": "ChatGPT", "ok": True, "action": "bridge+tunnel",
                "detail": f"{TOOL_LABEL}: {detail}", "state": s}
    # `ok` FALSE anche quando si potrebbe partire: un tunnel che scade da solo
    # non e' un'installazione riuscita, e segnarla verde vorrebbe dire lasciare
    # l'utente a scoprire da solo perche' ChatGPT ha smesso di rispondere.
    return {
        "client": "ChatGPT", "ok": False,
        "action": "manual",
        "detail": (detail + " — temporaneo, scade" if s["can_start"]
                   else "cloudflared assente"),
        "state": s,
        "snippet": "\n".join(instructions(s)),
    }


def start_command() -> list[str]:
    """Il comando che accende bridge e tunnel insieme."""
    import sys
    return [sys.executable, "-m", BRIDGE_MODULE, "--tunnel",
            "--port", str(BRIDGE_PORT)]
