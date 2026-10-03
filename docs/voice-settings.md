# Voice settings — addendum to voice-spec.md (2026-10-03)

Locked with Zac: **spoken mission announcements, yes** — and the voice's
frequency and verbosity are **per-user preferences**, not constants.

## Spoken missions

When a mission is accepted, audio surfaces speak one calm line:

> "New mission: Defeat the Rot Horde. Plus 30 XP."

The visual fanfare (the NEW MISSION overlay) stays loud; the voice stays
calm. This matches the locked synthesis: state reporting stays human,
earned progression gets to be loud. Controlled by the `speak_missions`
setting (default on).

## Frequency — how often she's allowed to speak first

The communication gate (max proactive nudges per day, minimum spacing)
is a per-user preference with three presets:

| Preset | Nudges/day | Min spacing | For |
|--------|-----------|-------------|-----|
| Quiet  | 3         | 120 min     | She speaks rarely, only when it matters |
| Normal | 5         | 90 min      | The classic gate — a few useful nudges |
| Chatty | 10        | 30 min      | She thinks out loud more — missions, nudges, ideas |

The default stays **Normal** (MIA's "less friction, not more notifications"
principle). Zac runs **Chatty**. A `voice_settings.json` file may also set
`max_proactive_per_day` / `min_spacing_minutes` directly for a fully
custom profile. The gate numbers are advisory to MIA Core — the wearable
never invents its own nudges beyond what the Context Engine approves.

## Verbosity — how much she says

| Preset | Behavior |
|--------|----------|
| Brief  | One-liners only. Details stay on the card. |
| Normal | One-liner plus the key detail. |

## Controls

- Master switch: `voice_enabled` (mute her entirely).
- Surface: Ask page → "Her voice, your rules". Frequency, verbosity,
  spoken-mission toggle, master switch — all printed, no invisible verbs.
- Persistence: saved on-device (localStorage) and POSTed to the dev
  server's `/api/voice-settings` → `webapp/voice_settings.json` (gitignored);
  the exporter merges it into `state.voice.settings` on the next export.
- The Android companion and any future bridge read the same
  `state.voice.settings`, so she never disagrees with herself about
  how chatty she's allowed to be.

## Mission combining

Locked: procurement missions (the Grand Supply Run) **combine** errands
across modules into one trip, and accepting one may surface the linked
do-the-thing mission (e.g. buy the air filter → replace the air filter).
This is her connecting your life, not nagging — the novelty ledger still
applies: dismiss the follow-up and she stays quiet about it for a week.
