"""La wheel di Gray Matter vendorata dentro il package non deve invecchiare.

`_gm_vendor/gray_matter-*.whl` esiste per un solo motivo: far funzionare
`neuron gui` su una macchina SENZA rete. È un artefatto congelato al momento
della release di Neuron, e il pyproject stesso lo dice ("va ricostruito a ogni
release di GM"). Finché veniva provata PRIMA di PyPI e GitHub, una macchina con
rete perfettamente funzionante finiva con una Gray Matter vecchia installata —
il "prende wheel vecchie se presenti nel PC" segnalato sul campo.

Due regole, quindi: resta l'ULTIMA risorsa, e non può essere più vecchia della
GM che le sta accanto nel checkout.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]           # .../neuron
VENDOR = ROOT / "src" / "neuron" / "_gm_vendor"
MAIN = ROOT / "src" / "neuron" / "__main__.py"


def _wheel_version() -> str | None:
    whls = sorted(VENDOR.glob("gray_matter-*.whl"))
    if not whls:
        return None
    m = re.match(r"gray_matter-([0-9][^-]*)-", whls[-1].name)
    return m.group(1) if m else None


def _sibling_gm_version() -> str | None:
    """Versione della GM accanto a noi nel checkout (in CI c'è sempre)."""
    for cand in (ROOT.parent / "gray_matter", ROOT.parent / "gray-matter"):
        toml = cand / "pyproject.toml"
        if not toml.exists():
            continue
        for line in toml.read_text(encoding="utf-8").splitlines():
            m = re.match(r'^\s*version\s*=\s*"(.+?)"', line)
            if m:
                return m.group(1)
    return None


def _tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def test_vendored_wheel_is_the_last_candidate():
    """Se torna davanti a PyPI/GitHub, il bug di regressione è tornato."""
    src = MAIN.read_text(encoding="utf-8")
    i_vendor = src.find('"wheel vendorata')
    i_pypi = src.find('"indice pip"')
    if i_vendor < 0:
        pytest.skip("nessuna wheel vendorata in questo checkout")
    assert i_pypi >= 0, "il candidato PyPI è sparito"
    assert i_vendor > i_pypi, (
        "la wheel vendorata viene provata PRIMA dell'indice pip: una macchina "
        "con rete si ritrova installata una Gray Matter congelata")


def test_vendored_wheel_is_offline_only():
    """--no-index, o non è un fallback offline: è una scorciatoia che può
    risolvere da PyPI di nascosto e mascherare il candidato precedente."""
    src = MAIN.read_text(encoding="utf-8")
    if '"wheel vendorata' not in src:
        pytest.skip("nessuna wheel vendorata in questo checkout")
    tail = src[src.find('"wheel vendorata'):]
    assert "--no-index" in tail[:400]


def test_vendored_wheel_is_not_older_than_the_sibling_gm():
    wheel = _wheel_version()
    sibling = _sibling_gm_version()
    if wheel is None:
        pytest.skip("nessuna wheel vendorata in questo checkout")
    if sibling is None:
        pytest.skip("nessuna Gray Matter accanto: niente con cui confrontare")
    assert _tuple(wheel) >= _tuple(sibling), (
        f"la wheel vendorata è {wheel} ma Gray Matter è {sibling}: ricostruiscila "
        f"(vedi RELEASE-CHECKLIST) o un install offline parte già vecchio")
