# find_candidates — screening duplicati prima di salvare

Cerca keyword esistenti simili a quelle che stai per salvare (vector search, 384-dim). Da chiamare **prima** di [[store_turn]] per evitare duplicati.

## Quando usarlo

- **Sempre prima di `store_turn`** se il grafo è cresciuto e potrebbero esistere keyword simili.
- Quando non sei sicuro se un concetto è già presente con un nome diverso.
- `store_turn` non lo fa automaticamente — sei tu a decidere se fondere o tenere separato.

## Come si usa

```
find_candidates(keywords=["kw1", "kw2"], top_n=5)
```

- Ritorna keyword candidate con similarità coseno.
- Se un candidato ha similarità > 0.85, considera di riusare il nodo esistente o chiamare [[merge]].

## Vantaggio

Previene la proliferazione di quasi-duplicati (`postgres` vs `postgresql`). Grafo più pulito → retrieval migliore, link più significativi.

## Link

[[merge]] (seguente: unisce i candidati simili) | [[store_turn]] (chiamare dopo) | [[vector_search]] (motore sottostante)
