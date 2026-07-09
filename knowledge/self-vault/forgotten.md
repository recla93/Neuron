# forgotten — trova concetti in decadimento

Cerca keyword non toccate da N turni — la salienza sta calando, stanno per essere dimenticate. Utile per riscoprire concetti persi o decidere se salvarli con [[confirm]].

## Quando usarlo

- Periodicamente, per riportare alla luce conoscenze residue.
- Prima di [[consolidate]]: decidere cosa proteggere dall'archiviazione.
- Curiosità: "cosa Neuron sta dimenticando di me?"

## Come si usa

```
forgotten(threshold=5, top_n=10)
```

- `threshold`: turni di inattività (default 5)
- `top_n`: quanti mostrare (default 10)

## Vantaggio

Ogni memoria ha un oblio naturale. `forgotten` rende l'oblio **trasparente** e **reversibile**: puoi [[confirm]] i nodi che vuoi trattenere o lasciarli decadere.

## Link

[[summary]] (include `forgotten`) | [[confirm]] (salva dall'oblio) | [[consolidate]] (archivia orfani) | [[salienza]]
