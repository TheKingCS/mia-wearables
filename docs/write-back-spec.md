# Homestead proposal write-back endpoint — design spec

**Purpose.** Let the MIA Glance glasses app send proposal decisions back to the homestead, replacing today's browser-localStorage-only approvals. One authenticated endpoint, three decision paths, strict status guards, full audit trail.

**Scope.** Approval decisions only: Gate A (proposed experiments) and Gate B (parameter-change proposals). Nothing else.

## Verified ground truth (repo `TheKingCS/mia-homestead`, `master`)

- `core/experiment_manager.py`:
  - `start_experiment(experiment_id: int) -> None` — raises `ValueError` if the id doesn't exist. Sets the experiment `running` and writes a `target_overrides` row the control loop reads every cycle. **It does not check current status** — it would happily restart a running/completed experiment. The endpoint must add the guard.
  - `approve_parameter_change(proposal_id: int) -> None` — raises `ValueError` if missing; sets `status='approved'`, `decided_at`, writes `target_overrides`. **Also no status guard** — it would approve an already-denied row. The endpoint must add the guard.
  - `deny_parameter_change(proposal_id: int) -> None` — unconditional `UPDATE … SET status='denied'`. No existence check; a bad id silently no-ops. The endpoint must check existence + status itself.
  - `ExperimentManager(db_conn, notifier=None, species_registry=None)` — all optional deps can be `None`.
  - There is **no dismiss/abort path for experiments** (schema has an `aborted` status but no manager or assistant function reaches it). Glasses "dismiss" of an experiment is therefore unsupported in v1 (see contract).
- `get_pending_proposals()` returns Gate B rows with `status='proposed'`. `get_active_experiments()` returns experiments in `('proposed','approved','running')` — filter to `proposed` for the queue surface.
- Decisions only ever change targets (`target_overrides`). Every individual dose still passes the Safety MCU's hard limits. An approval never bypasses safety.
- Audit table already exists: `automated_action_log(id, module_id TEXT, action TEXT, rationale TEXT, timestamp DEFAULT datetime('now'))`.
- The homestead repo has **no HTTP server today** (`main.py` is the control loop; `deploy/` has systemd units). This endpoint is new code.

## Where it lives

New file `server/proposal_api.py` in `mia-homestead`:

- Python stdlib only (`http.server.ThreadingHTTPServer`), same interpreter/deps as the rest of the repo — no new requirements.
- Own process, bound to **127.0.0.1**, port **8777** (configurable via env). Not reachable from the LAN, let alone the internet, by default.
- Instantiates `ExperimentManager(conn, notifier=None, species_registry=None)` per request on a fresh short-lived SQLite connection (`PRAGMA journal_mode=WAL`, `busy_timeout=5000`) to coexist with `main.py`'s long-lived connection. `notifier=None` is deliberate: no duplicate notifications (the homestead's own flow announces from its side). `species_registry=None` is a documented v1 tradeoff: approvals from the glasses don't offer the result as species-knowledge evidence until phase 2 wires the registry the way `main.py` does.
- Runs as its own systemd unit mirroring `deploy/mia-homestead.service` (`Restart=on-failure`, same service user).
- **Feature flag:** the server refuses decision requests unless `MIA_PROPOSAL_API_ENABLED=1` is set in its env file (returns `503 FEATURE_DISABLED`). Read endpoints can run with the flag off.
- Later consolidation, if wanted: mount the same handlers in the MIA phone server (port 8765, per-profile bearer auth already exists there). Not v1 — it would couple the homestead to the MIA repo's release cadence.

Remote reach (glasses → laptop) goes through the tunnel only (see Auth), never a port forward.

## Endpoint contract

All bodies are JSON. All responses are JSON with a top-level `"ok"` boolean.

### `GET /api/proposals/pending` (read-only)

Returns the approval queue for verification/debugging:

```json
{ "ok": true,
  "gate_a_experiments": [ { "id": 7, "cell_id": 2, "hypothesis": "…", "variable_changed": "ec_target_max", "baseline_value": 1.8, "test_value": 2.0, "status": "proposed", "proposed_at": "…", "planned_end_date": "…", "created_by": "assistant" } ],
  "gate_b_parameter_changes": [ { "id": 3, "experiment_id": 5, "cell_id": 2, "param_name": "ph_target_max", "current_value": 7.2, "proposed_value": 7.0, "rationale": "…", "status": "proposed", "proposed_at": "…" } ] }
```

### `POST /api/proposals/decide`

