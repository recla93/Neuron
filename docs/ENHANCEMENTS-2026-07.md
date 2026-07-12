# Report enhancement — ciclo "Piano 05 + proposte" (2026-07-10 → 2026-07-11)

Stato finale: **suite locale tutta verde, deploy eseguito**; le feature nuove sono live
sul server installato (il curation gate ha già corretto un store_turn reale in sessione).

## 1. Core — prestazioni e costo (T51, Piano 05/A)

| Intervento | Effetto misurato/atteso |
|---|---|
| A1 — scritture link O(attivi): `inactive_turns` derivato al load | **−98% righe scritte/turno** (bench: da 1491 a 18-38 su grafo 500 nodi/1491 link); su Turso = da O(L) a O(attivi) round-trip di rete |
| A2 — cache intra-call `_search_embeddings` (guardia weakref anti id-reuse) | niente ricerche duplicate dentro lo stesso turno (auto_link + context window + resolve) |
| A3 — pre-warm embedder in background all'avvio | primo `pre_turn` senza ~3s di model load |
| A4 — `_LOOP_HINT` one-shot + dedup stimoli in pre_turn | ~15 token risparmiati su ogni tool call dopo la prima |
| A0 — fix crash live "truth value of an array is ambiguous" | vettori numpy coerciti a lista; `pre_turn` non crasha più |
| A6 — `server_name` = slug (`neuron5`) | identità coerente v4/v5 |
| A7 — `scripts/bench_turn.py` | benchmark ripetibile righe/ms per turno |

## 2. Installer — centralizzato e auto-riparante (T52, Piano 05/B)

- **Motore unico Python** `src/neuron/clients.py` + CLI `neuron register` / `neuron doctor [--fix]`:
  merge non distruttivo, backup, verify+rollback, manifest d'installazione (`install-manifest.json`).
- **Fix dal log del collega:** snippet manuali sempre JSON valido (backslash escapati);
  Claude Desktop rilevato anche in MSIX/Store (`Packages\Claude_*`); Claude Code via
  `claude mcp add` (mai edit diretto dello state file live); entry residue/cruft rilevate.
- **Bug distruttivo eliminato:** `Register-CodexMcp` sovrascriveva l'intero `config.toml` →
  ora merge della sola sezione; verify+rollback anche su `Register-McpNested`.
- **Doctor esteso ai processi (B6b):** elenca i server `python -m neuron` vivi con provenienza
  (quale app li ha lanciati), uccide gli **orfani** con `--fix`, segnala versioni vecchie in RAM
  e doppioni per parent; v4/v5 side-by-side riconosciuto come legittimo.
- UI: voce menu "Check & repair (doctor)" nel Configuration Center; doctor automatico a fine install.
- **Test:** `tests/test_clients.py` 17/17 — i casi reali (JSONC, BOM, MSIX, TOML, cruft) sono
  regression test permanenti.

## 3. Qualità della memoria (proposte post-analisi)

- **T54 Curation gate** (`neuron/curation.py`): a scrittura, drop di verbi/frasi/path con nota
  correttiva in-context, salvataggio della parte nominale, remap dei near-duplicati sul nodo
  esistente (case/accenti/plurali EN-IT), link canonizzati e mai dangling/self. Gate *soft*:
  il turno passa se resta ≥1 keyword. 10/10 test. **Già attivo in produzione.**
- **T55 Telemetria loop:** `status` mostra pre_turn/store_turn/other per sessione + warning se
  il loop è rotto — la compliance del modello è ora misurabile.
- **T56 Episodi:** tabella `episodes(context, keyword, turn, text)` (migrazione idempotente);
  `store_turn(episode=...)` salva una frase-fatto, `pre_turn` risponde con `facts:` del top-node.
  I nodi portano decisioni, non solo temi. Cap 5/nodo, pulizia su rimozione nodi. 6/6 test.

## 4. Ecosistema client

- **Plugin Cowork `neuron-guard`** (`clients/cowork-plugin/neuron-guard/` + `.plugin` consegnato):
  hook SessionStart con handshake + 5 regole anti-misuse, skill `neuron-usage` on-demand con
  playbook e tabella errori comuni. **Stato: non ancora installato in Cowork** (verificato
  2026-07-11) — installarlo da Settings → Plugins per attivarlo.
- Anti-misuse propagato a Claude Code hook e plugin OpenCode (testo unico condiviso).

## 5. Modularizzazione (T57, ADR-006) — in corso

- `docs/design/06-server-modularization.md`: mappa moduli + regola anti-regressione
  (i test monkeypatchano attributi di `server` → split incrementale con re-export).
- Estratti finora: `curation.py` (nuovo), `clients.py` (nuovo), **`extraction.py`**
  (SemanticExtractor + costanti, ~390 righe verbatim), **`funnel.py`** (signpost, skill
  registry, reader). server.py: **~2550 → 2149 righe (−16%)**, comportamento identico
  (re-export totale, parità verificata su casi dei test reali).
- Restano: HELP_TEXT → funnel; `search.py` (richiede migrazione test che monkeypatchano
  `_srv._search_embeddings`/`_srv._db`); `stimulus.py`.

## Da fare / verificare (in ordine)
1. `pytest -q` locale dopo extraction/funnel (attesi verdi via re-export) + deploy.
2. Installare `neuron-guard.plugin` in Cowork e verificare `mcp__neuron5__help` al primo turno.
3. T57: HELP_TEXT, poi search.py con migrazione test contestuale.
4. Facoltativi: smoke episodi su Turso cloud; parse-check PS su macchina reale (se non già
   coperto dall'ultima run verde); estendere doctor a hook/plugin client.
