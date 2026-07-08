"""E0.1 — configurable embedding model/dimension.

- VECTOR_DIM is read from NS_EMBED_DIM (default 384) — pure stdlib, runs anywhere.
- NS_EMBED_MODEL selects the fastembed model (default = the English all-MiniLM-L6-v2
  for backward compatibility). The dimension guard in _get_embedding trips when a
  model's width disagrees with VECTOR_DIM. These need the server module (mcp), so
  they importorskip.
"""

import importlib
import os
import sys

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src = os.path.join(_project_root, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)


# --- VECTOR_DIM via NS_EMBED_DIM (no heavy deps) ----------------------------

def test_vector_dim_default_is_384():
    os.environ.pop("NS_EMBED_DIM", None)
    import neuron.models as m
    importlib.reload(m)
    assert m.VECTOR_DIM == 384


def test_vector_dim_env_override():
    os.environ["NS_EMBED_DIM"] = "256"
    try:
        import neuron.models as m
        importlib.reload(m)
        assert m.VECTOR_DIM == 256
    finally:
        os.environ.pop("NS_EMBED_DIM", None)
        import neuron.models as m
        importlib.reload(m)  # restore default for other tests


# --- NS_EMBED_MODEL selection + dim guard (need the server module) ----------

def test_embed_model_default_is_multilingual():
    # ADR-001: default flipped to the 384-dim multilingual model (EN+IT).
    pytest.importorskip("mcp")
    pytest.importorskip("fastembed")
    os.environ.pop("NS_EMBED_MODEL", None)
    import neuron.server as srv
    importlib.reload(srv)
    assert srv.NS_EMBED_MODEL == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def test_embed_model_env_override():
    pytest.importorskip("mcp")
    pytest.importorskip("fastembed")
    os.environ["NS_EMBED_MODEL"] = "intfloat/multilingual-e5-small"
    try:
        import neuron.server as srv
        importlib.reload(srv)
        assert srv.NS_EMBED_MODEL == "intfloat/multilingual-e5-small"
    finally:
        os.environ.pop("NS_EMBED_MODEL", None)
        import neuron.server as srv
        importlib.reload(srv)


def test_dim_mismatch_raises_clear_error():
    pytest.importorskip("mcp")
    pytest.importorskip("fastembed")
    import neuron.server as srv
    importlib.reload(srv)

    class _FakeEmbedder:
        def embed(self, texts):
            # wrong width on purpose (VECTOR_DIM is 384)
            return [[0.0] * 7 for _ in texts]

    srv._embedder = _FakeEmbedder()
    srv._embed_dim_checked = False
    with pytest.raises(RuntimeError) as exc:
        srv._get_embedding("hello")
    msg = str(exc.value)
    assert "7-dim" in msg and "VECTOR_DIM" in msg
