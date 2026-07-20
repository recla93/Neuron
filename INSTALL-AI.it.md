# INSTALL-AI — Neuron (memoria semantica persistente)

> Istruzioni per un agente AI. Neuron può lavorare standalone, ma il modello
> raccomandato è dietro il gateway Gray Matter (bundle-ato in ogni install).

## Percorso raccomandato (via Gray Matter)

```bash
pip install -e .                       # dal root di Neuron
pip install -e ../gray_matter          # il gateway, se presente nel checkout
gray-matter install --dry-run
gray-matter install                    # registra SOLO gray-matter nei client
gray-matter doctor                     # verifica: solo gray-matter listato
```

Gli hook di sessione (claude-code/cowork/opencode) li deploya `gray-matter
install`; gli altri client ricevono le `instructions` MCP all'handshake.

## Standalone (senza Gray Matter)

```bash
pip install -e .
neuron register                        # registra neuron5 nei client rilevati
python -m pytest tests -q              # attesi tutti verdi
```

Riavvia le app AI dopo la registrazione.

## Dati e rollback

- Grafo: `<data-dir>/neuron5/graphs/graph_<context>.db` (mai toccato dall'install).
- Cloud Turso opzionale: credenziali in `.env` (vedi `.env.example`); commentate = local mode.
- Deregistrazione: `gray-matter uninstall` (interattivo) o ripristino dei `.bak`.
- Loop d'uso per l'agente: `pre_turn` prima di rispondere, `store_turn` dopo —
  playbook completo nel tool `skill`/`help`.
