# Spreading activation — propagazione dell'attivazione nel grafo

Meccanismo di retrieval associativo: da uno o più nodi "seed" (keyword attive), l'attivazione si propaga lungo i link per `k` hop, con decadimento (`decay < 1`). I nodi raggiunti vengono ordinati per attivazione accumulata.

## Perché esiste

Il match vettoriale trova similarità semantica diretta. La spreading activation trova **connessioni indirette**: un concetto emerge non perché simile alla query, ma perché collegato a qualcosa che lo è. È ciò che rende Neuron una memoria associativa e non solo un indice.

## Come funziona

- Da ogni seed (attivazione iniziale = 1.0), propaga lungo i link per `k` hop.
- Contributo per hop = `attivazione × (forza_link / 3) × (1 + salienza / max_sal) × decay`.
- Link rinforzati da [[store_turn|rinforzo Hebbian]] propagano di più.
- Nodi salienti fungono da hub (accumulano più attivazione).
- `k=2`, `decay=0.5`, `min_activation=0.01` evitano il flooding.

## Dove viene usata

- [[pre_turn]]: il contesto caricato include nodi emersi per attivazione.
- [[flash]]: dormant pulse e creative leap sono basati su spreading activation.
- [[get_context]] depth>=2: la traversata a 2 hop è una spreading limitata.

## Link

[[pre_turn]] (consumatore principale) | [[salienza]] (amplifica l'attivazione) | [[flash]] (stimoli)
