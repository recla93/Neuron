# recall — riporta in vita un nodo archiviato

Rimette nel grafo attivo un nodo finito nel cimitero (graveyard). L'archiviazione non cancella: un concetto dimenticato resta recuperabile finché qualcosa lo richiama.

## Quando usarlo

- Quando uno stimolo di memoria dormiente fa riaffiorare un concetto archiviato che torna utile adesso.
- Quando [[forgotten]] mostra che un concetto stava decadendo e la conversazione ci torna sopra.
- **Non** per ricreare da zero un concetto: se non è mai esistito serve [[store_turn]], non `recall`.

## Come si usa

```
recall(keyword="nome-del-nodo")
```

## Vantaggio

Il decadimento è utile solo se è reversibile. Senza `recall` la potatura sarebbe una scommessa irreversibile su cosa conterà fra dieci turni; con `recall` diventa una scelta di priorità, che si può disfare.

## Link

[[forgotten]] (trova i concetti in decadimento) | [[prune]] (cosa li porta via) | [[salienza]] (il criterio del decadimento) | [[store_turn]] (per un concetto nuovo) | [[Neuron]]
