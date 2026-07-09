# Salienza — peso dinamico dei nodi

La salienza è un intero che misura quanto un concetto è "importante" nel grafo. Cresce quando il nodo viene toccato ([[store_turn]], [[confirm]], [[auto]]) e decade gradualmente con l'inattività. I nodi più salienti emergono nel ranking di [[get_context]] e come stimoli in [[pre_turn]].

## Perché esiste

La salienza distingue ciò che conta da ciò che è stato solo menzionato una volta. Un nodo con salienza alta (es. `spring boot`, `dependency injection`) riemerge nei contesti giusti anche senza match vettoriale diretto — è il meccanismo che rende Neuron una **memoria associativa**, non solo un indice.

## Come funziona

- Ogni [[store_turn]] o [[auto]] incrementa la salienza dei nodi menzionati.
- [[confirm]] aumenta la salienza di nodi specifici (feedback esplicito).
- La salienza è normalizzata nel ranking composito di [[get_context]] (peso 0.3 su max_sal).
- La consolidate preservation soglia (`protect_salience=8`) protegge i nodi più importanti dalla fusione.
- I nodi a bassa salienza senza link recenti vengono [[consolidate|archiviati]] nel graveyard.

## Strumenti correlati

- [[forgotten]] trova nodi a salienza decaduta
- [[summary]] mostra i top nodi per salienza
- [[flash]] usa la salienza per generare stimoli
