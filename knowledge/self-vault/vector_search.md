# vector_search — ricerca semantica tra keyword

Cerca keyword per similarità vettoriale (embedding 384-dim fastembed all-MiniLM-L6-v2 o multilingua configurato). Non traversa link — pura similarità coseno.

## Quando usarlo

- Quando serve un match "per significato" non per parola chiave.
- Come complemento a [[get_context]] (che usa link + salienza + vettori).
- Esplorazione: "cosa Neuron ha di simile a questo concetto astratto?"

## Come si usa

```
vector_search(keywords=["query"], top_n=8)
```

- La query viene embedded e confrontata coi vettori dei nodi.
- Il tier sqlite usa dot-product fallback (equivalente a coseno per vettori normalizzati).

## Vantaggio

Trova connessioni che i link non catturano: due concetti mai linkati ma semanticamente vicini emergono comunque. Motore sottostante di [[find_candidates]].

## Link

[[find_candidates]] (uso diretto) | [[get_context]] (ricerca combinata) | [[Neuron]]
