// neuron-handshake --- OpenCode plugin (Gray Matter suite).
//
// Two jobs, every turn: the handshake in the system prompt (independent of
// whether the host surfaces the MCP `instructions` field), and the memory loop
// Claude Code gets from its hooks -- recall before the answer, a save check
// after it (gray_matter/memhook.py, which this plugin runs).
//
// SHAPE (measured on OpenCode 2.2.6, 2026-11-10): ONE default export serving
// both plugin APIs, as @dietrichgebert/ponytail does. V2 calls `setup(ctx)`
// and loads a local plugin only as a DIRECTORY listed in opencode.json
// ("./plugins/neuron-handshake") whose file is index.mjs -- `main` is ignored;
// a bare file path
// was taken for an npm package and never loaded ("NpmInstallFailedError").
// V1 calls `server()` and reads the hook map it returns.
//
// WHO SPEAKS: exactly one tool, resolved HERE from the GME registry, not at
// deploy time -- gray-matter > neuron > neurag. All three tools deploy this
// same file to the same path, so deploying twice is idempotent and the double
// handshake a full suite used to produce cannot happen. Keep in sync with
// claude-code-hook/neuron_sessionstart_hook.py.
//
// Tool names are deliberately UNPREFIXED: OpenCode uses `<serverkey>_<tool>`
// (or code mode), the Claude side uses `mcp__<server>__<tool>`, and guessing
// produced instructions pointing at tools that do not exist. Only the trailing
// "if no ... tools exist" line names the server key.

import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PRIORITY = ["gray-matter", "neuron", "neurag"];

function gmeRoots() {
  // Mirror of gray_matter.gme.gme_root() -- never imported, just mirrored.
  // Node has no venv to ask, so every place a suite can live is read: GM_HOME,
  // the per-OS base, and ~/GrayMatterEnvironment (a suite outside AppData,
  // where an MSIX client cannot redirect it). Each root: registry/ first, then
  // the flat pre-suite layout. Reading only the flat one is why this plugin
  // never fired once the registry moved into registry/.
  let base =
    process.platform === "win32"
      ? process.env.LOCALAPPDATA || ""
      : process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share");
  if (!base) base = os.homedir();
  const suites = [
    process.env.GM_HOME,
    path.join(os.homedir(), "GrayMatterEnvironment"),
    path.join(base, "GrayMatterEnvironment"),
  ].filter(Boolean);
  return suites.flatMap((s) => [path.join(s, "registry"), s]);
}

function installedSlugs() {
  const out = new Set();
  for (const root of gmeRoots()) {
    let files = [];
    try {
      files = fs.readdirSync(root);
    } catch {
      continue; /* no registry here: try the next root */
    }
    for (const f of files) {
      if (!f.endsWith(".json")) continue;
      try {
        const raw = fs.readFileSync(path.join(root, f), "utf8");
        const d = JSON.parse(raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw);
        if (d && d.status === "installed" && d.key) out.add(d.key);
      } catch {
        /* unreadable/not json: skip this one, never the whole hook */
      }
    }
  }
  return out;
}

// The plugin itself injects each message's recall when the gateway loop runs: asking
// the model for pre_turn too paid the same recall twice (transcripts, 2026-11-14).
function memoryBlock(key, withKnowledge, recallWired) {
  return (
    "!IMPORTANT [NEURON]: persistent semantic memory is connected - priority " +
    "for this session.\n" +
    (recallWired
      ? "Each substantive turn: your message arrives with its memory context already " +
        "injected (fold it in silently); call pre_turn(topic, keywords) only to look up " +
        "a DIFFERENT subject. AFTER replying, call store_turn(facts=[{kind, text, " +
        "concepts}], English only) "
      : "Each substantive turn: call pre_turn(topic, keywords) BEFORE replying " +
        "(fold the returned context in silently), then store_turn(facts=[{kind, text, " +
        "concepts}], English only) AFTER, ") +
    "for what is worth not repeating: progress, decisions, fixes, details, ideas, the user and their habits; not what git or the project files already record (commits, test runs, file contents).\n" +
    "Concepts = 1-5 NOUNS per fact (no verbs/paths); an update passes " +
    "supersedes=<#id>; links typed, never a " +
    "self-link; a near-duplicate fact is flagged when stored; never store " +
    "secrets or tokens.\n" +
    (withKnowledge
      ? "Knowledge base: knowledge_query(query) when the question touches " +
        "indexed material.\n"
      : "") +
    "Skip on procedural turns (ack/thanks/yes-no) or an empty graph.\n" +
    `If no ${key}_* tools exist here, memory is not connected - ignore this silently.`
  );
}

