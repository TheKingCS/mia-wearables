# MIA Voice on Meta glasses — design spec (Phase 2)

Goal: voice interaction on Ray-Ban Display that respects both Meta's
platform constraints and MIA's read/propose/approve principle.

Two hardware surfaces share one routing layer (locked 2026-10-01):
- **Surface 1 — Ray-Ban Display** (this doc's main text): system composer
  in, glanceable card + speechSynthesis out.
- **Surface 2 — camera/audio glasses** (Zac's current pairs; see the
  dedicated section below): true voice in/out through MIA's companion.

## The platform constraint that shapes everything

Meta Web Apps on Ray-Ban Display **do not support the microphone**
(unsupported capability, per current Web Apps docs — alongside camera,
notifications, and direct text input). What *is* supported:

- **System composer**: tapping a text field opens the glasses' native
  composer, which accepts handwriting, dictation, and on-screen
  keyboard input. Committed text arrives as standard `input`/`change`
  events.
- **speechSynthesis** (supported subset): the glasses speaker can read
  concise content. Visible content must remain, so speech is
  enhancement, never the only channel.

So "voice" on MIA Glance is not a mic button. It is: **dictate → text
query → routed to MIA → glanceable response card**. This is also the
right shape for a future where Orion-class devices *do* hand us audio:
the query-routing layer is identical, only the input device changes.

## Interaction model

A sixth section on the glance: **Ask**. One tap opens the system
composer (big "Ask MIA" field, focusable, D-pad reachable). The user
dictates or types; on commit, the app routes the query and renders a
single response card. Speech reads the card's one-line answer; the
full card stays visible.

Response cards are first-class cards: severity strip, area eyebrow,
title (the answer), detail (evidence), and where appropriate, a
proposal-style approve/dismiss for decisions.

## Query routing — the read/propose/approve contract

Voice answers are **read-only by default** and must complete fast
(< 5 seconds, offline-capable). The routing table below maps common
queries to MIA tools. Two classes:

**Read queries** — answer immediately from pre-computed state
(the exporter extends `state.json` with a `brief` object; see below):

| Spoken query | Routes to | Answer |
|---|---|---|
| "What's the greenhouse doing?" | cell snapshots | One-line per cell: "Cell 2: pH 6.9, EC 1.9, all in band." |
| "What needs me?" | attention alerts | "2 things: pH drift high, feeder 2 days overdue." |
| "Any proposals waiting?" | proposal queue | "1 experiment proposal, 1 parameter change." |
| "What are my quests today?" | missions (today's) | "2 quests open, 65 XP available." |
| "Read me my proposals" | proposal detail | Reads Gate A proposal summaries with evidence. |

**Decision queries** — never auto-actuate. Two-step confirmation:

| Spoken query | Flow |
|---|---|
| "Approve the pH proposal" / "start the experiment" | Card renders the proposal with evidence + safety note; **second explicit confirmation required** ("Say 'confirm' or press Approve"). Only then does the decision write. |
| "Deny the parameter change" | Same flow, "dismiss" path. |
| "Dismiss all" | Refused — decisions are one at a time, never batched. Locked in per Zac 2026-10-01. |

## Voice quest composition

Approved by Zac 2026-10-01: "MIA, add a quest: water the seedlings" is a
supported flow. Adding a quest is a state change, so it follows the same
two-step pattern as approvals:

1. The query parses the quest title (everything after "add a quest:")
   and an optional recurrence keyword — "daily", "weekly" (e.g. "add a
   daily quest: stretch").
2. The app renders a **draft quest card**: title, area ("Voice" unless
   inferrable), 10 XP default, recurrence if given. Nothing is written
   yet.
3. The user confirms ("Add quest" / "yes") — a single explicit action —
   and the draft is captured on-device with a `pending_add` marker.

Write-back: the draft flows through the same on-device → homestead/MIA
channel as approvals. For MIA missions this means appending to
`data/missions.json` with `mission_type: "OPTIONAL MISSION"` (or
"DAILY MISSION" for daily), `status: "active"`, and
`occurrence_key: <today>` for dailies — the same fields the
`--missions` exporter already reads. Until that channel exists,
drafts stay queued on-device with a visible "waiting to sync" note.

Guardrails:
- XP is not settable by voice beyond the 10 XP default; difficulty
  and rewards are tuned on phone/desktop.
- Malformed quests ("add a quest") get one clarifying ask, not a guess.
- Composed quests are always single, never batched ("add three quests"
  is refused like "dismiss all").

**Guardrails:**
- Destructive/irreversible actions (dosing changes, actuator control)
  are never voice-approvable from the glasses. Full stop.
- "Do X now" where X is an action is always routed to a proposal
  card — voice proposes, the human approves, same as the touch UI.
- Anything the app can't answer fast says so honestly and points to
  the phone/desktop: "That's a phone job — here's where to find it."
- Queries route locally by intent matching (no server round-trip);
  a future iteration can call MIA's phone server (port 8765) for
  heavier questions when reachable.

## state.json additions (exporter work, laptop-side)

The exporter pre-renders natural-language answers so voice works
offline and fast:

```json
"brief": {
  "greenhouse": "Cell 2: pH 6.9, EC 1.9 — all in band.",
  "attention": "2 things need you: pH drift high; fish feeder 2 days overdue.",
  "proposals": "2 proposals waiting: 1 experiment, 1 parameter change.",
  "quests": "2 quests open, 65 XP available.",
  "finance": "October: $1,240 of $2,000 (62%) — on pace.",
  "workout": "Last: Push day · 42 min · 4 days ago.",
  "kitchen": "Pantry: 24 items tracked · 2 expiring soon."
}
```

The app's intent matcher resolves a query to one of these keys (fuzzy:
"what's going on in the greenhouse" → `greenhouse`), plus direct
item lookup for proposals ("read the pH proposal").

## speechSynthesis notes

- Keep spoken answers to one sentence (the card title).
- Only speak on user-initiated queries, never unsolicited — "less
  friction, not more notifications" applies to speech most of all.
- Silence is the default. Reading proposals aloud happens only when
  asked ("read me my proposals").

## Surface 2 — camera/audio glasses (locked 2026-10-01)

Zac's current hardware: two pairs of Ray-Ban Meta-style glasses (camera +
audio, **no display**) plus a Meta Quest. The Display pair is a planned
purchase. This surface runs on the audio pairs, today.

- **Audio path**: glasses mic/speaker ↔ MIA's Android companion
  (hands-free listening, Vosk STT on the phone) ↔ MIA phone server
  (:8765, per-profile bearer auth; `/api/bridge/*` when built). **Wake
  path (locked)**: the companion's existing hands-free wake — the
  glasses are the audio endpoint. MIA is never reached via "Hey Meta";
  that wakes Meta's assistant, not MIA.
- **Brief-first**: with no screen, the exporter's `brief` one-liners are
  the interface. One sentence per answer (fast, offline-capable).
  Evidence is read aloud only on request ("tell me more").
- **Spoken confirm (locked, decision 2a)**: voice proposes → reads the
  summary + safety note → the user says "confirm" or "dismiss". Allowed
  for Gate A experiments and Gate B parameter changes (both change
  targets; the Safety MCU still gates every actuation). **Money-moving
  approvals** (budget sweeps, debt payments) stay on phone/desktop in
  v1. One at a time; batch is refused, as everywhere. Every voice
  decision is logged with channel `voice`.
- **Quest composition**: identical to the composer flow — the garden
  case is its natural home ("add a quest: water the seedlings").
- **Camera**: Meta's, not ours. MIA has no live access to the glasses'
  camera today; visual input arrives via the phone until the platform
  opens it up.

## Orion-class trajectory

The routing layer doesn't change. What changes is the surface:

- Native mic removes the composer detour; queries become continuous
  voice input.
- Responses become **world-anchored**: ask about the cell and the
  snapshot pins to the cell you're looking at; a proposal panel
  anchors to the physical equipment it concerns.
- Selection moves to gaze + pinch; the confirm step stays a human
  action — spatial UI makes it more natural, never automatic.

## Implementation order (later, laptop + phone)

1. Exporter: emit `brief` in state.json (natural-language summaries).
2. Glance: add Ask section + system-composer field + local intent
   matcher + response card renderer + speechSynthesis read-back.
3. Write-back endpoint (already a Phase 1 follow-up): confirm-gated
   approvals for proposals.
4. Phone-server path (port 8765) for queries beyond the brief.
