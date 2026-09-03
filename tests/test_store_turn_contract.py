"""store_turn deve accettare la chiamata che la suite stessa prescrive.

Il loop `pre_turn` → rispondi → `store_turn` è il prodotto. Se lo STEP 2
rifiuta la forma che la guidance inietta in ogni sessione AI, la memoria non
si popola — e nessuno se ne accorge, perché il turno perso non lascia traccia.
"""

import ast
import asyncio
import pathlib

import pytest

from neuron import server as S

_SERVER_SRC = pathlib.Path(S.__file__)


def _literal(node):
    """`ast.literal_eval`, ma tollera le f-string.

    Una description che interpola una costante — `f"max {EPISODE_MAX_CHARS}
    caratteri"`, che è il modo giusto di tenere il testo allineato al codice —
    è un `JoinedStr`, non un literal: `literal_eval` alza `ValueError` e
    l'INTERO schema diventa illeggibile, quindi una descrizione di un campo
    faceva cadere i test sui campi di tutti gli altri. Qui i pezzi interpolati
    diventano `…`: questi test verificano quali CAMPI lo schema espone e cosa
    dichiarano, non i numeri dentro la prosa.
    """
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "…"
                       for v in node.values)
    if isinstance(node, ast.Dict):
        return {_literal(k): _literal(v) for k, v in zip(node.keys, node.values)}
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_literal(e) for e in node.elts]
    return ast.literal_eval(node)