function knowledgeBlock(key) {
  return (
    "!IMPORTANT [NEURAG]: a knowledge base is connected - use it before " +
    "answering from memory.\n" +
    "Search it with knowledge_query(query) when the question touches indexed " +
    "material; cite what you used.\n" +
    `If no ${key}_* tools exist here, the knowledge base is not connected - ignore this silently.`
  );
}

function handshakeFor(have, recallWired = false) {
  const owner = PRIORITY.find((s) => have.has(s));
  if (!owner) return "";
  if (owner === "neurag") return knowledgeBlock(owner);
  if (owner === "neuron") return memoryBlock(owner, false);
  // Gateway: the OWNER sets the key, the installed PEERS set the content.
  // Announcing a memory loop with no Neuron behind it points the model at
  // tools that are not there -- the same class of bug as a wrong prefix.
  if (have.has("neuron")) return memoryBlock(owner, have.has("neurag"), recallWired);
  if (have.has("neurag")) return knowledgeBlock(owner);
  return "";
}

// --- The memory loop (gray_matter/memhook.py does the work) -------------------
// KEEP IN SYNC with memhook.py: MIN_CHARS, ACKS, SUBSTANTIVE_CHARS.

const MIN_CHARS = 12;
const ACKS = new Set(["ok", "si", "sì", "no", "grazie", "va bene", "perfetto", "fallo", "vai", "procedi"]);
const SUBSTANTIVE_CHARS = 600;

function worthRecalling(prompt) {
  const p = (prompt || "").trim();
  if (p.length < MIN_CHARS || p.startsWith("/")) return false;
  return !ACKS.has(p.toLowerCase().replace(/^[ .!?]+|[ .!?]+$/g, ""));
}

// GM's python is the one opencode.json already runs the gateway with.
function gatewayPython(key) {
  let dir = path.dirname(fileURLToPath(import.meta.url));
  for (let i = 0; i < 3; i++, dir = path.dirname(dir)) {
    try {
      const raw = fs.readFileSync(path.join(dir, "opencode.json"), "utf8");
      const cmd = JSON.parse(raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw)?.mcp?.[key]?.command;
      return Array.isArray(cmd) && cmd[0] ? cmd[0] : "";
    } catch {
      /* not here (or jsonc): one level up */
    }
  }
  return "";
}

// stdout of `memhook <mode> --plain`; "" on any failure. Exit 2 is a save
// verdict (the reason on stderr), as Kimi reads it.
function runMemhook(python, mode, payload, ms = 4000) {
  return new Promise((resolve) => {
    let out = "", err = "", child;
    try {
      // -I -m: never imports from the cwd (see memhook.py docstring).
      child = spawn(python, ["-I", "-m", "gray_matter.memhook", mode, "--plain"], { windowsHide: true });
    } catch {
      return resolve("");
    }
    const timer = setTimeout(() => { child.kill(); resolve(""); }, ms);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (c) => { out += c; });
    child.stderr.on("data", (c) => { err += c; });
    child.on("error", () => { clearTimeout(timer); resolve(""); });
    child.on("close", (code) => { clearTimeout(timer); resolve((code === 2 ? err : out).trim()); });
    child.stdin.on("error", () => {});
    child.stdin.end(JSON.stringify(payload));
  });
}

