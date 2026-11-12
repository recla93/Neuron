"""Recall benchmark for Neuron: ADR-015, fase 0.

A fixed memory (recall.json `facts`) is written through the public tool path,
then each real message is asked through the path the hook uses, and the answer
is checked for the facts it should bring. Before this, every recall threshold
was tuned on two or three samples kept in a session, and one of those
measurements turned out wrong an hour later.

    python neuron/bench/run.py               # both read modes, table
    python neuron/bench/run.py --json        # same, machine-readable
    python neuron/bench/run.py --mode extract -v   # one mode, every answer

Read modes (gray_matter/server.py `_hook_recall`):
  extract  committed b29a186: neuron extract(message) -> pre_turn(topic, keywords)
  message  uncommitted on 2026-11-08: pre_turn(topic=message), no keywords

What it reports, per mode:
  stored    facts still in the graph after the writes (the write side loses
            some today: per-node cap, merges)
  recall    expected facts found / expected facts, over all messages
  full      messages with every expected fact
  noise     facts shown that the message did not expect, per message
  off_topic facts shown on the off-topic messages (should be 0)
  chars     answer length per message: what each recall costs in context

Runs on the real embedder, in a throwaway graph directory: never touches the
user's memory. One process, the same rule as the suites.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
SET = _HERE / "recall.json"
SIG = 40          # a fact is "shown" when its first SIG characters are in the answer
MODES = ("extract", "message")

if __name__ == "__main__" and os.environ.get("PYTHONHASHSEED") != "0":
    # Ties in the ranking follow set order, i.e. str hashing: two identical runs
    # differed by a found fact. A fixed seed makes a number comparable to the last.
    import subprocess
    sys.exit(subprocess.call([sys.executable, *sys.argv], env={**os.environ, "PYTHONHASHSEED": "0"}))

_TMP = tempfile.mkdtemp(prefix="neuron-bench-")
# Before importing neuron: the graph dir, turns.jsonl and backups all follow it.
os.environ["NS_GRAPHS_DIR"] = _TMP
os.environ["NEURON_NO_DOTENV"] = "1"
sys.path.insert(0, str(_HERE.parent / "src"))


def sig(text: str) -> str:
    return text[:SIG]


def load(path: pathlib.Path = SET) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def shown(answer: str, facts: list[dict]) -> set[str]:
    return {f["id"] for f in facts if sig(f["text"]) in answer}


async def _call(S, tool: str, args: dict) -> str:
    return (await S.call_tool(tool, args))[0].text


async def _query_args(S, mode: str, text: str) -> dict:
    q = " ".join(text.split())[:300]          # what the hook sends
    if mode == "message":
        return {"topic": q}
    try:
        ex = json.loads(await _call(S, "extract", {"text": q}) or "{}")
    except (ValueError, TypeError):
        ex = {}
    return {"topic": str(ex.get("topic") or q[:120]),
            "keywords": [str(k) for k in (ex.get("keywords") or [])][:5]}


async def _flash(S, text: str, facts: list) -> str:
    """What GM's flash would show (ADR-016 D2): the bench fact id, or ""."""
    q = " ".join(text.split())[:300]
    out = await _call(S, "forgotten", {"facts": True, "near": q, "top_n": 1})
    hit = shown(out, facts)
    return next(iter(hit)) if hit else ""


async def run_mode(mode: str, data: dict) -> dict:
    from neuron import server as S
    from neuron.registry import GraphRegistry
    S._g = GraphRegistry(graphs_dir=str(pathlib.Path(_TMP) / mode))
    S.TURSO_ENGINE = False
    S._turn_search_cache.clear()
    facts = data["facts"]
    for f in facts:   # one turn per fact, the ADR-015 call: facts only
        await _call(S, "store_turn", {"facts": [
            {"kind": f["kind"], "text": f["text"], "concepts": f["concepts"]}]})
    g = S._g.get()
    # Past 64 new texts the recall vectors are built in a background thread, and
    # until then the vector search returns nothing. Measure a warm daemon.
    from neuron import search as _search
    _search._search_recall("warm", 1, g)
    while _search._recall_warming:
        await asyncio.sleep(0.1)
    kept = {e["text"] for eps in g.episodes.values() for e in eps}
    stored = sum(1 for f in facts if f["text"] in kept)

    rows, found, expected, full, noise, chars, flashes = [], 0, 0, 0, 0, 0, 0
    for m in data["messages"]:
        ans = await _call(S, "pre_turn", await _query_args(S, mode, m["text"]))
        got = shown(ans, facts)
        exp = set(m["expect"])
        found += len(got & exp)
        expected += len(exp)
        full += exp <= got
        noise += len(got - exp)
        chars += len(ans)
        fl = await _flash(S, m["text"], facts)
        flashes += bool(fl)
        rows.append({"id": m["id"], "found": sorted(got & exp), "missed": sorted(exp - got),
                     "noise": sorted(got - exp), "flash": fl, "answer": ans})
    off = off_flash = 0
    for o in data["off_topic"]:
        ans = await _call(S, "pre_turn", await _query_args(S, mode, o["text"]))
        got = shown(ans, facts)
        off += len(got)
        fl = await _flash(S, o["text"], facts)
        off_flash += bool(fl)
        rows.append({"id": o["id"], "found": [], "missed": [], "noise": sorted(got),
                     "flash": fl, "answer": ans})
    n = len(data["messages"])
    return {"mode": mode, "stored": stored, "facts": len(facts),
            "recall": round(found / expected, 3), "found": found, "expected": expected,
            "full": full, "messages": n, "noise": round(noise / n, 2),
            "off_topic": off, "chars": round(chars / n),
            "flashes": flashes, "off_topic_flashes": off_flash, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=MODES, action="append")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every answer")
    ap.add_argument("--set", default=str(SET), help="the recall set (default recall.json)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")   # answers carry emoji; a Windows console is cp1252
    data = load(pathlib.Path(a.set))
    results = [asyncio.run(run_mode(m, data)) for m in (a.mode or MODES)]
    if a.json:
        print(json.dumps({"set_version": data["version"], "results": results},
                         ensure_ascii=False, indent=1))
        return 0
    for r in results:
        print(f"\n== {r['mode']}: stored {r['stored']}/{r['facts']}  "
              f"recall {r['recall']:.0%} ({r['found']}/{r['expected']})  "
              f"full {r['full']}/{r['messages']}  noise {r['noise']}/msg  "
              f"off_topic {r['off_topic']}  chars {r['chars']}/msg  "
              f"flash {r['flashes']}/{r['messages']} (off-topic {r['off_topic_flashes']})")
        for row in r["rows"]:
            print(f"  {row['id']:4} found {','.join(row['found']) or '-':12} "
                  f"missed {','.join(row['missed']) or '-':12} noise {','.join(row['noise']) or '-':12} "
                  f"flash {row['flash'] or '-'}")
            if a.verbose:
                print("       " + row["answer"].replace("\n", "\n       "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
