# Muse ↔ MIA bridge — design spec

Thesis: **Muse is the primary interface to Meta's glasses; MIA is the
intelligence and world model.** This bridge is the connective tissue:
Muse reads MIA's real state, proposes actions, and — only with the
user's explicit confirmation — submits decisions back. MIA keeps
working fully offline with Muse absent; the bridge never becomes a
dependency of the local core.

## Architecture

- **Server:** MIA's existing phone server (port 8765) extended with a
  `/api/bridge/*` contract. Locally hosted on Zac's laptop; MIA Core
  remains the source of truth.
- **Transport:** HTTPS tunnel (Cloudflare quick tunnel today; Tailscale
  or stable hosting later). Never exposed bare; never LAN-open by
  default.
- **Client:** Muse (this agent). Every read/write goes through the
  contract below — no ad-hoc SQL, no direct file edits.

```
Meta glasses (Glance app) ── state.json ──┐
                                          ├─ MIA Core (laptop)
Muse (chat / proactive) ── bridge API ───┤   ├─ phone server :8765
                                          └─ homestead DB + experiments
```

## Auth

- Per-profile bearer tokens already exist in MIA's phone server —
  reuse them. Muse's token lives in secure credential storage,
  never in chat, memory, code, or logs.
- Constant-time comparison on the server; loopback/tunnel-only
  exposure; per-token audit (who: `muse`, profile, timestamp).
- Token rotation = regenerate in the server, replace in the vault.

## Contract (draft v0)

### Read

| Endpoint | Returns | Used for |
|---|---|---|
| `GET /api/bridge/health` | `{ok, version, db}` | Muse checks reachability before promising anything |
| `GET /api/bridge/state` | glance `state.json` shape | "What's the greenhouse doing?" — attention/proposals/tracked/next_up/quests |
| `GET /api/bridge/brief` | natural-language summaries | voice + chat answers ("2 things need you…") |
| `GET /api/bridge/proposals` | Gate A/B rows, full evidence | reading a proposal before deciding |

### Write (confirm-gated, see rules)

| Endpoint | Body | Effect |
|---|---|---|
| `POST /api/bridge/proposals/decide` | `{kind, row_id, decision}` | routes to the homestead write-back (start/approve/deny) |
| `POST /api/bridge/quests` | `{title, recurring?}` | creates a mission draft in `data/missions.json` (`OPTIONAL MISSION`, or `DAILY MISSION` for daily) |

### Rules for every write

1. **Read/propose/approve, always.** Muse never writes on its own:
   it shows the proposal (evidence + safety note), the user confirms
   in chat (or on the glasses), then Muse submits. Batch decisions
   are refused — one at a time, in spec since 2026-10-01.
2. **Approve changes the target, never bypasses safety.** The bridge
   calls the homestead's own approval functions; the Safety MCU's
   hard limits still gate every dose. No endpoint may actuate, dose,
   or run arbitrary commands/SQL.
3. **Idempotent + status-guarded.** A decide only lands when the row's
   current status is still `proposed`; repeats are no-ops, reported
   as such.
4. **Audited.** Every write records channel (`muse` / `glasses`),
   profile, timestamp, and the row snapshot.
5. **Honest staleness.** If the bridge is unreachable (laptop asleep),
   Muse says so plainly, falls back to last-known state labeled
   stale, and never fabricates numbers.

## What "working well" looks like from Zac's side

- **Conversational front door:** "ask MIA how the greenhouse is" /
  "what needs me?" / "approve the EC proposal" — Muse answers from
  real state, shows the proposal before deciding, confirms, submits.
- **Continuity:** the same state powers the glasses glance, phone,
  and Muse — one model, many interfaces, no forks.
- **Proactive only where committed:** Muse may check MIA state a few
  times a day *only* through channels Zac has explicitly committed
  (today: the email watch; MIA alerts join that list only if he asks).
  Otherwise, user-initiated, quiet by default.
- **Future intelligence path:** the bridge is the front door the
  2026-10-01 handoff imagined — clearer interfaces so more capable
  intelligence can discover, understand, and extend MIA, with the
  human keeping authority.

## Build order (laptop sessions, via HANDOFF.md + Claude Code)

1. Spec review with Zac (this doc + write-back spec + Phase 3 draft).
2. Implement `/api/bridge/*` in the MIA phone server behind a feature
   flag; smoke test `health`/`state` locally.
3. Store the bridge token in Muse's secure vault; wire a small
   bridge skill doc (`AGENTS.md` note + committed skill) so every
   Muse session talks to MIA the same way.
4. First confirm-gated write end-to-end: read a proposal in chat →
   confirm → decide lands → audit line visible.
5. Expose over the tunnel for glasses + Muse; scheduled exporter
   keeps `state.json` fresh for the glance.
6. Optional: scheduled MIA attention check, only if Zac commits the
   channel.
