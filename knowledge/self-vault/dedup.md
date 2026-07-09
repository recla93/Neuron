# dedup — attiva/disattiva deduplicazione automatica

Toggle per la deduplicazione automatica delle keyword all'inserimento. Quando attiva, Neuron cerca automaticamente keyword simili prima di crearne di nuove.

## Quando usarlo

- Attivare in contesti rumorosi (molto [[auto]], sessioni multi-utente).
- Disattivare in contesti dove la precisione lessicale è critica (nomi di API, versione esatte).
- Di default è spenta per non interferire con scelte deliberate.

## Vantaggio

Automatizza ciò che [[find_candidates]] + [[merge]] fanno a mano. Comodo ma meno controllabile.

## Link

[[find_candidates]] | [[merge]] | [[consolidate]]
