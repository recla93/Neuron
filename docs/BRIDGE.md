# Bridge guide — expose Neuron over HTTP (for ChatGPT & other remote-only clients)

Neuron is a **local stdio** MCP server. Most clients (Claude Desktop/Code, Cursor, OpenCode,
VS Code, …) launch it directly as a subprocess. But some clients — notably **ChatGPT**
(Developer Mode connectors / Apps SDK) — only talk to a **remote HTTPS MCP endpoint** and
cannot launch a local process. For those, you put a small **stdio → HTTP bridge** in front of
Neuron and give the client the bridge's public URL.

> **Transport vs storage are independent.** The bridge only changes *how the client reaches
> Neuron*. The wrapped `python -m neuron` still resolves its storage exactly as usual — local
> file, or the shared Turso Cloud DB if the environment provides the credentials (see the
> [Team guide](TEAM.md)). You can mix freely: solo on stdio+local, team on stdio+Turso, a
> ChatGPT user on HTTP+Turso.

## The shape of it

```
ChatGPT  ──HTTPS──▶  public tunnel  ──HTTP──▶  mcp-proxy  ──stdio──▶  python -m neuron
```

Two pieces you add around Neuron:

1. **A bridge that runs a stdio server and serves it over HTTP/SSE.** The right tool is
   [`mcp-proxy`](https://github.com/sparfenyuk/mcp-proxy) in **server mode**.
   > Do **not** use `mcp-remote`: it goes the *other* direction (it lets a stdio *client* reach
   > a remote HTTP server), which is the opposite of what we need here.
2. **A public HTTPS URL.** ChatGPT connectors are remote and can't reach `localhost`, so the
   bridge's local port has to be exposed over HTTPS (a tunnel, or a hosted box).

## Steps

```bash
# 1. Wrap Neuron's stdio server and serve it over HTTP/SSE on localhost.
#    (flag names vary by mcp-proxy version — check its README)
uvx mcp-proxy --port 8000 -- python3 -m neuron
#    → local endpoint, e.g.  http://127.0.0.1:8000/sse

# 2. Expose that port over PUBLIC HTTPS. A quick throwaway tunnel:
cloudflared tunnel --url http://127.0.0.1:8000
#    → gives a  https://<random>.trycloudflare.com  URL
#    (ngrok, a reverse proxy, or a hosted box work too)

# 3. In ChatGPT → Settings → Connectors (Developer Mode) → add the public HTTPS URL,
#    appending the SSE path, e.g.  https://<random>.trycloudflare.com/sse
```

Run the bridge from inside Neuron's venv (so `mcp`, `fastembed`, `pyturso` resolve), and in the
same environment where your `.env` / Turso credentials live if you want the ChatGPT session to
share the team memory.

## Security

The tunnel exposes a network endpoint, and **Neuron ships with no auth layer**. Treat the URL
as a secret:

- Keep the tunnel **private and short-lived**; tear it down when you're not using it.
- For anything beyond a quick test, put access control in front of it (e.g. Cloudflare Access),
  or host it on a box you control with proper auth/TLS.
- Anyone with the URL can read and write your Neuron memory — on a **shared Turso** DB that
  means the whole team's knowledge.

## Requirements & notes

- ChatGPT MCP connectors require **Developer Mode** (beta) and are limited to the paid web
  plans (Plus / Pro / Business / Enterprise / Education). This may change — check ChatGPT's
  current connector docs.
- Perplexity's *local* MCP is macOS-only (a different mechanism); its remote connectors are a
  paid feature. See [DEVELOPER.md](DEVELOPER.md#mcp-client-configuration).

## Future: native HTTP transport

A first-class HTTP transport (Neuron serving MCP **Streamable HTTP** directly, no bridge) is a
planned option — tracked as **T15** in `TASKLIST.md`. It would remove the `mcp-proxy` hop, but
you'd still need a public HTTPS endpoint for ChatGPT, so the bridge above is the simplest path
today. The native transport is best added and validated with hands-on access to the runtime.
