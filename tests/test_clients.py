"""Tests for the central client-registration engine (Piano 05 / Parte B).

Pure stdlib — these encode the colleague's real-machine failure modes as
permanent regression tests: JSONC configs, BOM, MSIX Claude Desktop path,
TOML with other servers, cruft entries pointing at dead venvs, and manual
snippets that must be valid JSON (escaped backslashes).
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neuron import clients as C  # noqa: E402


PY = sys.executable   # an executable that certainly exists on disk


class TestJsoncRead(unittest.TestCase):
    def test_strip_comments_and_trailing_commas(self):
        raw = (
            '{\n'
            '  // user comment\n'
            '  "a": "value // not a comment",\n'
            '  /* block */\n'
            '  "b": [1, 2,],\n'
            '}\n'
        )
        data = json.loads(C.strip_jsonc(raw))
        self.assertEqual(data["a"], "value // not a comment")
        self.assertEqual(data["b"], [1, 2])

    def test_load_config_kinds(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "cfg.json")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write('{"x": 1}')
            self.assertEqual(C.load_config(p), ({"x": 1}, "json"))
            with open(p, "w", encoding="utf-8") as fh:
                fh.write('{"x": 1, // hey\n}')
            data, kind = C.load_config(p)
            self.assertEqual((data, kind), ({"x": 1}, "jsonc"))
            with open(p, "w", encoding="utf-8") as fh:
                fh.write('not json at all {{{')
            self.assertEqual(C.load_config(p)[1], "invalid")
            self.assertEqual(C.load_config(os.path.join(td, "nope.json"))[1], "missing")

    def test_bom_tolerated(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "cfg.json")
            with open(p, "wb") as fh:
                fh.write(b'\xef\xbb\xbf{"x": 1}')
            self.assertEqual(C.load_config(p), ({"x": 1}, "json"))

    def test_save_json_writes_no_bom(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "cfg.json")
            C.save_json(p, {"x": 1})
            with open(p, "rb") as fh:
                self.assertFalse(fh.read().startswith(b"\xef\xbb\xbf"))


class TestManualSnippet(unittest.TestCase):
    def test_snippet_is_valid_json_with_windows_paths(self):
        # The colleague's log printed  "C:\Users\giuse\..." unescaped → invalid.
        entry = {"command": r"C:\Users\giuse\AppData\Local\Programs\neuron5\.venv\Scripts\python.exe",
                 "args": ["-m", "neuron"]}
        snip = C.manual_snippet(["mcpServers"], "neuron5", entry)
        parsed = json.loads(snip)   # must not raise
        self.assertIn("giuse", parsed["mcpServers"]["neuron5"]["command"])


class TestTomlUpsert(unittest.TestCase):
    def test_append_preserves_other_sections(self):
        old = '[mcp_servers.obsidian]\ncommand = "obs"\n'
        new = C.toml_upsert_section(old, "mcp_servers.neuron5",
                                    C.codex_entry_lines(r"C:\v\python.exe"))
        self.assertIn("[mcp_servers.obsidian]", new)
        self.assertIn("[mcp_servers.neuron5]", new)
        self.assertIn('command = "C:\\\\v\\\\python.exe"', new)

    def test_replace_only_own_section(self):
        old = ('[mcp_servers.neuron5]\ncommand = "old"\n\n'
               '[mcp_servers.other]\ncommand = "keep"\n')
        new = C.toml_upsert_section(old, "mcp_servers.neuron5",
                                    C.codex_entry_lines("newpy"))
        self.assertNotIn('"old"', new)
        self.assertIn('command = "keep"', new)
        self.assertEqual(new.count("[mcp_servers.neuron5]"), 1)


class TestClaudeDesktopCandidates(unittest.TestCase):
    def test_msix_path_probed(self):
        with tempfile.TemporaryDirectory() as td:
            appdata = os.path.join(td, "Roaming")
            localapp = os.path.join(td, "Local")
            msix = os.path.join(localapp, "Packages", "Claude_pzs8sxrjxfjjc",
                                "LocalCache", "Roaming", "Claude")
            os.makedirs(msix)
            with open(os.path.join(msix, "claude_desktop_config.json"), "w") as fh:
                fh.write("{}")
            with mock.patch.dict(os.environ, {"APPDATA": appdata,
                                              "LOCALAPPDATA": localapp}):
                cands = C.claude_desktop_candidates()
            hits = [p for p in cands if os.path.exists(p)]
            self.assertEqual(len(hits), 1)
            self.assertIn("Claude_pzs8sxrjxfjjc", hits[0])

    def test_pick_most_recent_when_both_exist(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = os.path.join(td, "a.json"), os.path.join(td, "b.json")
            for p in (a, b):
                with open(p, "w") as fh:
                    fh.write("{}")
            os.utime(a, (1, 1))            # a is old
            chosen, existing = C.pick_existing([a, b])
            self.assertEqual(chosen, b)
            self.assertEqual(sorted(existing), sorted([a, b]))


class TestRegister(unittest.TestCase):
    def _cursor_env(self, td):
        """Route the 'cursor' client into a temp HOME."""
        return mock.patch.dict(os.environ, {"HOME": td, "USERPROFILE": td})

    def test_register_creates_and_merges(self):
        with tempfile.TemporaryDirectory() as td, self._cursor_env(td):
            with mock.patch("os.path.expanduser",
                            side_effect=lambda p: p.replace("~", td)):
                r = C.register("cursor", "neuron5", PY, install_dir=td)
                self.assertTrue(r.ok, r.line())
                cfg = json.load(open(os.path.join(td, ".cursor", "mcp.json")))
                self.assertEqual(cfg["mcpServers"]["neuron5"]["command"], PY)
                # existing keys survive a re-register
                cfg["mcpServers"]["other"] = {"command": "x"}
                C.save_json(os.path.join(td, ".cursor", "mcp.json"), cfg)
                r2 = C.register("cursor", "neuron5", PY, install_dir=td)
                self.assertTrue(r2.ok)
                cfg2 = json.load(open(os.path.join(td, ".cursor", "mcp.json")))
                self.assertIn("other", cfg2["mcpServers"])
                # manifest recorded the write (B7)
                m = C.load_manifest(td)
                self.assertIn("cursor", m.get("registrations", {}))

    def test_jsonc_config_is_never_rewritten(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("os.path.expanduser",
                            side_effect=lambda p: p.replace("~", td)):
                p = os.path.join(td, ".cursor")
                os.makedirs(p)
                cfg_path = os.path.join(p, "mcp.json")
                jsonc = '{\n  // my servers\n  "mcpServers": {},\n}\n'
                with open(cfg_path, "w") as fh:
                    fh.write(jsonc)
                r = C.register("cursor", "neuron5", PY)
                self.assertFalse(r.ok)
                self.assertIn("JSONC", r.detail)
                json.loads(r.snippet)          # snippet must be valid JSON
                with open(cfg_path) as fh:      # file untouched
                    self.assertEqual(fh.read(), jsonc)


class TestDoctor(unittest.TestCase):
    def test_detects_duplicate_and_cruft_and_fixes(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("os.path.expanduser",
                            side_effect=lambda p: p.replace("~", td)):
                p = os.path.join(td, ".cursor")
                os.makedirs(p)
                cfg_path = os.path.join(p, "mcp.json")
                dead = os.path.join(td, "gone", "python.exe")
                C.save_json(cfg_path, {"mcpServers": {
                    "neuron":  {"command": PY, "args": ["-m", "neuron"]},
                    "neuron5": {"command": dead, "args": ["-m", "neuron"]},
                }})
                # limit scan to cursor for the test
                with mock.patch.dict(C.CLIENTS, {k: v for k, v in C.CLIENTS.items()
                                                 if k == "cursor"}, clear=True):
                    lines, problems = C.doctor("neuron5", PY)
                    joined = "\n".join(lines)
                    self.assertIn("BOTH", joined)
                    self.assertIn("missing", joined.lower())
                    self.assertGreaterEqual(problems, 2)
                    # fix mode removes the cruft entry
                    lines2, _ = C.doctor("neuron5", PY, fix=True)
                    cfg = json.load(open(cfg_path))
                    self.assertNotIn("neuron5", cfg["mcpServers"])
                    self.assertIn("neuron", cfg["mcpServers"])   # untouched

    def test_wrong_install_repointed(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("os.path.expanduser",
                            side_effect=lambda p: p.replace("~", td)):
                p = os.path.join(td, ".cursor")
                os.makedirs(p)
                cfg_path = os.path.join(p, "mcp.json")
                other = sys.executable   # exists but pretend current is different
                C.save_json(cfg_path, {"mcpServers": {
                    "neuron5": {"command": other, "args": ["-m", "neuron"]}}})
                fake_current = os.path.join(td, "venv-python.exe")
                with open(fake_current, "w") as fh:
                    fh.write("")
                with mock.patch.dict(C.CLIENTS, {k: v for k, v in C.CLIENTS.items()
                                                 if k == "cursor"}, clear=True):
                    lines, problems = C.doctor("neuron5", fake_current)
                    self.assertTrue(any("DIFFERENT install" in ln for ln in lines))
                    C.doctor("neuron5", fake_current, fix=True)
                    cfg = json.load(open(cfg_path))
                    self.assertEqual(cfg["mcpServers"]["neuron5"]["command"],
                                     fake_current)


class TestDeregister(unittest.TestCase):
    """T63 — uninstall path: remove only our slug, idempotent, backup taken."""

    def test_deregister_removes_only_our_slug(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("os.path.expanduser",
                            side_effect=lambda p: p.replace("~", td)):
                os.makedirs(os.path.join(td, ".cursor"))
                p = os.path.join(td, ".cursor", "mcp.json")
                C.save_json(p, {"mcpServers": {
                    "neuron5": {"command": PY}, "other": {"command": "y"}}})
                r = C.deregister("cursor", "neuron5")
                self.assertTrue(r.ok and r.action == "deregistered")
                cfg = json.load(open(p))
                self.assertNotIn("neuron5", cfg["mcpServers"])
                self.assertIn("other", cfg["mcpServers"])
                self.assertTrue(os.path.exists(p + ".neuron-bak"))
                self.assertEqual(C.deregister("cursor", "neuron5").action, "skipped")

    def test_deregister_never_rewrites_jsonc(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("os.path.expanduser",
                            side_effect=lambda p: p.replace("~", td)):
                os.makedirs(os.path.join(td, ".cursor"))
                p = os.path.join(td, ".cursor", "mcp.json")
                jsonc = '{\n  // hi\n  "mcpServers": {"neuron5": {"command": "x"}},\n}\n'
                open(p, "w").write(jsonc)
                r = C.deregister("cursor", "neuron5")
                self.assertFalse(r.ok)
                self.assertEqual(open(p).read(), jsonc)


class TestProcessDoctor(unittest.TestCase):
    """B6b — live-process section coupled with the doctor."""

    PYV5 = r"C:\u\AppData\Local\Programs\neuron5\.venv\Scripts\python.exe"
    PYOLD = r"C:\u\AppData\Local\Programs\neuron5\.venv-old\Scripts\python.exe"
    PYV4 = r"C:\u\AppData\Local\Programs\neuron\.venv\Scripts\python.exe"

    def _procs(self):
        return [
            # the doctor itself + its PowerShell parent: must be excluded
            {"pid": 50, "ppid": 40, "name": "python.exe",
             "cmd": f"{self.PYV5} -m neuron doctor --slug neuron5"},
            {"pid": 40, "ppid": 1, "name": "powershell.exe", "cmd": "powershell"},
            # healthy: launched by a live client
            {"pid": 101, "ppid": 200, "name": "python.exe",
             "cmd": f"{self.PYV5} -m neuron"},
            {"pid": 200, "ppid": 1, "name": "claude.exe", "cmd": "claude"},
            # orphan: ppid 999 does not exist
            {"pid": 102, "ppid": 999, "name": "python.exe",
             "cmd": f"{self.PYV5} -m neuron"},
            # stale OUR-slug install (different venv under Programs\neuron5)
            {"pid": 103, "ppid": 300, "name": "python.exe",
             "cmd": f"{self.PYOLD} -m neuron"},
            {"pid": 300, "ppid": 1, "name": "cursor.exe", "cmd": "cursor"},
            # v4 side-by-side: NOT a problem
            {"pid": 104, "ppid": 400, "name": "python.exe",
             "cmd": f"{self.PYV4} -m neuron"},
            {"pid": 400, "ppid": 1, "name": "code.exe", "cmd": "code"},
        ]

    def test_classification(self):
        lines, problems = C.process_doctor(
            "neuron5", self.PYV5, lister=self._procs, self_pid=50)
        joined = "\n".join(lines)
        self.assertIn("4 Neuron server(s)", joined)      # 50/40 excluded
        self.assertIn("ORPHAN", joined)
        self.assertIn("DIFFERENT install", joined)
        self.assertNotIn("pid 104: launched by code.exe [!!]", joined)  # v4 ok
        self.assertTrue(any("pid 101" in ln and "[ok]" in ln for ln in lines))
        self.assertEqual(problems, 2)                    # orphan + stale

    def test_fix_kills_only_orphans(self):
        killed = []
        lines, problems = C.process_doctor(
            "neuron5", self.PYV5, fix=True,
            lister=self._procs, killer=lambda pid: killed.append(pid) or True,
            self_pid=50)
        self.assertEqual(killed, [102])                  # orphan only
        self.assertTrue(any("FIXED" in ln and "102" in ln for ln in lines))

    def test_duplicate_same_parent_flagged(self):
        procs = [
            {"pid": 101, "ppid": 200, "name": "python.exe",
             "cmd": f"{self.PYV5} -m neuron"},
            {"pid": 102, "ppid": 200, "name": "python.exe",
             "cmd": f"{self.PYV5} -m neuron"},
            {"pid": 200, "ppid": 1, "name": "claude.exe", "cmd": "claude"},
        ]
        lines, _ = C.process_doctor("neuron5", self.PYV5,
                                    lister=lambda: procs, self_pid=1)
        joined = "\n".join(lines)
        self.assertIn("spawned 2 Neuron servers", joined)
        self.assertIn("duplicate keys", joined)

    def test_no_servers(self):
        lines, problems = C.process_doctor("neuron5", self.PYV5,
                                           lister=lambda: [], self_pid=1)
        self.assertEqual(problems, 0)
        self.assertIn("no `python -m neuron`", lines[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
