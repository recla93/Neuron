# consolidate — pulizia del grafo: merge duplicati + archivia orfani

Operazione di manutenzione che: unisce nodi quasi-duplicati (similarità coseno > soglia), archivia i nodi orfani a bassa salienza in un `_graveyard` recuperabile, e ricompatta i link.

## Quando usarlo

- Periodicamente (ogni 20-30 turni) per tenere il grafo pulito.
- Dopo import massivo o sessioni lunghe con molto [[auto]] (rumore).
- Può girare automaticamente se `NS_CONSOLIDATE_AUTO=1` (dopo ogni [[store_turn]]).
- Sicuro: idempotente, mai distruttivo (tutto finisce nel graveyard).

## Come si usa

```
consolidate(sim_threshold=0.85, drop_orphans=True, orphan_salience=2, orphan_inactive=10)
```

- `sim_threshold`: cosine per fondere (default 0.85)
- `drop_orphans`: archivia nodi senza link e bassa salienza
- I nodi con salienza >= 8 sono protetti dalla fusione

## Vantaggio

Grafo più piccolo, più denso, link più significativi. I quasi-duplicati non diluiscono il retrieval. L'archiviazione è recuperabile — non è cancellazione.

## Link

[[prune]] (link tangenziali) | [[merge]] (merge manuale) | [[forgotten]] (cosa sta morendo) | [[auto]] (la causa del rumore)
