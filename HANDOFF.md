# HANDOFF.md — for Claude Code (and future us)

This is the working handoff for `TheKingCS/mia-wearables`. If you're Claude Code
running on Zac's laptop, start here. Everything below is verified against the
repos as of 2026-10-01 (overnight session).

## What this repo is

Phase 1 of MIA for Meta glasses: **MIA Glance**, a web app built to Meta's
current Web Apps spec. One model (MIA Core), many interfaces — the glasses are
a read/propose/approve surface. Nothing actuates from the glasses, ever.

Design language: **Tracked / Needs Attention / Next Up** — here rendered as
four card sections: Needs attention, Proposals, Next up, Today's quests.
Gamification (XP, streaks) is in the quest cards.

## Repo layout

- `webapp/index.html` — the whole app, single file. Cards, detail overlay,
  keyboard/arrow navigation, localStorage persistence, 60s auto-refresh, `R`
  manual refresh.
- `webapp/state.json` — generated data. Never hand-edit; regenerate it.
- `webapp/.well-known/meta-wearables-manifest.json` — V1 manifest for Meta's
  crawler/validator.
- `tools/export_glance_state.py` — the exporter. Reads live sources, writes
  `webapp/state.json`. All flags optional; missing data = honest empty states.
- `tools/seed_demo_data.py` — seeds the homestead DB with demo rows.
- `tools/quests.example.json` — fallback quest file format (missions.json
  preferred).

## Zac's local paths (Windows laptop, PowerShell)

- This repo: `C:\Users\yhrho\mia-wearables`
- Homestead repo (nested inside!): `C:\Users\yhrho\mia-wearables\mia-homestead`
- Homestead DB: `C:\Users\yhrho\mia-wearables\mia-homestead\mia_homestead.db`
- MIA repo: wherever Zac cloned `TheKingCS/MIA` — its `data/missions.json`
  feeds quests. Ask Zac for the path; do not guess.

## The full pipeline (run on Zac's laptop, PowerShell)

```powershell
cd C:\Users\yhrho\mia-wearables\mia-homestead
# 1. Homestead -> dashboard JSON (needs schema applied once per fresh DB:
#    sqlite3 mia_homestead.db < schema/mia_homestead_schema.sql)
python viewer/export_dashboard_state.py --db mia_homestead.db

# 2. Dashboard JSON + homestead DB + MIA missions -> glance state
cd C:\Users\yhrho\mia-wearables
python tools/export_glance_state.py `
  --dashboard C:/Users/yhrho/mia-wearables/mia-homestead/viewer/dashboard_state.json `
  --db C:/Users/yhrho/mia-wearables/mia-homestead/mia_homestead.db `
  --missions C:/path/to/MIA/data/missions.json `
  --out webapp/state.json

# 3. Serve + preview
python -m http.server 8080   # then open http://localhost:8080/webapp/
```

Notes:
- Use `python`, not `py` (py isn't on PATH). Forward slashes in Python
  string paths; `C:\Users` inside a normal Python string breaks on `\U`.
- `--missions` filters MIA missions: status=active, profile null/match,
  daily recurrences only when occurrence_key == today. Weekly recurrences
  are skipped for now (their `{start}-W{n}` keys need a matcher).
- Recurring missions only materialize in `missions.json` when MIA desktop
  runs `ensure_current_missions()` — if today's dailies are missing, that's
  why.
- `state.json` is NOT regenerated automatically. A Windows Scheduled Task
  for steps 1–2 has not been created yet — that's a next step.

## What's done (all committed on main)

1. Glance prototype: 4 sections, detail overlay, approve/dismiss persisting
   to localStorage, keyboard navigation, auto-refresh.
2. Live data: homestead alerts -> attention, maintenance -> next up.
3. Real proposals (no more demo dosing card): the exporter maps the
   homestead approval queue into a Proposals section —
   - Gate A: `experiments` with status='proposed' (from dashboard JSON).
     Approve = `start_experiment(row_id)`.
   - Gate B: `parameter_change_proposals` with status='proposed' (read from
     the DB via `--db`). Approve = `approve_parameter_change(row_id)`.
   - Each card carries `proposal: {kind, row_id, real: true, ...}` so the
     future write-back knows exactly what to call.
4. Real quests: `--missions` pulls today's active MIA missions (title,
   region, summary, XP).
5. Demo seeder for the homestead DB.

## What's next (in this order)

1. **Proposal write-back.** Approvals currently persist in browser
   localStorage only. Build a small authenticated endpoint on the homestead
   side that calls `start_experiment()` / `approve_parameter_change()` /
   `deny_parameter_change()` from `core/experiment_manager.py`, and have the
   app POST decisions there. Safety rule stands: approval changes the
   *target*; the Safety MCU's hard limits still gate every dose.
2. **Windows Scheduled Task** running steps 1–2 every few minutes.
3. **Stable HTTPS hosting** to replace the Cloudflare quick tunnel.
4. **Meta AI app / Developer Mode** — still blocked (app stuck loading
   account). Then: verify manifest over HTTPS from the phone, load Glance
   on the Ray-Ban Display, record layout/scroll/focus/Back behavior.
5. **Voice interaction** (Phase 3): map voice queries to MIA tools.

## Gotchas learned the hard way

- PowerShell: never put `<...>` placeholders in a runnable command.
- Complex quoting breaks; prefer committed `.py` files over long one-liners.
- `sqlite3.OperationalError: no such table` → the schema wasn't applied.
- Cloudflare quick-tunnel URLs change on every restart.
- The Meta AI Android app (Oct 2026) gets stuck on account loading; the
  "tap version 5 times" trick is unverified for the current build.
- Never ask Zac to paste tokens/secrets into chat.
