# Neuron — Project Memory

Memoria persistente del progetto Neuron (semantic memory MCP server). Questo file viene
letto automaticamente all'avvio di ogni chat: contiene le regole di lavoro sulle task.

---

## ⚙️ Workflow task (REGOLE OBBLIGATORIE)

Queste regole valgono per **ogni** sessione di lavoro su questo progetto.

### 1. All'avvio di ogni chat — importare le task
All'inizio di ogni nuova chat, **leggere `TASKLIST.md`** (nella root del progetto, se
presente). Per ogni task elencata lì, **creare la task corrispondente nella task list
della chat** (TaskCreate), preservando titolo, descrizione e stato.
- Lo stato in `TASKLIST.md` (`pending` / `in_progress` / `completed`) va riprodotto nella chat.
- Le task `completed` possono essere create già completate o mostrate come riferimento storico — non riproporle come da fare.

### 2. Allineamento bidirezionale chat ↔ TASKLIST.md
`TASKLIST.md` è la **fonte di verità persistente** tra una chat e l'altra. Quindi:
- Ogni volta che cambio lo stato di una task nella chat (es. `pending → in_progress`,
  `in_progress → completed`), **aggiorno subito anche `TASKLIST.md`** con lo stesso stato.
- Ogni volta che aggiungo una task in chat, la aggiungo anche a `TASKLIST.md`.
- Ogni volta che leggo/riapro `TASKLIST.md` e trovo task nuove non in chat, le importo.

Chat e `TASKLIST.md` non devono mai divergere a fine sessione.

### 3. Formato di ogni task (in `TASKLIST.md` e in chat)
Ogni task deve avere:
- **Titolo principale** — sintetico e azionabile.
- **Descrizione esaustiva** del problema — abbastanza dettagliata da dare contesto
  completo a una chat futura che parte da zero (cosa, perché, rischio/impatto).
- **File rilevanti** — i percorsi (e righe quando utile) che danno contesto al problema.

Usare il template definito in cima a `TASKLIST.md`.

---

## Struttura progetto (riferimento rapido)

- **Repo sorgente (sviluppo):** `C:\Users\recla\Desktop\NEURON\Update\neuron-project`
- **Installazione attiva (server MCP):** `C:\Users\recla\AppData\Local\Programs\neuron`
  — copia deployata manualmente; va risincronizzata a ogni release (vedi task deploy).
- **Codice:** `src/neuron/` — `server.py` (vero MCP server), `engine.py` (motore CLI
  standalone, usato solo da `scripts/run_interactive.py`), `models.py`, `db.py` (layer DB
  unificato a 3 livelli: Turso cloud → Turso locale → sqlite3), `registry.py`.
- **Test:** `tests/test_core.py` + `tests/test_server.py` (52 test totali).
- **Scope/spec:** `Neuron.txt`.

Versione corrente: **3.3.0**.