Request:

```json
{
  "kind": "experiment" | "parameter_change",
  "row_id": 7,
  "decision": "approve" | "deny" | "dismiss",
  "channel": "glasses" | "phone" | "cli" | "claude-code",
  "request_id": "optional client-generated uuid"
}
```

`channel` is an allowlisted enum, recorded in the audit row. Unknown values → `400`.

Decision mapping (the *only* mutations the endpoint may perform):

| kind | decision | manager call | guard |
|---|---|---|---|
| `experiment` | `approve` | `start_experiment(row_id)` | current status must be `proposed` |
| `experiment` | `deny` / `dismiss` | — none exists — | → `422 UNSUPPORTED_DECISION` |
| `parameter_change` | `approve` | `approve_parameter_change(row_id)` | current status must be `proposed` |
| `parameter_change` | `deny` / `dismiss` | `deny_parameter_change(row_id)` | current status must be `proposed` |

Success response (`200`):

```json
{ "ok": true, "idempotent": false, "kind": "experiment", "row_id": 7,
  "decision": "approve", "previous_status": "proposed", "new_status": "running" }
```

`new_status` is re-read after the manager call (`experiments` → `running`; parameter changes → `approved`/`denied`).

### Idempotency and the status guard (the critical section)

The manager functions trust their caller; the endpoint must not. Rules:

1. **Serialize all decides** with a single `threading.Lock` held for read-guard-act-reread. SQLite plus the manager's unconditional UPDATEs make this the only safe posture on one process.
2. Inside the lock: read the row. If it doesn't exist → `404 ROW_NOT_FOUND`. If `status != 'proposed'` → **no writes**, and:
   - The already-recorded outcome **matches** the requested decision (e.g. re-posted approve of an `approved` row, or approve of a `running` experiment) → `200` with `"idempotent": true`, `previous_status`/`new_status` = current status, plus `"note": "already_decided"`.
   - The recorded outcome **conflicts** (row was denied; caller asks approve) → `409 STATUS_CONFLICT` with `"current_status"`. The client should refresh its card from `state.json` on next export.
3. `request_id` is accepted and stored in the audit rationale; it is a dedupe *hint*, not the mechanism. The status guard is the mechanism. (If a future version needs strict exactly-once across restarts, add a unique index on a decisions table — phase 2.)
4. `ValueError` from the manager (missing row race) → `404`; any other exception → `500 MANAGER_ERROR` with no partial state committed by us (the endpoint itself writes only via the manager + one audit insert; audit insert happens after the manager call succeeds, inside the same DB transaction when practical).

## Auth

- **Shared bearer token.** Header `Authorization: Bearer <token>`. Compared with `hmac.compare_digest` against the expected token. Missing/malformed/wrong → `401 UNAUTHORIZED` (identical body for all three; no oracle).
- **Storage.** The token lives in an env file outside the repo tree — `~/.config/mia-homestead/proposal_api.env` (mode `600`, referenced by the systemd unit as `EnvironmentFile=`) — or the repo's gitignored `.env`. Never in the repo, never in chat, never in `HANDOFF.md`. Generate once: `python3 -c "import secrets; print('MIA_PROPOSAL_API_TOKEN=' + secrets.token_urlsafe(32))" > ` that file.
- **Startup refusals.** The server exits with a clear error if the token is unset or shorter than 16 characters. No "dev mode" default values, ever.
- **Exposure.** Loopback bind only. Remote access exclusively through the existing Cloudflare tunnel — and because a quick-tunnel URL is effectively public, **the token is the only gate**: treat the URL + token as a pair, rotate the token if either leaks (edit env file, restart unit; the glasses app re-reads its stored token at launch). Per-channel tokens are a phase-2 nice-to-have; v1 is one token, shared by glasses + Claude Code + scripts.
- The MIA phone server's per-profile bearer auth (port 8765) is the model to converge on if this endpoint is later folded into it.

## Audit

Every decide request (success, idempotent replay, conflict, and auth failure) produces:

1. A structured log line (stdout → journald): timestamp, channel, kind, row_id, decision, outcome code, previous/new status.
2. On any request that reached a row, one insert into the existing table:

```sql
INSERT INTO automated_action_log (module_id, action, rationale) VALUES (?, ?, ?)
-- module_id: 'proposal_api'
-- action:    'decide'
-- rationale: JSON string, e.g.
-- {"channel":"glasses","kind":"experiment","row_id":7,"decision":"approve",
--  "previous_status":"proposed","new_status":"running","request_id":"…",
--  "snapshot":{"cell_id":2,"variable_changed":"ec_target_max",
--              "baseline_value":1.8,"test_value":2.0,"hypothesis":"…"}}
```

