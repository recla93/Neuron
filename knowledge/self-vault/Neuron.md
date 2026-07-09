# Neuron — semantic memory MCP server

Neuron è un server MCP (Model Context Protocol) che implementa **memoria semantica persistente** per LLM. Conserva concetti, relazioni e salienza attraverso sessioni, usando un grafo di nodi (keyword) e link tipizzati. Il retrieval è associativo: non solo match testuale, ma spreading activation, ranking composito (similitudine vettoriale + salienza + recency) e flash semantici.

## Loop a 2 step

Ogni turno sostanziale segue: [[pre_turn]] (carica contesto prima di rispondere) → rispondi → [[store_turn]] (persiste ciò che è nuovo). [[confirm]] rafforza i nodi utili. Su turni banali (saluti, yes/no) si salta tutto.

## Contesti

I dati sono organizzati in contesti gerarchici (`default`, `java/spring`, `python/django`...). Un contesto figlio eredita dai genitori ([[contesti]]). [[switch_context]] cambia il contesto attivo.

## Meccanismi chiave

- **Salienza** ([[salienza]]): ogni nodo ha un peso che cresce con l'uso e decade con l'inattività.
- **Rinforzo Hebbian**: link co-attivati in uno stesso turno si rafforzano.
- **Spreading activation**: da keyword attive, l'attivazione si propaga lungo i link ([[flash]], [[pre_turn]]).
- **Consolidation**: [[consolidate]] unisce quasi-duplicati e archivia orfani; [[prune]] rimuove link tangenziali inattivi.

## Tools

Vedi [[help]] per l'elenco completo o [[skill]] per il playbook.
