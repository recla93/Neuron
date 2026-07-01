# Team guide — one shared Neuron memory on Turso Cloud

Neuron can back a **single shared knowledge graph** that a small team (designed for **up to ~6
people**) writes into concurrently. Everyone points their Neuron at the **same Turso Cloud
database**; saves are incremental and concurrency-safe, so people editing in parallel don't
lose or overwrite each other's work.

Nothing about day-to-day use changes: a solo user and a team member run the **same code**. The
only difference is the connection string.

## 1. Create the shared database (once, by one person)

Using the [Turso CLI](https://docs.turso.tech/cli):

```bash
turso db create neuron-team
turso db show neuron-team --url          # the shared DB URL — same for everyone
```

## 2. Each member connects (with their own token)

Give everyone the **same DB URL**, but have each person create their **own auth token** so
tokens can be revoked individually:

```bash
turso db tokens create neuron-team       # one per person
```

Then each member, inside Neuron's venv:

```bash
pip install "neuron[cloud]"              # adds libsql-client
python scripts/connect_turso.py          # paste the shared URL + your own token
```

`connect_turso.py` runs a real read + write probe before saving anything, and saves the working
credentials to `.env` (the token is never printed). The server **auto-loads `.env`** at
startup, so from then on Neuron uses the shared DB automatically. Verify end-to-end:

```bash
python scripts/smoke_cloud.py            # expects RESULT: PASS
```

## 3. Choose one context, or several

The store is keyed by a **`context`** column, so multiple contexts coexist in the same tables
without colliding. You have two natural styles:

- **One shared context** (e.g. everything in `default`) → a single unified picture of what the
  whole team knows. Simplest, and gives everyone the full view.
- **Separate contexts** (e.g. `backend`, `frontend`) → each area keeps its own graph, still in
  the same shared DB, with cross-context inheritance from `default`.

> Note: the `neuron_auto` pipeline can **switch context automatically** when it detects a
> different domain. That's fine — the `context` column keeps those contexts from colliding on
> the shared DB — but it means "one context only" is a convention, not something the system
> enforces. Agree on the convention as a team.

## What's guaranteed under concurrent writing

- **No lost updates on the same node.** Salience is applied as an atomic relative delta
  (`salience = MAX(0, salience + Δ)`), so if two people both bump `spring` (say +2 and +3), the
  DB ends at +5 — not last-write-wins.
- **No silent downgrades.** Link weight only ever moves *up* (`tangential < medium < strong`)
  under concurrent writes.
- **No one wipes anyone.** A normal per-turn save writes only its own delta and never issues a
  blanket delete, so a member's save can't remove rows another member just wrote.
- **Reconciliation is deliberate.** The only save that deletes "rows no longer present in
  memory" is a **structural `neuron_merge`** (merging duplicate nodes). Treat merges — and
  `neuron_reset` — as intentional, coordinated operations, not routine ones, on a shared DB.

## Security & operational notes

- **The Turso token is the boundary.** Neuron itself has no user-level auth: anyone with a
  valid token can read and write the whole shared memory. Issue per-member tokens, and
  **revoke** a token (`turso db tokens invalidate …` / rotate) when someone leaves.
- **Keep `.env` private.** It holds the token and is gitignored — never commit it.
- **Back up** the shared DB periodically (`turso db shell … .dump`, or Turso's backup features)
  before large `merge`/`reset` operations.
- **Scale.** This is designed for a small trusted team (≤ ~6). Going bigger / public would want
  a service layer in front of the DB (conflict resolution, rate limiting, partitioning) — a
  deliberate future step, not built yet.

## Troubleshooting

- `connect_turso.py` fails with `WSServerHandshakeError: 400` → the tool auto-retries over
  `https://` and saves that; just re-run. If **every** scheme fails, the token is likely wrong,
  read-only, or issued for a different DB.
- Server isn't using the cloud → confirm both `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` are
  present (a real env var wins over `.env`; `NEURON_NO_DOTENV=1` disables the auto-load), then
  run `python scripts/check_cloud_config.py` (offline) or `smoke_cloud.py` (online).
