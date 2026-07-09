# get_context — recupero esplicito di nodi e link collegati

Recupera ciò che Neuron già sa su un topic — nodi collegati, link, attivazione. Versione esplicita e controllabile di ciò che [[pre_turn]] fa in automatico.

## Quando usarlo

- Quando [[pre_turn]] ha restituito poco e serve più profondità.
- Per esplorazione laterale: `depth=2` traversa 2 hop nel grafo (link dei link).
- Per cercare connessioni cross-contesto (`depth>=3` include i drift link).
- Nel caricamento normale a inizio turno, preferire `pre_turn` (più efficiente).

## Come si usa

```
get_context(topic="concetto", keywords=["kw"], depth=1, format="compact", max_tokens=400)
```

- `depth`: 1 (diretto) / 2 (link dei link) / 3 (include drift cross-contesto)
- `format`: "compact" per injection silenziosa, "full" per leggibilità
- I risultati ereditati dai contesti genitori sono annotati `(from: <parent>)`

## Vantaggio

Più granulare di `pre_turn`: controlli profondità e formato. A `depth=3` emergono connessioni lontane e cross-dominio che il retrieval piatto non vede.

## Link

[[pre_turn]] (versione compatta automatica) | [[contesti]] (ereditarietà) | [[salienza]] (ranking) | [[flash]] (stimoli associativi)
