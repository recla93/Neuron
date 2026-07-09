// neuron-handshake --- OpenCode plugin.
//
// Ensures Neuron's handshake priority text reaches the model's system prompt
// on every turn, independent of whether the host actually surfaces the MCP
// `instructions` field (opencode does not do this reliably today). Uses the
// same mechanism as other opencode plugins that inject system-prompt text
// (experimental.chat.system.transform), so it composes cleanly with them.
//
// Deployed automatically by scripts/neuron5-config.ps1's "Add Neuron to your
// AI" -> OpenCode flow, which copies this file to
// %USERPROFILE%\.config\opencode\plugins\neuron-handshake.mjs and registers
// it in opencode.json's top-level "plugin" array. Safe to copy by hand too.

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

export default async () => {
  return {
    "experimental.chat.system.transform": async (_input, output) => {
      output.system.push(NEURON_HANDSHAKE);
    },
  };
};
