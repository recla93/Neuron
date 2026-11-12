"""Build the English variants of the recall set (2026-11-12): does writing the
facts in English cost recall when the user asks in Italian?

    python neuron/bench/make_en.py      # writes recall_en_text.json, recall_en_all.json
    python neuron/bench/run.py --set neuron/bench/recall_en_all.json

Messages and off-topic stay in Italian, the user's language. en_text: fact text
in English, concepts as they were. en_all: text and concepts in English, i.e.
the whole machine side in English. Translations by hand, identifiers kept.
"""
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent

EN = {
    "u1": ("The user develops Gray Matter, Neuron and NeuRAG alone, writes in Italian and works on two PCs: one's local memory is not on the other.", ["user", "gray-matter"]),
    "u2": ("After a push the user checks CI: do not watch or rerun it, just say what was pushed.", ["ci", "push"]),
    "u3": ("Before implementing, the user wants the plan with pros, cons and measured gains: no code before the decision.", ["plan", "costs"]),
    "u4": ("After a fix the user wants a real-user test, interactive on the installers and the GUI, not just a green test suite.", ["test", "user"]),
    "d1": ("The three tools are standalone: Neuron and NeuRAG install and work without GM; GM is optional but recommended, as gateway and bridge.", ["standalone", "install"]),
    "d2": ("All three tools use Turso, locally too through pyturso; Turso cloud is optional and sqlite3 stays only as an automatic fallback.", ["turso", "cloud"]),
    "d3": ("ADR-015 (8/11): Neuron stores typed, append-only facts, concepts are only the index; node evictions and merges must no longer delete facts.", ["neuron", "facts", "memory"]),
    "d4": ("ADR-014 D3b: the UserPromptSubmit hook makes a single IPC request to the GM daemon, which builds Neuron context, KB pointer and flash.", ["memhook", "ipc", "flash"]),
    "d5": ("The recall's KB pointer is vector-based with a 0.55 threshold: search by name pointed at whole folders and minted noise bridges like flash-audit.", ["kb", "bridge", "threshold"]),
    "p1": ("Release 1.7.0 published on 6/11: gray_matter 1.7.0, neuron 6.9.0, neurag 1.7.1, commit 672bf01, CI green, installed with install.ps1 -Force.", ["release", "ci"]),
    "p2": ("As of 8/11 commit b29a186 (the install wires the memhook) is on main but not pushed, and the recall change in gray_matter/server.py is not committed.", ["memhook", "push", "commit"]),
    "p3": ("As of 8/11 GM's memory exists only on the main PC: graphs 24 MB and NeuRAG 65 MB, backups on the same disk, no external copy.", ["backup", "memory"]),
    "x1": ("Neuron and neurag tests failed with a locked WAL and disk I/O error: system Python had pyturso 0.6.1 instead of the 0.8.3 pin. Fix: pip install the wheel in neuron/vendor.", ["pyturso", "wal", "test"]),
    "x2": ("NeuRAG's SQL vector ranking never ran: f32blob does not exist in pyturso and the except swallowed the error. Fix 1.3.2: vector_distance_cos already takes the blob.", ["f32blob", "ranking", "neurag"]),
    "x3": ("Turns lost on restart: a killed worker left its last write in the WAL. Fix GM 1.3.0: clean shutdown via pipe, checkpoint every 8 mutations.", ["worker", "wal", "restart"]),
    "x4": ("Many python.exe running: the venv one is a CPython redirector and the real interpreter is its child, so each worker counts twice; _reap_orphans closes the orphans recorded in pids.", ["processes", "python", "worker"]),
    "x5": ("On 12/9 consolidate with drop_orphans archived 255 nodes out of 296; now drop_orphans is False by default and a drop above 20% of the nodes is refused.", ["consolidate", "graveyard"]),
    "k1": ("Neuron's graphs live in %LOCALAPPDATA%\\GrayMatterEnvironment\\neuron\\graphs, the KB in ...\\neurag, the daily backups in ...\\backups.", ["graphs", "backup", "paths"]),
    "k2": ("The three test suites run in three processes: neuron/tests/_mockdeps.py injects fake mcp and fastembed that contaminate the other suites.", ["test", "suite", "mockdeps"]),
    "k3": ("The Mirror workflow publishes the repo: graphs, KB, Claude Code memory and other personal data must never be committed.", ["mirror", "repo", "privacy"]),
    "k4": ("After a code change you must reinstall (install.ps1 -Force or Repair in the GUI) and restart the client: the MCP server runs from a copy in the venv.", ["install", "venv", "client"]),
    "k5": ("pyturso has no win_amd64 wheel on PyPI: the release builds an abi3 wheel and install.ps1 takes it from vendor/ with --find-links; the pin is exact on purpose.", ["pyturso", "wheel", "install"]),
    "k6": ("The hook's first recall after a restart lasts as long as the timeout (2.49 s of 2.5): the first message may get no memory; warm about 560 ms.", ["memhook", "latency", "daemon"]),
    "k7": ("The memhook is wired in ~/.claude/settings.json: recall on UserPromptSubmit, save in dry-run on Stop, both run with python -I.", ["memhook", "settings", "hook"]),
    "i1": ("User's idea (5/8): intercept events aspect-style to fire stimuli and flashes straight from the DB instead of via MCP.", ["events", "stimuli", "flash"]),
    "i2": ("User's idea (30/7): set from the GUI how much knowledge and how many stimuli are injected, to save tokens without filling the context.", ["gui", "token", "knowledge"]),
    "z1": ("The default embedding is paraphrase-multilingual-MiniLM-L12-v2 at 384 dimensions: changing it requires re-embedding the whole store.", ["embedding", "model"]),
    "z2": ("Neuron extract's tokenizer splits on the apostrophe: dell'albero becomes dell, and 472 stopwords do not stop Italian greetings and verbs.", ["extract", "tokenizer"]),
    "z3": ("ADR-013: a row's owner and space are written at INSERT and never change, even if others update it later.", ["owner", "space", "multiuser"]),
    "z4": ("The ai graph hit the MAX_NODES cap of 500 on 4/11: since then every new concept evicts the one with the lowest salience.", ["graph", "cap", "nodes"]),
    "s1": ("Hook coverage per client: Kimi Code has SessionStart and UserPromptSubmit but no Stop, Codex only SessionStart from the cowork plugin, OpenCode an experimental plugin on the system prompt.", ["hook", "kimi", "codex"]),
    "s3": ("Neuron keeps at most 5 episodes per node: at the sixth, the oldest is deleted (EPISODES_PER_NODE).", ["episodes", "neuron"]),
    "s2": ("Multi-client hooks done: Kimi Code has Stop too (exit 2), Codex has UserPromptSubmit and Stop in hooks.json, OpenCode a folder plugin with recall and save check.", ["hook", "kimi", "codex", "opencode"]),
    "s4": ("ADR-015: facts are append-only and never deleted, an update goes through supersedes; EPISODES_PER_NODE only limits how many facts pre_turn shows.", ["episodes", "neuron", "facts"]),
}


def main() -> None:
    base = json.loads((HERE / "recall.json").read_text(encoding="utf-8"))
    assert {f["id"] for f in base["facts"]} == set(EN), "every fact needs a translation"
    for name, with_concepts in (("en_text", False), ("en_all", True)):
        d = json.loads(json.dumps(base))
        d["what"] = f"{base['what']} Variant {name}: facts in English, messages in Italian."
        for f in d["facts"]:
            f["text"] = EN[f["id"]][0]
            if with_concepts:
                f["concepts"] = EN[f["id"]][1]
        (HERE / f"recall_{name}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        print("wrote", f"recall_{name}.json")


if __name__ == "__main__":
    main()
