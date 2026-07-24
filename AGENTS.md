# Neuron — Project Memory

Memoria persistente del progetto Neuron (semantic memory MCP server). Questo file viene
letto automaticamente all'avvio di ogni chat: contiene le regole di lavoro sulle task.

---

## Struttura progetto (riferimento rapido)

- **Repo sorgente (sviluppo):** `C:\Users\recla\Desktop\Gray Matter Enviroment\neuron`
  — sibling di `neurag` e `gray_matter`; la cartella contenitore NON è un repo git,
  i tre tool sì (repo separati, tool standalone).
- **Installazione attiva (server MCP):** venv condiviso creato da Gray Matter in
  `%LOCALAPPDATA%\gray-matter\.venv` (`pip install` dal sorgente, non copia manuale).
  Standalone senza GM: `%LOCALAPPDATA%\neuron\.venv`.
- **Codice:** `src/neuron/` — `server.py` (MCP server), `engine.py` (CLI standalone),
  `models.py`, `db.py` (3 livelli: Turso cloud → Turso locale → sqlite3), `registry.py`.
- **Asset handshake:** `src/neuron/clients/` — hook Claude Code, plugin Cowork e OpenCode.
  Viaggiano nel wheel, deployati dall'installer GM.
- **Test:** `tests/` (37 file, **272 test**) — `python -m pytest tests -q`.
- **Wheel offline:** `vendor/` — pyturso cp310→cp314 win_amd64 (`--find-links`).

Versione corrente: **6.1.0** (vedi CHANGELOG.md).

---

## ⚙️ Workflow task

### 1. All'avvio di ogni chat — importare le task
All'inizio di ogni nuova chat, **leggere `TASKLIST.md`**. Per ogni task, importare titolo, descrizione e stato nella task list della chat.

### 2. Allineamento bidirezionale chat ↔ TASKLIST.md
`TASKLIST.md` è la **fonte di verità persistente** tra una chat e l'altra.
Aggiornare subito TASKLIST.md quando si cambia lo stato di una task in chat.

### 3. Formato di ogni task
Ogni task deve avere: titolo sintetico, descrizione esaustiva, file rilevanti.
Template in cima a `TASKLIST.md`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
