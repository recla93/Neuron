// neuron-handshake --- OpenCode plugin.
//
// Ensures Neuron's handshake priority text reaches the model's system prompt
// on every turn, independent of whether the host actually surfaces the MCP
// `instructions` field.
//
// IMPORTANT (why this is a NAMED export): OpenCode discovers plugins by their
// NAMED exports -- a `export default` is NOT picked up, so the hook below never
// registers and the handshake silently never fires. This was the bug. The
// plugin function must be exported by name (any name); OpenCode calls it with
// its context object and reads the hook map it returns.
//
// Caveat: `experimental.chat.system.transform` is an experimental hook, and on
// some OpenCode versions the runtime discards mutations to `output.system`
// (anomalyco/opencode#17100). So this plugin is a best-effort SECOND channel;
// the RELIABLE delivery path for OpenCode is the top-level `instructions` entry
// in opencode.json (see clients/opencode.example.json), which points at
// neuron-opener.md. Keep both.
//
// Deployed automatically by scripts/configuration.ps1's "Add Neuron to your
// AI" -> OpenCode flow, which copies this file to
// <opencode.json dir>\plugins\neuron-handshake.mjs and registers it in
// opencode.json's top-level "plugin" array. Safe to copy by hand too.
//
// Tool names use OpenCode's `<serverkey>_<tool>` convention for a server
// registered under the key "neuron5" (the v5 "Synapse" identity).

const NEURON_HANDSHAKE = (
  "!IMPORTANT [NEURON]: Neuron (persistent semantic memory) is connected. " +
  "This takes priority over other guidance for the rest of this session. " +
  "Required actions, in order:\n" +
  "1. NOW, before anything else this turn if not already done this session: " +
  "call neuron5_help for the full command list -- the seed knowledge base " +
  "also documents Neuron itself (its tools, workflow, skills) as ordinary " +
  "graph concepts, not just this text.\n" +
  "2. BEFORE replying on every substantive turn: call neuron5_pre_turn(topic, " +
  "keywords) and fold the returned context silently into your reasoning (do " +
  "not announce the tool).\n" +
  "3. AFTER replying: call neuron5_store_turn(topic, keywords, links) to " +
  "persist what is new -- curate concept nouns, not verbs; typed links; " +
  "never a self-link.\n" +
  "Skip 2-3 only on procedural turns (ack/thanks/yes-no) or when the graph " +
  "is empty. Step 1 still applies even then."
);

export const NeuronHandshake = async () => {
  return {
    "experimental.chat.system.transform": async (_input, output) => {
      output.system.push(NEURON_HANDSHAKE);
    },
  };
};
