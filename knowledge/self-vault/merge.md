# merge — fusione manuale di nodi duplicati

Unisce due o più nodi in uno: sposta tutti i link degli `aliases` nel `canonical`, somma le salienze, poi elimina gli alias. Operazione inversa non esiste — decidere con cura.

## Quando usarlo

- Dopo [[find_candidates]] ha rivelato quasi-duplicati (`postgres` vs `postgresql`).
- Quando si scoprono a mano nodi che rappresentano lo stesso concetto.
- Mai su nodi con salienza alta se non si è sicuri.

## Come si usa

```
merge(canonical="keyword_corretto", aliases=["kw1", "kw2"])
```

- `canonical`: il nodo che sopravvive (keyword corretta)
- `aliases`: nodi da assorbire (vengono cancellati)

## Vantaggio

Il grafo non ha "sinonimi sparsi". Ogni concetto ha un nodo solo, con tutta la salienza e tutti i link accumulati in un posto solo.

## Link

[[find_candidates]] (chiamare prima per scoprire i duplicati) | [[consolidate]] (merge automatico) | [[store_turn]] (evitare duplicati ex-ante)