def _schema_of(tool_name: str) -> dict:
    """`inputSchema` di un tool, letto dal SORGENTE.

    Non da `list_tools()`: la suite di Neuron gira con un finto modulo `mcp`
    (tests/_mockdeps.py), il cui `Tool.__init__` butta via tutti i kwargs — con
    lo stub attivo nessun attributo del tool è leggibile. Il literal nel
    sorgente è comunque la cosa che vogliamo davvero verificare, ed è leggibile
    sempre, con o senza mcp installato.
    """
    tree = ast.parse(_SERVER_SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        name = kw.get("name")
        if isinstance(name, ast.Constant) and name.value == tool_name:
            if "inputSchema" in kw:
                return _literal(kw["inputSchema"])
    raise AssertionError(f"schema di '{tool_name}' non trovato in {_SERVER_SRC}")


def _text(result) -> str:
    r = result[0] if isinstance(result, list) else result
    return getattr(r, "text", str(r))


def _call(tool: str, args: dict) -> str:
    return _text(asyncio.run(S.call_tool(tool, args)))


@pytest.fixture
def _isolated(tmp_path, monkeypatch):
    """Store isolato: questi test SCRIVONO nel grafo."""
    monkeypatch.setenv("NS_GRAPHS_DIR", str(tmp_path / "graphs"))
    monkeypatch.setenv("NEURON_NO_DOTENV", "1")
    monkeypatch.setenv("NEURON_NO_GM", "1")


def test_the_schema_requires_only_what_the_guidance_sends():
    """Il bug: lo schema esigeva topic+keywords+domain+intent+sentiment, ma
    l'hook di Claude Code, il plugin OpenCode, il playbook, il funnel e la coda
    di pre_turn dicono tutti `store_turn(topic, keywords, links)`. Un modello
    che segue le NOSTRE istruzioni mandava tre campi su cinque: o il client
    validava e la chiamata non partiva, o passava e il turno moriva su un
    KeyError. Lo schema deve chiedere ciò che la guidance promette."""
    schema = _schema_of("store_turn")
    required = set(schema.get("required", []))
    assert required == {"topic", "keywords"}, (
        f"store_turn esige {sorted(required)}: la guidance ne manda 3"
    )
    # e i campi con default devono restare offerti, non spariti
    props = schema["properties"]
    for field in ("domain", "intent", "sentiment", "links"):
        assert field in props, f"'{field}' non è più proponibile al modello"
        if field != "links":
            assert "default" in props[field], f"'{field}' opzionale senza default"


def test_the_minimal_documented_call_saves_the_turn(_isolated):
    """Esattamente la chiamata dell'hook: topic, keywords, links."""
    out = _call("store_turn", {
        "topic": "pranzo con Ada",
        "keywords": ["ada", "pranzo", "venerdi"],
        "links": [{"source": "ada", "target": "pranzo",
                   "link_type": "instance-of", "weight": "strong"}],
    })
    assert "error" not in out.lower(), out
    assert "saved" in out.lower(), out


def test_a_malformed_link_costs_the_link_not_the_whole_turn(_isolated):
    """`links` è JSON generato da un modello: la forma sbagliata è questione di
    quando, non di se. Prima un elemento non-dict sollevava AttributeError e
    faceva perdere l'intero turno — topic e keywords compresi — per un campo
    di solo arricchimento."""
    out = _call("store_turn", {
        "topic": "pranzo con Ada",
        "keywords": ["ada", "pranzo"],
        "links": [["ada", "pranzo"]],          # forma sbagliata, plausibile
    })
    assert "saved" in out.lower(), out
    assert "malformed link" in out.lower(), "lo scarto dev'essere DETTO, non silenzioso"


def test_what_was_stored_comes_back_on_the_next_pre_turn(_isolated):
    """Il giro completo che è il prodotto: salvo un concetto, e al turno dopo
    il contesto me lo ripropone. Senza questo, tutto il resto è decorazione."""
    _call("store_turn", {"topic": "pranzo con Ada",
                         "keywords": ["ada", "pranzo", "venerdi"]})
    recall = _call("pre_turn", {"topic": "ada", "keywords": ["ada"]})
    assert "ada" in recall.lower(), f"il concetto salvato non riemerge: {recall}"


def test_a_truncated_episode_is_declared_in_the_response(_isolated):
    """La perdita la dichiara la risposta della SCRITTURA, non un log.

    `add_episode` compila il report (testato in test_episodes.py) e lo schema
    del tool promette che si torna indietro in `episode_lost`. Ma il blocco che
    lo emetteva stava nel return di `_tool_auto`, dove quel nome non esiste: il
    report era dead code qui e un NameError là. Il modello superava il cap senza
    saperlo."""
    from neuron.models import EPISODE_MAX_CHARS

    clean = _call("store_turn", {
        "topic": "episodi", "keywords": ["retry backoff"],
        "episode": "scelto https su wss",
    })
    assert "episode_lost" not in clean, "niente perso, niente da dichiarare"

    over = _call("store_turn", {
        "topic": "episodi", "keywords": ["retry backoff"],
        "episode": "x" * (EPISODE_MAX_CHARS + 17),
    })
    assert "episode_lost" in over, over
    assert '"truncated": 17' in over, over


def test_auto_answers_at_all(_isolated):
    """`auto` non era chiamato da NESSUN test: ha girato con un NameError nel
    return (`_episode_report`, locale di store_turn) senza che la suite se ne
    accorgesse. Uno smoke test sul tool basta a impedire il bis."""
    out = _call("auto", {"text": "oggi ho scelto https su wss per Turso"})
    assert "error" not in out.lower(), out
    assert "extraction" in out, out


def test_defaults_are_applied_not_just_tolerated(_isolated):
    """Un turno senza domain non deve finire in un dominio vuoto: 'general' è
    un default vero, non un buco."""
    _call("store_turn", {"topic": "una cosa qualsiasi",
                         "keywords": ["cosa", "qualsiasi"]})
    status = _call("status", {})
    assert "error" not in status.lower(), status


def test_domain_declares_that_it_switches_the_context():
    """`domain` non è un'etichetta: due turni consecutivi con lo stesso valore
    non-'general' spostano il contesto attivo, e i turni successivi finiscono in
    un ALTRO grafo.

    La descrizione diceva solo "free-form topic label... ANY label works", e chi
    chiama non poteva sapere il resto. Osservato il 2026-08-17: una sessione di
    lavoro continua passata a `domain="AI"` due volte si è divisa fra il grafo
    `default` (154 nodi di storia del progetto) e `ai` (il lavoro di quel
    giorno), e nessuno dei due conteneva la sessione intera. Il meccanismo va
    bene ed ha già l'isteresi; quello che mancava era dirlo a chi chiama.
    """
    desc = _schema_of("store_turn")["properties"]["domain"]["description"]
    low = desc.lower()
    assert "switch" in low, f"la conseguenza non è dichiarata: {desc}"
    assert "consecutive" in low, "manca la condizione che fa scattare lo switch"
    assert "general" in low, "manca come restare nel contesto corrente"


def test_a_bad_weight_is_rejected_at_the_boundary(_isolated):
    """Regression 2026-08-25: an out-of-enum `weight` ("Strong", "high") passed
    validation, `add_link` mutated the graph and THEN WEIGHT_ORDER[lk.weight]
    raised KeyError → whole turn lost, graph half-mutated. Enums must be
    enforced at the boundary, before any mutation."""
    out = _call("store_turn", {
        "topic": "peso sbagliato",
        "keywords": ["ada", "pranzo"],
        "links": [{"source": "ada", "target": "pranzo", "weight": "Strong"}],
    })
    assert "validation error" in out.lower(), out
    assert "weight" in out.lower(), out

    bad_type = _call("store_turn", {
        "topic": "tipo sbagliato",
        "keywords": ["ada", "pranzo"],
        "links": [{"source": "ada", "target": "pranzo",
                   "link_type": "causes-effect"}],
    })
    assert "validation error" in bad_type.lower(), bad_type

    # e il turno NON è salvato: le keywords non esistono nel grafo
    recall = _call("pre_turn", {"topic": "peso sbagliato", "keywords": ["pranzo"]})
    assert "peso sbagliato" not in recall.lower() or "no context" in recall.lower()


def test_dismiss_and_confirm_survive_hostile_numbers(_isolated):
    """boost/penalty/trust_penalty come from the model: negatives (a NEGATIVE
    penalty used to INCREASE salience), strings, absurd floats. None of the
    three may crash the tool or move the numbers the wrong way."""
    _call("store_turn", {"topic": "feedback", "keywords": ["ada"]})

    ok = _call("confirm", {"keywords": ["ada"], "boost": -50})
    assert '"boost": 0' in ok, ok                      # clamped to 0, not -50

    weird = _call("confirm", {"keywords": ["ada"], "boost": "abc"})
    assert '"boost": 2' in weird, weird                # default, no crash

    dis = _call("dismiss", {"keywords": ["ada"], "penalty": "abc",
                            "trust_penalty": -5})
    assert '"dismissed"' in dis.lower(), dis           # defaults applied, no crash


def test_pre_turn_serves_a_ready_confirm_call(_isolated):
    """Reinforcement enforcement (2026-08-25): confirm must not depend on the
    model's memory. When pre_turn serves content, the reply carries the ALREADY
    WRITTEN call with the exact served keywords."""
    _call("store_turn", {"topic": "pranzo con Ada",
                         "keywords": ["ada", "pranzo", "venerdi"]})
    recall = _call("pre_turn", {"topic": "ada", "keywords": ["ada"]})
    assert "confirm(keywords=" in recall, recall
    # suggested keywords are this turn's served ones, not a generic placeholder
    suggerite = recall.split("confirm(keywords=")[1].split(")")[0]
    assert '"ada"' in suggerite, f"suggested: {suggerite}, expected 'ada'"


def test_the_hint_has_a_kill_switch(_isolated, monkeypatch):
    """NEURON_CONFIRM_HINT=0 restores the previous behaviour: no line, no cost.
    A feature without a switch is a dependency."""
    monkeypatch.setenv("NEURON_CONFIRM_HINT", "0")
    _call("store_turn", {"topic": "pranzo con Ada",
                         "keywords": ["ada", "pranzo"]})
    recall = _call("pre_turn", {"topic": "ada", "keywords": ["ada"]})
    assert "confirm(keywords=" not in recall, recall


def test_confirm_has_a_cooldown(_isolated):
    """With the pre_turn hint, confirming costs zero effort: without anti-bounce
    a reflexive model inflates salience/trust on the same node every turn and
    dilutes the signal. Second confirmation in the same turn -> cooled, not
    applied."""
    _call("store_turn", {"topic": "pranzo con Ada",
                         "keywords": ["ada", "pranzo"]})
    first = _call("confirm", {"keywords": ["ada"]})
    assert '"cooled"' not in first, first

    again = _call("confirm", {"keywords": ["ada"]})
    assert '"cooled": ["ada"]' in again, again

    # refutes bypass the cooldown: downgrading must always remain possible
    dis = _call("dismiss", {"keywords": ["ada"], "penalty": 1})
    assert '"dismissed"' in dis.lower(), dis
