"""Fase G foundation: project_id marker + path canonicalization (stdlib-only)."""
import os

from neuron import project as P


def test_init_is_idempotent_and_returns_uuid(tmp_path):
    pid = P.init_project(tmp_path)
    assert isinstance(pid, str) and len(pid) == 36
    assert P.init_project(tmp_path) == pid            # never overwrites a shared id


def test_read_and_root(tmp_path):
    pid = P.init_project(tmp_path)
    f = tmp_path / "gray_matter" / "server.py"
    f.parent.mkdir(parents=True); f.write_text("")
    assert P.read_project_id(f) == pid
    assert os.path.abspath(str(P.project_root(f))) == os.path.abspath(str(tmp_path))


def test_canonical_ref_is_posix_relative_and_shared(tmp_path):
    pid = P.init_project(tmp_path)
    f = tmp_path / "Neuron" / "src" / "neuron" / "models.py"
    f.parent.mkdir(parents=True); f.write_text("")
    ref = P.canonical_ref(f, by="claudio")
    assert ref["shared"] is True
    assert ref["path"] == "Neuron/src/neuron/models.py"      # relative, forward slashes
    assert ":" not in ref["path"] and not ref["path"].startswith("/")  # no absolute leak
    assert ref["project_id"] == pid and ref["by"] == "claudio"


def test_unmarked_file_is_local_not_shared(tmp_path):
    f = tmp_path / "loose.py"
    f.write_text("")                                  # no .neuron marker anywhere
    ref = P.canonical_ref(f)
    assert ref["shared"] is False
    assert ref["project_id"] is None                  # -> routed to per-user sidecar


def test_sidecar_dir_is_per_user(tmp_path):
    assert "path_sidecar" in str(P.sidecar_dir())


def test_canonicalize_absolute_file_ref(tmp_path):
    P.init_project(tmp_path)
    f = tmp_path / "gray_matter" / "server.py"
    f.parent.mkdir(parents=True); f.write_text("")
    refs = P.canonicalize_references(
        [{"type": "file", "path": str(f)}], by="claudio")
    assert refs[0]["path"] == "gray_matter/server.py"     # made project-relative
    assert refs[0]["shared"] is True and refs[0]["by"] == "claudio"
    assert refs[0]["project_id"]


def test_canonicalize_is_idempotent(tmp_path):
    P.init_project(tmp_path)
    f = tmp_path / "a.py"; f.write_text("")
    once = P.canonicalize_references([{"type": "file", "path": str(f)}], by="x")
    twice = P.canonicalize_references(once, by="y")        # already has project_id
    assert twice == once                                  # untouched second pass


def test_canonicalize_passthrough_url_and_relative(tmp_path):
    refs = P.canonicalize_references(
        [{"type": "url", "path": "https://x"}, {"type": "file", "path": "already/rel.py"}],
        by="z")
    assert refs[0]["path"] == "https://x"                 # url untouched
    assert refs[1]["path"] == "already/rel.py"            # relative path untouched


def test_merge_refs_dedups_by_path():
    a = [{"type": "file", "project_id": "p", "path": "x.py"}]
    b = [{"type": "file", "project_id": "p", "path": "x.py"},
         {"type": "file", "project_id": "p", "path": "y.py"}]
    merged = P.merge_refs(a, b)
    assert [r["path"] for r in merged] == ["x.py", "y.py"]  # x not duplicated


def test_render_file_refs_compact():
    refs = [{"type": "file", "path": "a.py", "by": "claudio"},
            {"type": "url", "path": "https://x"}]
    assert P.render_file_refs(refs) == ["a.py (claudio)"]   # url skipped
