"""Tests for Turso credential sanitising/validation (connect_turso.py + db.py).

Pure stdlib — no libsql-client/mcp/fastembed needed, so these run anywhere.

Regression target: a stray newline/CR inside the auth token goes into the HTTP
header ``Authorization: Bearer <token>``; the HTTP stack rejects any header value
with a control char (header-injection guard), so EVERY connection scheme fails
identically ("every scheme failed"). ``.strip()`` only cleaned the ends, letting
an internal line break through. sanitize_credential now removes control/whitespace
chars anywhere; validate_url rejects a genuinely bad URL scheme clearly (the
token itself is validated authoritatively by the real network probe).
"""

import importlib.util
import os
import sys

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src = os.path.join(_project_root, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)


def _load_connect_turso():
    # Moved into the package (scripts/connect_turso.py is now a thin shim).
    import neuron.connect as mod
    return mod


ct = _load_connect_turso()


# --- sanitize_credential ----------------------------------------------------

def test_sanitize_removes_internal_newline():
    tok = "eyJhbGci.eyJpYXQ\n2ODk0.sig-part_123"
    clean = ct.sanitize_credential(tok)
    assert "\n" not in clean
    assert clean == "eyJhbGci.eyJpYXQ2ODk0.sig-part_123"


def test_sanitize_removes_cr_tab_space_anywhere():
    assert ct.sanitize_credential(" a\tb\r\nc ") == "abc"


def test_sanitize_preserves_valid_token_body():
    # base64url + JWT dots + '_' and '-' must survive untouched.
    tok = "eyJhbG_ciOi-Jd.ab_c-123.si_g"
    assert ct.sanitize_credential(tok) == tok


def test_sanitized_token_is_a_legal_http_header_value():
    import http.client
    tok = "Bearer_part.eyJ\n0eXAiOiJKV1Q.sig"
    clean = ct.sanitize_credential(tok)
    c = http.client.HTTPConnection("localhost")
    c.putrequest("POST", "/")
    c.putheader("Authorization", "Bearer " + clean)  # must NOT raise


# --- token preview (length-only; masking de-obscured) -----------------------

def test_preview_shows_length_only():
    assert ct._preview("eyJhbGciOi_secret_token-123") == "<27 chars>"
    assert ct._preview("") == "<empty>"


# --- validate_url -----------------------------------------------------------

def test_validate_url_accepts_libsql_and_https():
    assert ct.validate_url("libsql://db-org.turso.io") is None
    assert ct.validate_url("https://db-org.turso.io") is None


def test_validate_url_rejects_unknown_scheme():
    assert ct.validate_url("ftp://x") is not None


def test_validate_url_rejects_missing_scheme():
    assert ct.validate_url("db-org.turso.io") is not None


# --- db._clean_env (server-side path) ---------------------------------------

def test_db_clean_env_strips_internal_control_chars(monkeypatch=None):
    import importlib
    os.environ["TURSO_AUTH_TOKEN"] = "tok_en\npart-1"
    os.environ["TURSO_DATABASE_URL"] = "libsql://db-org.turso.io\n"
    import neuron.db as db
    importlib.reload(db)
    try:
        assert db.TURSO_AUTH_TOKEN == "tok_enpart-1"
        assert db.TURSO_DATABASE_URL == "libsql://db-org.turso.io"
    finally:
        os.environ.pop("TURSO_AUTH_TOKEN", None)
        os.environ.pop("TURSO_DATABASE_URL", None)
        importlib.reload(db)