The `snapshot` is the full row as read *before* the decision, so the log remains meaningful after the row mutates. Auth failures log with `module_id='proposal_api'`, `action='auth_failure'`, rationale containing only the remote address and timestamp — never the attempted token.

## What this endpoint must NEVER do

- **No SQL interface.** No `/query`, no string interpolation anywhere; every statement parameterized; `kind`/`decision`/`channel` checked against allowlists, never mapped to table/column names dynamically.
- **No dosing or actuator calls.** No `execute_command`, no MCU serial traffic, no `dosing_events` writes. Approvals change targets only; the Safety MCU's hard limits gate every dose regardless.
- **No creating proposals.** Cannot insert into `experiments` or `parameter_change_proposals`; cannot call `propose_experiment` / `propose_experiments_for_underperformance`.
- **No lifecycle extras.** No completing, aborting, or restarting experiments; no maintenance, inventory, orders, or alert mutations; no config file writes.
- **No guard bypass.** If `status != 'proposed'`, it writes nothing — even with a valid token.
- **No new exposure.** Never binds non-loopback; never serves other paths; unknown paths → `404`. No CORS beyond what the glasses web app origin needs (none by default).

## Error shapes

```json
{ "ok": false, "error": { "code": "STATUS_CONFLICT", "message": "proposal 3 is already denied" },
  "current_status": "denied" }
```

| HTTP | code | when |
|---|---|---|
| 400 | `BAD_REQUEST` | malformed JSON, missing fields, unknown kind/decision/channel |
| 401 | `UNAUTHORIZED` | missing or wrong token |
| 404 | `ROW_NOT_FOUND` | row_id not in the mapped table (also: all unknown paths) |
| 409 | `STATUS_CONFLICT` | row already decided, conflicting outcome |
| 422 | `UNSUPPORTED_DECISION` | `deny`/`dismiss` on an experiment (no manager path exists yet) |
| 500 | `MANAGER_ERROR` | manager raised after the guard passed |
| 503 | `FEATURE_DISABLED` | decide attempted with the feature flag off |

## Client (glasses app) contract

- On decision tap: POST with the stored token; optimistic UI as today.
- `200` + `idempotent: true` → render "already handled" state.
- `409` → mark the card decided-elsewhere; the next `state.json` export will omit it.
- `401` → show "approval token rejected — decisions still saved on this device" (fall back to localStorage-only, today's behavior). Never retry in a loop.
- Decisions queued offline re-POST once when connectivity returns; the status guard makes this safe.

## Rollout

1. **Land `server/proposal_api.py` + tests.** Test suite spins a temp DB from `schema/mia_homestead_schema.sql` and covers: auth (401s, bad/missing/malformed), guard (409s, idempotent 200s, no writes on non-`proposed`), mapping (Gate A approve → `running` + `target_overrides` row; Gate B approve/deny), audit rows written, flag-off 503. Zero behavior change to `main.py`.
2. **Install, flag off.** Systemd unit + env file with the token; verify `GET /pending` on loopback and `POST /decide` → 503.
3. **Flag on, loopback soak.** Drive it with curl from the laptop for a few days of real proposals; confirm audit rows and that `main.py` picks up new targets exactly as with phone-side approvals.
4. **Client wiring.** Glance app gains the send path behind its own flag; localStorage remains the fallback. Verify 401/409 handling against the live server.
5. **Tunnel exposure.** Route the endpoint path through the existing Cloudflare tunnel. Re-verify from the phone: no-token request → 401.
6. **Glasses soak.** A week of real decisions from the Display before considering the phone/desktop flows anything other than authoritative. Review `automated_action_log` for `module_id='proposal_api'` weekly during the soak.

## Open questions (for Zac)

1. **Experiment dismiss.** Glasses users will tap Dismiss on Gate A cards. v1 returns `422`. Add a manager-level dismiss (set `status='aborted'`) in phase 2, or leave Gate A dismissal to phone/desktop permanently?
2. **Species evidence.** Phone-side approvals offer the result as species evidence (`species_registry` wired); glasses-side v1 skips it. Acceptable until phase 2?
3. **Token custody on the glasses app.** The token must reach `webapp/` JS without being committed. Proposal: a gitignored `webapp/config.local.js` generated by the export pipeline. OK?
