# flash — attiva/disattiva i flashback semantici

Toggle per i flash semantici: stimoli associativi che Neuron genera durante il [[pre_turn]]. Quando attivi, il `pre_turn` può restituire "flash": nodi dormienti emersi per spreading activation, link cross-dominio, o percorsi creativi a 2 hop.

## Quando usarlo

- **Attivo di default**: lascia che il grafo "parli" e sorprenda.
- Disattivare in contesti dove serve massima precisione e zero distrazione (es. debugging, data entry).

## Meccanismi dei flash (sotto il cofano)

1. **Dormant pulse**: nodo saliente ma non toccato da molti turni che emerge per attivazione.
2. **Cross-domain spark**: link drift verso un altro contesto.
3. **Creative leap**: percorso a 2 hop verso un dominio diverso.

## Vantaggio

È ciò che rende Neuron una memoria "viva" e non un DB. Le connessioni inaspettate sono spesso le più utili.

## Link

[[pre_turn]] (dove i flash appaiono) | [[salienza]] (determina i dormant pulse) | [[get_context]] (depth=3 per esplorare flash)
