# introspect — il modello che Neuron ha di sé

Restituisce in JSON cosa la memoria sa di se stessa: concetti più forti e più affidabili, crescita recente, dominio più debole, aderenza al loop [[pre_turn]] → [[store_turn]].

## Quando usarlo

- Per capire **se il loop viene rispettato davvero**, invece di andare a sensazione: il conteggio dice quante `pre_turn` e quante `store_turn` sono passate da questo processo.
- Per scoprire quale dominio è rimasto indietro prima di decidere cosa approfondire.
- Prima di una potatura o di un consolidamento, per vedere dove il grafo è denso e dove è sottile.

## Come si usa

```
introspect()
```

## Vantaggio

È l'unico tool che risponde su Neuron invece che sul contenuto. [[status]] dice quanto è grande il grafo; `introspect` dice se sta crescendo bene, e dove no.

## Attenzione

Il conteggio del loop è **per processo**, non per sessione del client: se il worker viene riavviato, riparte da zero anche se la conversazione continua.

## Link

[[status]] (stato del grafo, non del suo uso) | [[summary]] (contenuto, non struttura) | [[pre_turn]] e [[store_turn]] (il loop che misura) | [[salienza]] | [[Neuron]]
