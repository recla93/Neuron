// neuron-handshake --- OpenCode plugin (Gray Matter suite).
//
// Pushes the handshake into the model's system prompt every turn, independent
// of whether the host surfaces the MCP `instructions` field.
//
// IMPORTANT (why this is a NAMED export): OpenCode discovers plugins by their
// NAMED exports -- an `export default` is NOT picked up, so the hook never
// registers and the handshake silently never fires. This was a real bug. The
// plugin function must be exported by name (any name); OpenCode calls it with
// its context object and reads the hook map it returns.
//
// WHO SPEAKS: exactly one tool, resolved HERE from the GME registry, not at
// deploy time -- gray-matter > neuron > neurag. All three tools deploy this
// same file to the same path, so deploying twice is idempotent and the double
// handshake a full suite used to produce cannot happen. Keep in sync with
// claude-code-hook/neuron_sessionstart_hook.py.
//
// Tool names are deliberately UNPREFIXED: OpenCode uses `<serverkey>_<tool>`,
// the Claude side uses `mcp__<server>__<tool>`, and guessing produced
// instructions pointing at tools that do not exist. Only the trailing "if no
// ... tools exist" line names the server key.
//
// Caveat: `experimental.chat.system.transform` is experimental and some
// OpenCode versions discard mutations to `output.system`
// (anomalyco/opencode#17100). Best-effort SECOND channel; the reliable path
// stays the top-level `instructions` entry in opencode.json. Keep both.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const PRIORITY = ["gray-matter", "neuron", "neurag"];

function gmeRoot() {
  // Mirror of gray_matter.gme.user_base() -- never imported, just mirrored.
  let base =
    process.platform === "win32"
      ? process.env.LOCALAPPDATA || ""
      : process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share");
  if (!base) base = os.homedir();
  return path.join(base, "GrayMatterEnvironment");
}

function installedSlugs() {
  const out = new Set();
  const root = gmeRoot();
  try {
    for (const f of fs.readdirSync(root)) {
      if (!f.endsWith(".json")) continue;
      try {
        const raw = fs.readFileSync(path.join(root, f), "utf8");
        const d = JSON.parse(raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw);
        if (d && d.status === "installed" && d.key) out.add(d.key);
      } catch {
        /* unreadable/not json: skip this one, never the whole hook */
      }
    }
  } catch {
    /* no registry: no handshake, never a crash at session start */
  }
  return out;
}

function memoryBlock(key, withKnowledge) {
  return (
    "!IMPORTANT [NEURON]: persistent semantic memory is connected - priority " +
    "for this session.\n" +
    "Each substantive turn: call pre_turn(topic, keywords) BEFORE replying " +
    "(fold the returned context in silently), then store_turn(topic, keywords, " +
    "links) AFTER, to persist what is new.\n" +
    "Keywords = 3-5 concept NOUNS (no verbs/paths); links typed, never a " +
    "self-link; before minting a concept check find_candidates; never store " +
    "secrets or tokens.\n" +
    (withKnowledge
      ? "Knowledge base: knowledge_query(query) when the question touches " +
        "indexed material.\n"
      : "") +
    "Skip on procedural turns (ack/thanks/yes-no) or an empty graph. Once per " +
    "session: help (commands), skill(name='playbook') (full workflow).\n" +
    `If no ${key}_* tools exist here, memory is not connected - ignore this silently.`
  );
}

function knowledgeBlock(key) {
  return (
    "!IMPORTANT [NEURAG]: a knowledge base is connected - use it before " +
    "answering from memory.\n" +
    "Search it with knowledge_query(query) when the question touches indexed " +
    "material; cite what you used.\n" +
    "Once per session: knowledge_skill(name='usage') for the retrieval workflow " +
    "(chunking, filters, when NOT to search).\n" +
    `If no ${key}_* tools exist here, the knowledge base is not connected - ignore this silently.`
  );
}

export function handshakeFor(have) {
  const owner = PRIORITY.find((s) => have.has(s));
  if (!owner) return "";
  if (owner === "neurag") return knowledgeBlock(owner);
  if (owner === "neuron") return memoryBlock(owner, false);
  // Gateway: the OWNER sets the key, the installed PEERS set the content.
  // Announcing a memory loop with no Neuron behind it points the model at
  // tools that are not there -- the same class of bug as a wrong prefix.
  if (have.has("neuron")) return memoryBlock(owner, have.has("neurag"));
  if (have.has("neurag")) return knowledgeBlock(owner);
  return "";
}

export const NeuronHandshake = async () => {
  const text = handshakeFor(installedSlugs());
  return {
    "experimental.chat.system.transform": async (_input, output) => {
      if (text) output.system.push(text);
    },
  };
};
