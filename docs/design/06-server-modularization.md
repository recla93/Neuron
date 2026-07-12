# ADR-006 — Modularizzazione di server.py

Stato: **fase 1 attuata** (nuovo codice nasce in moduli); split completo pianificato.

## Problema
`server.py` ~2550 righe: estrazione semantica, embedding/ricerca, motore stimoli,
25+ handler tool, signpost/skill funnel, entrypoint. Costo cognitivo alto, diff
rumorosi, test tutti agganciati a un solo namespace.

## Vincolo chiave (perché NON un big-bang)
La suite monkeypatcha attributi di `server` (`_srv._search_embeddings`,
`_srv._db.connect_local`, `_srv.TURSO_ENGINE`, `_srv._seed_usable`, ...). Se una
funzione si sposta in `neuron/search.py` e i call-site interni usano i nomi del
nuovo modulo, il patch su `server.X` non intercetta più nulla → rotture silenziose
(test verdi che non testano). Lo split va fatto UN modulo alla volta, migrando i
test dello stesso modulo nello stesso commit.

## Mappa moduli target

| Modulo | Contenuto (da server.py) | Dipendenze |
|---|---|---|
| `curation.py` ✅ (fase 1) | gate keywords/link, dup-key, note correttive | stdlib |
| `extraction.py` | `SemanticExtractor`, STOP_WORDS, `_fold_accents`, `_auto_extract` | stdlib |
| `search.py` | `_get_embedder/_get_embedding/_embed_one`, cache embed, `_search_embeddings`, seed conn cache, `_refine_domain` | fastembed, db, models |
| `stimulus.py` | `_build_context_window`, `_stimulus_block`, flash, `_detect_topic_shift`, `_auto_link` | models, search |
| `funnel.py` | SIGNPOST, `_build_signpost`, `_SKILLS`/`_read_skill`, HELP_TEXT | stdlib |
| `server.py` (resta) | `app`, list_tools/schemas, `call_tool` wrapper+impl, telemetry, entrypoint | tutti |

## Regole di migrazione (per ciascun modulo, in ordine)
1. Spostare il codice nel modulo nuovo; in `server.py` importare i SIMBOLI e
   mantenere alias (`_search_embeddings = search._search_embeddings`) SOLO
   finché i call-site interni non sono migrati.
2. Migrare i test del blocco: patchare il modulo nuovo, non `server`.
3. Rimuovere gli alias quando nessun test punta più a `server.<simbolo>`.
4. Un modulo per PR/commit; suite completa verde a ogni passo.

Ordine consigliato: `extraction` (autocontenuta, molti test dedicati) →
`funnel` (stringhe pure) → `search` → `stimulus` (dipende da search).

## Non-goal
Nessun cambiamento di comportamento; nessun rename dei tool; `engine.py`
resta fuori scope (decisione A8 separata).
