# Life modules card map

What each of MIA's life modules contributes to the Glance sections, and where
the data comes from. Mapping verified against TheKingCS/MIA core managers
(2026-10-01). The exporter's `--mia-data <MIA data dir>` reads the JSON
files directly; where a module stores nothing, no card is emitted (no
fabrication).

Sections: **Needs attention** (act now) · **Proposals** (needs your yes) ·
**Tracked** (quietly fine) · **Next up** (coming soon) · **Today's quests**.

## Budget — `data/bills.json`, `budget_expenses/targets/income/debts.json`
- Attention: bill overdue (critical); month category spent over target.
- Tracked: worst category's month pace ("$210 of $150 — Dining"); income in.
- Next up: bills due ≤7 days; highest-APR debt ("pay this first").
- Note: bill due dates read stored `due_date`; MIA's manager advances
  recurring anchors, so never-paid recurring bills are approximate.

## Real estate — `data/properties.json`
- Tracked: per-property "equity $X · NOI $Y last 30 days".
- Honest gap: there is no lease/tenant field on Property — a "lease
  decision" card would be fabrication. Property attention comes from
  linked maintenance tasks and overdue rental income (via Budget).

## Kitchen — `data/kitchen_pantry.json`, `kitchen_grocery_list.json`
- Attention: pantry items expiring ≤1 day ("Use up soon").
- Tracked: pantry item count (+ expiring count).
- Next up: unchecked grocery items.
- Honest gap: no meal-plan entity; no low-stock threshold ("low" is not a
  stored signal — expiration is).

## Workout — `data/workout_sessions.json`, `workout_templates.json`
- Tracked: last session ("Push day · 42 min · 4 days ago"); warning ≥3 days.
- The streak lives in recurring fitness missions — already exported as
  Today's quests. "Missed workout" derives from days-since, not a schedule.

## Property — `data/maintenance.json` (Appliance/Property/Tool assets)
- Attention: overdue tasks (asset `last_completed` + `interval_days`);
  warranty expiring ≤30 days.
- Next up: tasks due ≤14 days.
- MIA's `core/today.py` already computes most of these shapes (bills,
  maintenance, pantry) and the phone server serves `/api/today` plus
  `/api/finance/summary` — the glance can fetch those later via the bridge;
  for now the exporter reads the same files directly.
