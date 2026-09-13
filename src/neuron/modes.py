"""Modalità operative del retrieval (Neuron, ippocampo).

Strategie di ranking pure applicate da `_resolve_context`:

- ``semantic`` (default): invariato — il ranking composito esistente.
- ``focus``: boost sui nodi simili al compito attivo. Il focus arriva come
  PARAMETRO (standalone: passato a mano; con GM: iniettato dal proxy dal
  blackboard `cervello/focus`) — Neuron non legge mai il DB di GM.

Qui vive anche il log dei TURNI (`turns.jsonl` in graphs_dir, append-only,
best-effort): topic + keywords per turno, che il grafo NON preserva. Era il
materiale della modalità ``pattern`` (tolta il 2026-09-13: su 155 turni reali
0 previsioni giuste, vocabolario troppo sparso). Il log resta per rimisurare
a 500 turni — coppie fra turno t e t+1, non dentro lo stesso turno.

Stdlib only. Le funzioni sono pure e testabili (__main__ incluso).
"""
from __future__ import annotations

import json
from pathlib import Path

MODES = ("semantic", "focus")

# Niente modalita' "brainstorm" qui: generare candidati inattesi richiede il
# grafo INTERO piu' i chunk NeuRAG, quindi tocca due componenti ed e' di GM
# (gray_matter_brainstorm, che si costruisce il pool con vector_search). Una
# modalita' puo' solo ri-pesare i candidati gia' selezionati per rilevanza:
# ri-ordinare per anti-rilevanza un insieme filtrato per rilevanza non fa
# emergere niente di lontano, perche' i nodi lontani non sono mai candidati.


# --- focus -------------------------------------------------------------

def focus_boost(node_scores: dict[str, float], focus_sim: dict[str, float],
                boost: float = 0.3) -> dict[str, float]:
    """Riordina i punteggi dando ``+boost * sim`` ai nodi simili al focus."""
    out = dict(node_scores)
    for kw, s in focus_sim.items():
        if kw in out:
            out[kw] += boost * s
    return out


# --- log dei turni ------------------------------------------------------

def append_turn(log_path: "str | Path", topic: str, keywords: list[str]) -> None:
    """Appende un turno al log (turns.jsonl): topic + keywords ordinate come
    ricevute. Best-effort: il log è storico, mai bloccante."""
    try:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"topic": topic, "keywords": list(keywords)},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    # focus: il nodo del focus sale
    ns = {"wal": 0.8, "persistenza": 0.6, "checkpoint": 0.7}
    boosted = focus_boost(ns, {"persistenza": 1.0, "wal": 0.1})
    assert boosted["persistenza"] > boosted["wal"], boosted

    # log dei turni: append, una riga JSON per turno
    p = Path(__import__("tempfile").mkdtemp()) / "turns.jsonl"
    append_turn(p, "seed", ["checkpoint", "wal", "seed"])
    append_turn(p, "release", ["seed", "release"])
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]
    assert [r["topic"] for r in rows] == ["seed", "release"], rows
    # cartella impossibile (un file al posto della dir) -> nessuna eccezione
    append_turn(p / "x.jsonl", "t", [])

    print("PASS: modes")