// One turn per session: the message, its recall, what the answer did. Fed by
// the event stream, read by the context hook and at the end of the turn.
function memoryLoop(python) {
  const turns = new Map();
  const turn = (sid) => turns.get(sid) || turns.set(sid, { prompt: "", recall: null, steps: 0, chars: 0 }).get(sid);
  return {
    message(sid, text) {
      const t = turn(sid);
      Object.assign(t, { prompt: text || "", steps: 0, chars: 0, recall: null });
      // The memhook also tells the daemon an ack needs no save check.
      t.recall = runMemhook(python, "recall", { prompt: t.prompt, session_id: sid });
    },
    step(sid) { turn(sid).steps++; },
    text(sid, s) { turn(sid).chars += (s || "").length; },
    async recalled(sid) { return (await turns.get(sid)?.recall) || ""; },
    // The reminder to send, or "": the daemon saw every store_turn of the turn.
    async ended(sid) {
      const t = turns.get(sid);
      if (!t || t.prompt.startsWith("[memory]") || !worthRecalling(t.prompt)) return "";
      // Our own answer of substance (a tool step, or a long reply) is what
      // memhook calls a turn of substance; `null` = substantive, "" = not.
      const substantive = t.steps > 1 || t.chars >= SUBSTANTIVE_CHARS;
      return runMemhook(python, "save", { session_id: sid, last_assistant_message: substantive ? null : "" });
    },
  };
}

function owned() {
  const have = installedSlugs();
  const owner = PRIORITY.find((s) => have.has(s));
  // The loop needs the GM daemon (recall, save check): only a gateway has one.
  const python = owner === "gray-matter" && have.has("neuron") ? gatewayPython(owner) : "";
  return { text: handshakeFor(have, Boolean(python)), python };
}

// One turn state per process, shared by every instance of this plugin: OpenCode
// sets a plugin up once per location AND again on every hot reload (an install
// rewriting this file is one). A "already listening" flag broke there, live
// 2026-11-10: the reloaded instance saw the flag of the dead one and never
// subscribed. Now every instance subscribes, and duplicates are dropped per
// event id and per context call instead.
// Field by field: a hot reload finds the object an OLDER version of this file left.
const g = (globalThis.__neuronMemory ||= {});
g.seen ||= new Set();

function firstSight(e) {
  const id = e?.id ?? `${e?.type}:${JSON.stringify(e?.data ?? null).slice(0, 300)}`;
  if (g.seen.has(id)) return false;
  if (g.seen.size > 5000) g.seen.clear();   // a bound, not a window: ids are unique
  g.seen.add(id);
  return true;
}

export default {
  id: "neuron-handshake",

  // OpenCode V2: system parts through the `context` hook, the turn through events.
  async setup(ctx) {
    const { text, python } = owned();
    const loop = python ? (g.loop ||= memoryLoop(python)) : null;
    await ctx.session.hook("context", async (ev) => {
      if (ev.__neuronMemory) return;          // a second instance on the same call
      ev.__neuronMemory = true;
      if (text) ev.system.push({ type: "text", text });
      const recalled = loop && ev.sessionID ? await loop.recalled(ev.sessionID) : "";
      // The daemon's text carries its own <recalled-memory> frame.
      if (recalled) ev.system.push({ type: "text", text: recalled });
    });
    if (!loop) return;
    (async () => {
      for await (const e of ctx.event.subscribe()) {
        if (!firstSight(e)) continue;
        const d = e?.data || {};
        if (e?.type === "session.inbox.enqueued" && d.item?.type === "user") loop.message(d.sessionID, d.item.payload?.text);
        else if (e?.type === "session.step.started") loop.step(d.sessionID);
        else if (e?.type === "session.text.ended") loop.text(d.sessionID, d.text);
        else if (e?.type === "session.execution.succeeded") {
          const reason = await loop.ended(d.sessionID);
          if (reason) await ctx.session.prompt({ sessionID: d.sessionID, text: reason });
        }
      }
    })().catch(() => { /* a dead stream never breaks the session */ });
  },

  // OpenCode V1: the handshake only (its loop would need the V1 chat hooks).
  async server() {
    const { text } = owned();
    return {
      "experimental.chat.system.transform": async (_input, output) => {
        if (text) output.system.push(text);
      },
    };
  },

  // Exposed for tests; OpenCode reads only id/setup/server.
  _test: { handshakeFor, worthRecalling, memoryLoop },
};
