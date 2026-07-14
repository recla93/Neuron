# skill — carica il playbook completo di Neuron

Restituisce il testo integrale di una skill/playbook Neuron. Il playbook contiene il workflow completo PRE/POST, regole di curatela, best practice, e reference di tutti i tool.

## Quando usarlo

- Dopo [[help]]: quando l'indice non basta e serve la guida completa.
- Una volta per sessione: caricare la skill e usarla come reference.
- Nomi validi: `playbook` (consigliato), `curated`.
- Token-cheap: caricata on-demand, non always-on.

## Come si usa

```
skill(name="playbook")
```

- `playbook`: workflow completo PRE+POST (uso normale)
- `curated`: regole per grafo pulito (concept nouns, link tipati, no self-link)

## Vantaggio

La guida completa quando serve, zero token quando non serve. È la documentazione just-in-time.

## Link

[[help]] (indice prima di skill) | [[Neuron]] | [[pre_turn]] (cosa dice il playbook)
