"""The pivot language (2026-11-12): facts are written in English whatever the
chat language. A fact in another language is kept, with a one-line correction."""
import asyncio

import pytest

from neuron.curation import foreign_language


@pytest.mark.parametrize("text, foreign", [
    ("Dopo un push la CI la controlla l'utente: non va sorvegliata ne' rilanciata.", True),
    ("El usuario revisa la CI después de cada push y no hay que vigilarla.", True),
    ("Der Benutzer prüft die CI nach dem Push und das ist nicht zu beobachten.", True),
    ("Le daemon est arrêté pour que les tests ne touchent pas la mémoire.", True),
    ("After a push the user checks CI: do not watch or rerun it.", False),
    ("fix: daemon log locked on purge -> retry rmtree 30s", False),        # a gist
    ("GM_INSTANCE suffix in ipcauth.channel_address", False),              # identifiers only
    ("ok", False),
])
def test_foreign_language(text, foreign):
    assert foreign_language(text) is foreign


def test_a_fact_in_another_language_is_stored_with_a_correction(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from neuron import server as S
    from neuron.registry import GraphRegistry
    monkeypatch.setattr(S, "_g", GraphRegistry(graphs_dir=str(tmp_path)))
    monkeypatch.setattr(S, "TURSO_ENGINE", False)
    call = lambda facts: asyncio.run(S.call_tool("store_turn", {"facts": facts}))[0].text  # noqa: E731
    it = call([{"kind": "habit", "text": "Dopo un push la CI la controlla l'utente, non va rilanciata.",
                "concepts": ["ci"]}])
    assert "1 saved" in it and "English" in it, it                       # kept, never refused
    en = call([{"kind": "habit", "text": "After a push the user checks CI; never rerun it.",
                "concepts": ["ci"]}])
    assert "English" not in en.split("facts:")[1], en
