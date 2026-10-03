#!/usr/bin/env python3
"""
MIA Wearables — Glance state exporter.

Builds webapp/state.json for the MIA Glance web app from live sources:

  - homestead dashboard_state.json  ->  attention (unresolved alerts)
                                        next_up   (open maintenance tasks)
                                        proposals (experiments with
                                                   status='proposed' — Gate A)
  - homestead mia_homestead.db (--db) ->  proposals (parameter_change_proposals
                                        with status='proposed' — Gate B)
  - MIA data/missions.json (--missions) -> quests (today's active missions)
  - MIA data dir (--mia-data)         ->  budget / real estate / kitchen /
                                          workout / property-maintenance
                                          cards across every section

Every export also emits `brief`: one-line natural-language summaries
(greenhouse / attention / proposals / quests / finance / workout /
kitchen) for voice surfaces (see docs/voice-spec.md). And `answer`:
the answer-first home object (headline + focus card + sub-line),
computed from the same cards so every surface agrees on what
matters most (design addendum 2026-10-02: Context Engine).
  - optional quests JSON file         ->  quests    (fallback when --missions omitted)

Proposals are the real homestead approval queue, not stand-ins: Gate A
rows approve via start_experiment(), Gate B via approve_parameter_change().
Approving from the glasses app is not wired yet — decisions are captured
in the browser until the write-back endpoint exists.

Every source is optional. Missing data renders as an honest empty state
in the app ("All clear."), never fabricated cards.

Usage:
    python tools/export_glance_state.py \
        --dashboard /path/to/mia-homestead/viewer/dashboard_state.json \
        --db /path/to/mia-homestead/mia_homestead.db \
        --missions /path/to/MIA/data/missions.json \
        --out webapp/state.json

Run it on a schedule (or on demand) and re-host webapp/ — the glasses
app picks up the new state.json on next load.
"""

import argparse
import re
import datetime as dt
import json
from pathlib import Path

SEV_MAP = {"critical": "critical", "warning": "warning", "info": "info"}
PRIO_MAP = {"critical": "critical", "high": "warning",
            "medium": "info", "low": "info"}


def parse_date(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(s[:19] if "T" in s or len(s) > 10 else s, fmt).date() \
                if fmt == "%Y-%m-%d" else dt.datetime.strptime(s[:19], fmt).date()
        except ValueError:
            continue
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def due_line(due_date):
    d = parse_date(due_date)
    if not d:
        return "No due date set."
    today = dt.date.today()
    delta = (d - today).days
    if delta < 0:
        return f"Overdue {-delta} day{'s' if -delta != 1 else ''} (was due {d.isoformat()})."
    if delta == 0:
        return "Due today."
    return f"Due in {delta} day{'s' if delta != 1 else ''} ({d.isoformat()})."


PARAM_LABELS = {
    "ph_target_min": "pH target minimum",
    "ph_target_max": "pH target maximum",
    "ec_target_min": "EC target minimum",
    "ec_target_max": "EC target maximum",
}

# Approval changes the *target* the Pi's control loop aims for; every
# individual dose still passes the Safety MCU's hard limits.
SAFETY_TEXT = (
    "Approval changes the target the control loop aims for. Every dose "
    "still passes the Safety MCU's hard limits (max pulse, min interval, "
    "24h cap) — approval changes the target, never bypasses safety."
)


def map_experiment_proposal(e, cell_names):
    """Gate A: a proposed experiment -> start/approve it (start_experiment)."""
    param = e.get("variable_changed") or ""
    cell = cell_names.get(e.get("cell_id"), f"Cell {e.get('cell_id')}")
    ev = [
        e.get("hypothesis", ""),
        f"{cell}: baseline {e.get('baseline_value')}, test {e.get('test_value')}",
    ]
    if e.get("planned_end_date"):
        ev.append(f"Runs until {e['planned_end_date']}")
    ev.append(f"Proposed by {e.get('created_by', 'unknown')} at {e.get('proposed_at', '')}".strip())
    prop = {
        "real": True,
        "kind": "experiment",
        "row_id": e.get("id"),
        "title": (f"Test {PARAM_LABELS.get(param, param)}: "
                  f"{e.get('baseline_value')} → {e.get('test_value')}"),
        "summary": e.get("hypothesis", ""),
        "evidence": [x for x in ev if x],
        "safety": SAFETY_TEXT,
        "approve_label": "Start experiment",
        "dismiss_label": "Dismiss",
    }
    return {
        "id": f"exp-{e.get('id')}",
        "area": "Proposals",
        "title": prop["title"],
        "detail": e.get("hypothesis", ""),
        "severity": "warning",
        "action": "Review",
        "proposal": prop,
    }


def map_param_change_proposal(r, cell_names, hypotheses):
    """Gate B: adopt a completed experiment's result permanently."""
    param = r.get("param_name") or ""
    cell = cell_names.get(r.get("cell_id"), f"Cell {r.get('cell_id')}")
    ev = [r.get("rationale", "")]
    if r.get("experiment_id") is not None:
        ev.append(f"Source: experiment #{r['experiment_id']}")
        hyp = hypotheses.get(r["experiment_id"])
        if hyp:
            ev.append(hyp)
    ev.append(f"Proposed {r.get('proposed_at', '')}".strip())
    prop = {
        "real": True,
        "kind": "parameter_change",
        "row_id": r.get("id"),
        "title": (f"Adopt {PARAM_LABELS.get(param, param)} = "
                  f"{r.get('proposed_value')} permanently"),
        "summary": (f"Experiment #{r.get('experiment_id')} on {cell}: change "
                    f"{param} from {r.get('current_value')} to {r.get('proposed_value')}."),
        "evidence": [x for x in ev if x],
        "safety": SAFETY_TEXT,
        "approve_label": "Approve change",
        "dismiss_label": "Deny",
    }
    return {
        "id": f"ppc-{r.get('id')}",
        "area": "Proposals",
        "title": prop["title"],
        "detail": prop["summary"],
        "severity": "warning",
        "action": "Review",
        "proposal": prop,
    }


def load_gate_b_proposals(db_path):
    """Read parameter_change_proposals with status='proposed' from the DB."""
    import sqlite3
    p = Path(db_path)
    if not p.is_file():
        return []
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM parameter_change_proposals WHERE status = 'proposed' "
            "ORDER BY proposed_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def map_alert(a):
    parts = []
    if a.get("source"):
        parts.append(f"Source: {a['source']}")
    if a.get("last_seen"):
        parts.append(f"last seen {a['last_seen']}")
    if (a.get("occurrence_count") or a.get("repeat_count") or 1) > 1:
        parts.append(f"×{a.get('occurrence_count') or a.get('repeat_count')} occurrences")
    return {
        "id": f"alert-{a.get('id')}",
        "area": "Greenhouse",
        "title": a.get("message", "Unnamed alert"),
        "detail": ". ".join(parts) + "." if parts else "",
        "severity": SEV_MAP.get(str(a.get("severity", "info")).lower(), "info"),
        "action": "Open on phone",
        "proposal": None,
    }


def map_maintenance(m):
    sev = PRIO_MAP.get(str(m.get("priority", "medium")).lower(), "info")
    d = parse_date(m.get("due_date"))
    overdue = d is not None and d < dt.date.today()
    if overdue and sev == "info":
        sev = "warning"
    detail_bits = []
    if m.get("description"):
        detail_bits.append(m["description"])
    detail_bits.append(due_line(m.get("due_date")))
    if m.get("task_type"):
        detail_bits.append(f"Type: {m['task_type']}")
    return {
        "id": f"maint-{m.get('id')}",
        "area": "Greenhouse",
        "title": m.get("title", "Unnamed task"),
        "detail": " ".join(detail_bits),
        "severity": sev,
        "action": "Open on phone",
    }


def map_tracked_cell(c):
    """A 'quietly good' cell snapshot for the Tracked section."""
    readings = (c.get("readings") or []) if isinstance(c, dict) else []
    bits, stale = [], False
    for r in readings:
        if not isinstance(r, dict):
            continue
        label = (r.get("type") or "").replace("_", " ")
        label = {"ph": "pH", "ec": "EC"}.get(label, label)
        if r.get("value") is not None:
            unit = (r.get("unit") or "").replace("mS/cm", "").strip()
            if unit and not unit.startswith("°"):
                unit = " " + unit
            bits.append(f"{label} {r['value']}{unit}")
        if r.get("is_stale"):
            stale = True
    name = c.get("name") or f"Cell {c.get('id')}"
    return {
        "id": f"cell-{c.get('id')}",
        "area": "Greenhouse",
        "title": " · ".join(bits) if bits else name,
        "detail": (f"{name} — latest readings"
                   + (" (some readings stale)." if stale else ", in band.")
                   if bits else ""),
        "severity": "warning" if stale else "info",
        "action": "Open twin",
        "proposal": None,
    }


# ---------------------------------------------------------------------------
# Life modules (MIA repo data/ JSON files, --mia-data <path to MIA data dir>)
#
# Card mapping (per module):
#   budget     -> attention: overdue bills, month categories over target
#                 next_up:   bills due in 7d, ranked top debt ("pay this first")
#                 tracked:   month pace (income vs expenses) or top category
#   real_estate-> tracked:   per-property equity + 30-day NOI
#   kitchen    -> attention: pantry items expiring within 1 day
#                 tracked:   pantry count (+ expiring count)
#                 next_up:   unchecked grocery items
#   workout    -> tracked:   last session + days-since
#   property   -> attention: overdue maintenance tasks (Appliance/Property/
#                 Tool assets) + warranty expiring within 30 days
#                 next_up:   maintenance tasks due within 14 days
#
# The MIA managers (core/budget_manager.py etc.) compute richer dates via
# recurrence anchors (Bill.due_date advanced by recurrence vs last_paid_date).
# The exporter only reads the stored fields, so bill dates are approximate
# where a bill recurs and has never been paid. Where the stored data says
# nothing, the exporter emits no card (no fabrication).
# ---------------------------------------------------------------------------

def load_list(path):
    data = load_json(str(Path(path)))
    return data if isinstance(data, list) else []


def _money(x):
    try:
        return f"${float(x):,.0f}"
    except (TypeError, ValueError):
        return "$?"


LAST_BILLS_LINE = {"text": None}


def map_budget_cards(data_dir, today, attention, next_up, tracked):
    p = Path(data_dir)
    bills = load_list(p / "bills.json")
    expenses = load_list(p / "budget_expenses.json")
    income = load_list(p / "income.json")
    targets = load_list(p / "budget_targets.json")
    debts = load_list(p / "debts.json")

    upcoming = []
    for b in bills:
        due = parse_date(b.get("due_date"))
        name = b.get("name", "Bill")
        if due is None:
            continue
        days = (due - today).days
        if 0 <= days <= 14:
            upcoming.append((days, name, due, b.get("amount")))
        if days < 0:
            attention.append({
                "id": f"bill-{b.get('bill_id')}",
                "area": "Budget",
                "title": f"{name} is overdue",
                "detail": f"{_money(b.get('amount'))} — was due {due.isoformat()}.",
                "severity": "critical",
                "action": "Open on phone",
                "proposal": None,
            })
        elif days <= 7:
            next_up.append({
                "id": f"bill-{b.get('bill_id')}",
                "area": "Budget",
                "title": f"{name} due in {days} day{'s' if days != 1 else ''}",
                "detail": f"{_money(b.get('amount'))} — due {due.isoformat()}.",
                "severity": "warning" if days <= 2 else "info",
                "action": "Open on phone",
            })

    spent_by_cat = {}
    for e in expenses:
        d = parse_date(e.get("date"))
        if d and (d.year, d.month) == (today.year, today.month):
            spent_by_cat[e.get("category", "Other")] = (
                spent_by_cat.get(e.get("category", "Other"), 0.0)
                + float(e.get("amount") or 0))
    tgt = {}
    for tg in targets:
        if (tg.get("entity_id") or "") == "":
            tgt[tg.get("category", "Other")] = float(tg.get("monthly_amount") or 0)
    for cat, amt in tgt.items():
        s = spent_by_cat.get(cat, 0.0)
        if amt > 0 and s > amt:
            attention.append({
                "id": f"budget-over-{cat}",
                "area": "Budget",
                "title": f"{cat} over budget",
                "detail": f"{_money(s)} of {_money(amt)} this month.",
                "severity": "warning",
                "action": "Open on phone",
                "proposal": None,
            })
    if upcoming:
        upcoming.sort()
        d0, n0, due0, amt0 = upcoming[0]
        when = "today" if d0 == 0 else ("tomorrow" if d0 == 1
                                        else f"in {d0} day{'s' if d0 != 1 else ''}")
        LAST_BILLS_LINE["text"] = (
            f"Next bill: {n0} {_money(amt0)} due {when} ({due0.isoformat()})."
            + (f" {len(upcoming) - 1} more in the next two weeks."
               if len(upcoming) > 1 else ""))
    else:
        LAST_BILLS_LINE["text"] = "No bills due in the next two weeks."
    if spent_by_cat and tgt:
        worst = max((c for c in spent_by_cat if c in tgt and tgt[c] > 0),
                    key=lambda c: spent_by_cat[c] / tgt[c], default=None)
        if worst:
            tracked.append({
                "id": f"budget-pace-{worst}",
                "area": "Budget · this month",
                "title": (f"{_money(spent_by_cat[worst])} of "
                          f"{_money(tgt[worst])} — {worst}"),
                "detail": f"{spent_by_cat[worst] / tgt[worst]:.0%} of the month's budget used.",
                "severity": "info",
                "action": "Open on phone",
                "proposal": None,
            })
    elif spent_by_cat:
        total = sum(spent_by_cat.values())
        tracked.append({
            "id": "budget-spent",
            "area": "Budget · this month",
            "title": f"{_money(total)} spent this month",
            "detail": "No budget targets set — pace shows once targets exist.",
            "severity": "info",
            "action": "Open on phone",
            "proposal": None,
        })
    total_in = 0.0
    for i in income:
        d = parse_date(i.get("date"))
        if d and (d.year, d.month) == (today.year, today.month):
            total_in += float(i.get("amount") or 0)
    if total_in:
        tracked.append({
            "id": "budget-income",
            "area": "Budget · this month",
            "title": f"{_money(total_in)} in this month",
            "detail": "Income recorded so far this month.",
            "severity": "info",
            "action": "Open on phone",
            "proposal": None,
        })
    if debts:
        ds = sorted(debts, key=lambda d: -float(d.get("interest_rate") or 0))
        d0 = ds[0]
        bal = _money(d0.get("balance"))
        apr = f"{d0.get('interest_rate')}%" if d0.get("interest_rate") is not None else "?"
        next_up.append({
            "id": f"debt-{d0.get('debt_id')}",
            "area": "Budget · debts",
            "title": f"Pay {d0.get('name')} first",
            "detail": f"{bal} at {apr} APR — highest rate of {len(debts)} debts.",
            "severity": "info",
            "action": "Open on phone",
        })


def map_real_estate_cards(data_dir, today, tracked):
    p = Path(data_dir)
    props = load_list(p / "properties.json")
    if not props:
        return
    exps = load_list(p / "budget_expenses.json")
    incs = load_list(p / "income.json")
    lo = today - dt.timedelta(days=30)
    for pr in props:
        pid = pr.get("property_id")
        name = pr.get("name", "Property")
        bits = []
        try:
            ev = float(pr.get("current_value") or 0) - float(pr.get("mortgage_balance") or 0)
            if ev:
                bits.append(f"equity {_money(ev)}")
        except (TypeError, ValueError):
            pass
        noi = 0.0
        for e in exps:
            d = parse_date(e.get("date"))
            if e.get("property_id") == pid and d and lo <= d <= today:
                noi -= float(e.get("amount") or 0)
        for i in incs:
            d = parse_date(i.get("date"))
            if i.get("property_id") == pid and d and lo <= d <= today:
                noi += float(i.get("amount") or 0)
        if noi:
            bits.append(f"NOI {_money(noi)} last 30 days")
        tracked.append({
            "id": f"prop-{pid}",
            "area": "Real estate",
            "title": name,
            "detail": " · ".join(bits) + ("." if bits else ""),
            "severity": "info",
            "action": "Open on phone",
            "proposal": None,
        })


def map_kitchen_cards(data_dir, today, attention, next_up, tracked):
    p = Path(data_dir)
    pantry = load_list(p / "kitchen_pantry.json")
    soon, expiring = [], 0
    for it in pantry:
        exp = it.get("expiration_date")
        if not exp:
            continue
        d = parse_date(exp)
        if d is None:
            continue
        days = (d - today).days
        if days <= 1:
            soon.append(it.get("name", "item"))
            expiring += 1
    if soon:
        attention.append({
            "id": "pantry-expiring",
            "area": "Kitchen",
            "title": "Use up soon",
            "detail": ", ".join(soon[:4]) + (f" +{len(soon) - 4} more"
                                            if len(soon) > 4 else "") + ".",
            "severity": "warning",
            "action": "Open on phone",
            "proposal": None,
        })
    grocery = [g for g in load_list(p / "kitchen_grocery_list.json")
               if not g.get("checked")]
    if grocery:
        names = [g.get("name", "") for g in grocery]
        next_up.append({
            "id": "grocery-list",
            "area": "Kitchen",
            "title": f"{len(grocery)} grocery item{'s' if len(grocery) != 1 else ''}",
            "detail": ", ".join(n for n in names[:4] if n)
                      + (f" +{len(names) - 4} more" if len(names) > 4 else "") + ".",
            "severity": "info",
            "action": "Open on phone",
        })
    if pantry:
        tracked.append({
            "id": "pantry-count",
            "area": "Kitchen",
            "title": (f"Pantry: {len(pantry)} items tracked"
                      + (f" · {expiring} expiring soon" if expiring else "")),
            "detail": "Counted from your pantry list.",
            "severity": "info",
            "action": "Open on phone",
            "proposal": None,
        })


def _is_garage_category(cat):
    """Garage = vehicles & tools. Property keeps Appliance/Property; the
    property card mapper still owns due-task cards for tools, so a tool
    with an overdue task may surface in both places — browse vs alert."""
    c = (cat or "").lower()
    return any(k in c for k in ("vehicle", "tool", "equipment", "mower",
                               "automotive", "machine"))


def map_garage_index(data_dir):
    """Garage browse index: the real asset records + their tasks.

    Same MaintenanceAsset source as the glance cards — this is the
    asset's full record, not a summary.
    """
    p = Path(data_dir)
    raw = load_json(str(p / "maintenance.json"))
    if isinstance(raw, dict):
        assets, tasks = raw.get("assets", []), raw.get("tasks", [])
    elif isinstance(raw, list):
        assets, tasks = raw, []
    else:
        assets, tasks = [], []
    if not tasks and (p / "maintenance_tasks.json").is_file():
        tasks = [x for x in load_list(p / "maintenance_tasks.json")
                 if isinstance(x, dict)]
    items = []
    for a in assets:
        if not isinstance(a, dict):
            continue
        cat = (a.get("category") or "").strip()
        if not _is_garage_category(cat):
            continue
        rows = []
        for key, label in [("category", "Category"),
                           ("manufacturer", "Make"),
                           ("model", "Model"),
                           ("serial", "Serial"),
                           ("purchase_date", "Purchased"),
                           ("warranty_until", "Warranty until"),
                           ("location", "Location"),
                           ("current_hours", "Hours")]:
            v = a.get(key)
            if v is not None and str(v).strip():
                rows.append([label, str(v)])
        t_rows = []
        for x in tasks:
            if not isinstance(x, dict) or x.get("asset_id") != a.get("asset_id"):
                continue
            label = x.get("title", "Task")
            hrs, cur = x.get("interval_hours"), x.get("current_hours")
            if hrs is not None and cur is not None:
                left = hrs - cur
                label += (" — due now" if left <= 0
                          else f" — in {left} hrs")
            elif x.get("interval_days"):
                label += f" — every {x['interval_days']} days"
            t_rows.append(label)
        sub_bits = [cat] if cat else []
        if a.get("current_hours") is not None:
            sub_bits.append(f"{a['current_hours']} hrs")
        items.append({
            "id": f"asset-{a.get('asset_id')}",
            "title": a.get("name", "Unnamed"),
            "sub": " · ".join(sub_bits),
            "fields": rows,
            "list": ({"title": "Maintenance", "items": t_rows}
                     if t_rows else None),
            "note": "Same engine as the glance cards — completing a task "
                    "checks it off everywhere.",
        })
    return {"label": "Garage", "eyebrow": "Vehicles & tools", "items": items}


def map_kitchen_index(data_dir):
    """Kitchen browse index: recipe cards from KitchenManager fields."""
    p = Path(data_dir)
    items = []
    for r in load_list(p / "kitchen_recipes.json"):
        if not isinstance(r, dict):
            continue
        ings = []
        for i in (r.get("ingredients") or [])[:10]:
            if isinstance(i, dict):
                qty = str(i.get("quantity", "")).strip()
                unit = str(i.get("unit", "")).strip()
                ings.append(f"{qty} {unit} {i.get('name', '')}".strip())
            else:
                ings.append(str(i))
        rows = []
        for key, label in [("servings", "Servings"),
                           ("prep_time_minutes", "Prep"),
                           ("prep_time", "Prep"),
                           ("cook_time_minutes", "Cook"),
                           ("cook_time", "Cook"),
                           ("calories", "Calories"),
                           ("protein_g", "Protein"), ("protein", "Protein"),
                           ("carbs_g", "Carbs"), ("fat_g", "Fat")]:
            v = r.get(key)
            if v is not None and str(v).strip():
                rows.append([label, str(v)])
        sub_bits = []
        ct = r.get("cook_time_minutes") or r.get("cook_time")
        if ct:
            sub_bits.append(f"{ct} min")
        if r.get("calories"):
            sub_bits.append(f"{r['calories']} cal")
        steps = r.get("steps") or r.get("instructions") or []
        rid = r.get("recipe_id") or r.get("id") or r.get("name")
        items.append({
            "id": f"recipe-{rid}",
            "title": r.get("name", "Untitled recipe"),
            "sub": " · ".join(sub_bits),
            "fields": rows,
            "list": ({"title": "Ingredients", "items": ings}
                     if ings else None),
            "note": (f"{len(steps)} steps — cooking mode reads them to you."
                     if steps else "Recipe stored in KitchenManager."),
        })
    pantry = load_list(p / "kitchen_pantry.json")
    if pantry:
        items.append({
            "id": "pantry",
            "title": "Pantry",
            "sub": f"{len(pantry)} items tracked",
            "fields": [["Items", str(len(pantry))]],
            "list": {"title": "Sample",
                     "items": [str(x.get("name")) for x in pantry[:6]
                               if isinstance(x, dict)]},
            "note": "The glance's 'Use up soon' card opens into this.",
        })
    return {"label": "Kitchen", "eyebrow": "Recipes & pantry", "items": items}


def map_workout_cards(data_dir, today, tracked):
    p = Path(data_dir)
    sessions = load_list(p / "workout_sessions.json")
    if not sessions:
        return
    templates = {t_.get("template_id"): t_.get("name", "")
                 for t_ in load_list(p / "workout_templates.json")}
    last, last_date = None, None
    for s in sessions:
        d = parse_date(s.get("date"))
        if d and (last_date is None or d > last_date):
            last_date, last = d, s
    if last is None:
        return
    days = (today - last_date).days
    name = templates.get(last.get("template_id"), "Freeform")
    bits = [f"Last: {name}" if name else "Last session"]
    if last.get("duration_minutes"):
        bits.append(f"{last['duration_minutes']} min")
    bits.append("today" if days == 0 else
                ("yesterday" if days == 1 else f"{days} days ago"))
    tracked.append({
        "id": "workout-last",
        "area": "Workout",
        "title": " · ".join(bits),
        "detail": ("Streak from recurring fitness missions "
                   "shows in Today's quests."),
        "days": days,
        "severity": "warning" if days >= 3 else "info",
        "action": "Open on phone",
        "proposal": None,
    })


PROPERTY_CATEGORIES = ("Appliance", "Property", "Tool")


def map_garage_cards(data_dir, today, attention, next_up):
    """Due/overdue maintenance for garage-category assets (vehicles/tools).

    Property cards deliberately skip these categories; the garage owns
    its own alerts so the mower's oil change surfaces like everything else.
    """
    p = Path(data_dir)
    raw = load_json(str(p / "maintenance.json"))
    if isinstance(raw, dict):
        assets, tasks = raw.get("assets", []), raw.get("tasks", [])
    elif isinstance(raw, list):
        assets, tasks = raw, []
    else:
        return
    if not tasks and (p / "maintenance_tasks.json").is_file():
        tasks = [x for x in load_list(p / "maintenance_tasks.json")
                 if isinstance(x, dict)]
    by_id = {a.get("asset_id"): a for a in assets if isinstance(a, dict)}
    for x in tasks:
        if not isinstance(x, dict):
            continue
        a = by_id.get(x.get("asset_id"), {})
        if not _is_garage_category(a.get("category")):
            continue
        last = parse_date(x.get("last_completed"))
        interval = x.get("interval_days")
        title = x.get("title", "Maintenance task")
        aname = a.get("name", "")
        label = f"{title}: {aname}" if aname else title
        if interval and last:
            due = last + dt.timedelta(days=int(interval))
            days = (due - today).days
            card = {
                "id": f"garage-task-{x.get('task_id')}",
                "area": "Garage",
                "title": label,
                "severity": "info",
                "action": "Open on phone",
                "proposal": None,
            }
            if days < 0:
                card.update({
                    "detail": f"Overdue {-days} day{'s' if -days != 1 else ''} "
                              f"(was due {due.isoformat()}).",
                    "severity": "warning",
                })
                attention.append(card)
            elif days <= 14:
                card.update({
                    "detail": f"Due in {days} day{'s' if days != 1 else ''} "
                              f"({due.isoformat()}).",
                    "severity": "warning" if days <= 3 else "info",
                })
                next_up.append(card)


def map_property_cards(data_dir, today, attention, next_up):
    p = Path(data_dir)
    assets = {a.get("asset_id"): a for a in load_list(p / "maintenance.json")
              if isinstance(a, dict)}
    tasks = [t_ for t_ in load_list(p / "maintenance_tasks.json")
             if isinstance(t_, dict)] if (p / "maintenance_tasks.json").is_file() else []
    if not tasks:
        # maintenance.json may hold {"assets":[...],"tasks":[...]}
        raw = load_json(str(p / "maintenance.json"))
        if isinstance(raw, dict):
            assets = {a.get("asset_id"): a for a in raw.get("assets", [])}
            tasks = raw.get("tasks", [])
    for t_ in tasks:
        a = assets.get(t_.get("asset_id"), {})
        if a.get("category") not in PROPERTY_CATEGORIES:
            continue
        last = parse_date(t_.get("last_completed"))
        interval = t_.get("interval_days")
        title = t_.get("title", "Maintenance task")
        aname = a.get("name", "")
        if interval and last:
            due = last + dt.timedelta(days=int(interval))
            days = (due - today).days
            if days < 0:
                attention.append({
                    "id": f"maint-task-{t_.get('task_id')}",
                    "area": "Property",
                    "title": f"{title}: {aname}",
                    "detail": f"Overdue {-days} day{'s' if -days != 1 else ''} "
                              f"(was due {due.isoformat()}).",
                    "severity": "critical",
                    "action": "Open on phone",
                    "proposal": None,
                })
            elif days <= 14:
                next_up.append({
                    "id": f"maint-task-{t_.get('task_id')}",
                    "area": "Property",
                    "title": f"{title}: {aname}",
                    "detail": f"Due in {days} day{'s' if days != 1 else ''} ({due.isoformat()}).",
                    "severity": "warning" if days <= 3 else "info",
                    "action": "Open on phone",
                })
    for a in assets.values():
        if a.get("category") not in PROPERTY_CATEGORIES:
            continue
        wu = parse_date(a.get("warranty_until"))
        if wu:
            days = (wu - today).days
            if 0 <= days <= 30:
                attention.append({
                    "id": f"warranty-{a.get('asset_id')}",
                    "area": "Property",
                    "title": f"Warranty ending: {a.get('name', '')}",
                    "detail": f"Coverage ends in {days} day{'s' if days != 1 else ''} ({wu.isoformat()}).",
                    "severity": "warning",
                    "action": "Open on phone",
                    "proposal": None,
                })


def _area_card(cards, prefix):
    for c in cards:
        if (c.get("area") or "").startswith(prefix):
            return c
    return None


def _top_titles(cards, n=2):
    return "; ".join(c.get("title", "") for c in cards[:n] if c.get("title"))


def build_brief(attention, proposals, tracked, quests):
    """One-line natural-language summaries for voice surfaces.

    Derived from the mapped cards so voice always agrees with the glance.
    Missing data says so honestly ("No budget data right now.").
    """
    if attention:
        n = len(attention)
        b_attention = f"{n} thing{'s' if n != 1 else ''} need{'s' if n == 1 else ''} you"
        tt = _top_titles(attention)
        b_attention += f": {tt}." if tt else "."
    else:
        b_attention = "Nothing needs you."
    if proposals:
        n = len(proposals)
        b_proposals = f"{n} proposal{'s' if n != 1 else ''} waiting"
        tt = _top_titles(proposals, 1)
        b_proposals += f": {tt}." if tt else "."
    else:
        b_proposals = "No proposals waiting."
    xp = sum(int(q.get("xp") or 0) for q in quests if isinstance(q, dict))
    n = len(quests)
    b_quests = (f"{n} quest{'s' if n != 1 else ''} open"
                + (f", {xp} XP available." if xp else "."))
    gh = _area_card(tracked, "Greenhouse")
    b_greenhouse = gh.get("title") if gh else "No live greenhouse data right now."
    fin = _area_card(attention, "Budget") or _area_card(tracked, "Budget")
    b_finance = fin.get("title") if fin else "No budget data right now."
    b_bills = LAST_BILLS_LINE["text"] or "No bill data right now."
    wrk = _area_card(tracked, "Workout")
    b_workout = wrk.get("title") if wrk else "No workout data right now."
    kit = _area_card(tracked, "Kitchen")
    b_kitchen = kit.get("title") if kit else "No pantry data right now."
    return {
        "greenhouse": b_greenhouse,
        "attention": b_attention,
        "proposals": b_proposals,
        "quests": b_quests,
        "finance": b_finance,
        "bills": b_bills,
        "workout": b_workout,
        "kitchen": b_kitchen,
    }


SEV_RANK = {"critical": 0, "warning": 1, "info": 2}


def build_answer(attention, proposals, quests):
    """Context Engine v1 — "what does this moment deserve?"

    Scores every candidate (attention cards, waiting proposals, today's
    quests) with explainable weights, then composes the answer from the
    top-ranked item. Weights live in CONTEXT_WEIGHTS so they can be tuned
    against real data; each candidate keeps its basis in `reason`.
    """
    ranked = _score_candidates(attention, proposals, quests)
    n_att = len(attention)
    parts = []
    if proposals:
        parts.append(f"{len(proposals)} proposal{'s' if len(proposals) != 1 else ''} waiting")
    if quests:
        parts.append(f"{len(quests)} quest{'s' if len(quests) != 1 else ''} open")
    if not ranked:
        return {"headline": "Everything is quiet.",
                "focus": None,
                "sub": (" · ".join(parts) + ".") if parts else "No proposals or quests waiting.",
                "reason": ["nothing scored — quiet state"]}
    score, kind, basis, obj = ranked[0]
    focus = {"id": obj.get("id"), "title": obj.get("title"),
             "area": obj.get("area"), "score": score,
             "why": ", ".join(basis)}
    if score >= CONTEXT_WEIGHTS["urgent_line"]:
        if n_att > 1:
            headline = f"{n_att} things need you — {obj.get('title')} first."
        else:
            headline = f"{obj.get('title')} needs you."
    elif n_att > 1:
        headline = f"{n_att} things deserve you — {obj.get('title')} first."
    elif kind == "quest":
        headline = f"Today's focus: {obj.get('title')}."
    else:
        headline = obj.get("title", "Something deserves you.")
    sub_bits = []
    if len(ranked) > 1:
        second = ranked[1][3]
        sub_bits.append(f"Also: {second.get('title')}.")
    sub_bits.extend(parts)
    sub = " ".join(sub_bits) if sub_bits else "Nothing else waiting."
    reason = [f"{o.get('title')} — {s} ({', '.join(b)})"
              for (s, _k, b, o) in ranked[:3]]
    return {"headline": headline, "focus": focus, "sub": sub, "reason": reason}


RARITY_TIERS = [(350, "Legendary"), (200, "Epic"), (100, "Rare"),
                (50, "Uncommon"), (0, "Common")]


def rarity_for(xp):
    """Borderlands-flavored rarity from XP value."""
    for floor, name in RARITY_TIERS:
        if (xp or 0) >= floor:
            return name
    return "Common"


def quest_voice(q):
    """MVS-style quest narration.

    The calm title stays in sections; the epic name carries the game
    voice for announcements, quest cards, and voice. Announcements are
    the exact spoken/banner formats:
      NEW MISSION -> "New Mission: <epic>!" + reward lines
      COMPLETE    -> "Mission: <epic> Complete!" + reward lines + total
    """
    epic = q.get("epic_name") or q.get("title", "Unnamed quest")
    xp = q.get("xp", 0) or 0
    bonus = [b for b in (q.get("bonus_tasks") or []) if isinstance(b, dict)]
    reward_lines = [f"+{xp} XP"]
    total = xp
    for b in bonus:
        bx = b.get("xp", 0) or 0
        reward_lines.append(f"+{bx} XP — {b.get('name', 'Bonus')}")
        total += bx
    return {
        "epic_title": epic,
        "rarity": rarity_for(xp),
        "new_mission": f"New Mission: {epic}",
        "complete": f"Mission: {epic} Complete!",
        "reward_lines": reward_lines,
        "total_xp": total,
        "bonus_count": len(bonus),
    }


def load_quest_packs():
    """Opt-in quest content packs. Universal engine, personal content:
    packs are how MIA serves different lives — starter for everyone,
    seasonal-celebrations (or gardener, student, parent...) only for
    subscribers. Pack quests never fire unless the pack is enabled."""
    packs = {}
    d = Path(__file__).parent / "quest_packs"
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            try:
                p = json.loads(f.read_text())
            except Exception:
                continue
            if isinstance(p, dict) and p.get("pack_id"):
                packs[p["pack_id"]] = p
    return packs


def _in_window(today, window):
    """window = {'start': 'MM-DD', 'end': 'MM-DD'}; handles year wrap."""
    if not window:
        return True
    def tup(s):
        m, d_ = s.split("-")
        return (int(m), int(d_))
    now, s, e = (today.month, today.day), tup(window["start"]), tup(window["end"])
    return s <= now <= e if s <= e else (now >= s or now <= e)


NOVELTY_DAYS = 7          # dismissed suggestions stay quiet this long
ACCEPT_SUPPRESS_DAYS = 30  # accepted-but-not-yet-applied fallback window


def _decision_suppresses(mid, d):
    """Novelty ledger: should this suggestion id stay quiet?

    Dismissed -> quiet for NOVELTY_DAYS (rejected suggestions must not
    reappear immediately). Accepted -> quiet for ACCEPT_SUPPRESS_DAYS as
    a fallback; once the mission is applied to missions.json, the
    suggestion_id on the active quest suppresses it instead, and once
    that quest is done/archived the trigger may suggest again.
    """
    if not isinstance(d, dict):
        return False
    try:
        at = dt.datetime.fromisoformat(str(d.get("at", "")))
    except ValueError:
        return True  # unparseable timestamp: stay quiet, don't nag
    age_days = (dt.datetime.now() - at).days
    if d.get("decision") == "dismissed":
        return age_days < NOVELTY_DAYS
    if d.get("decision") == "accepted":
        snap = d.get("snapshot") or {}
        if snap.get("once"):
            return True  # one-shot missions never re-suggest once accepted
        return age_days < ACCEPT_SUPPRESS_DAYS
    return False


def load_decisions(path):
    """Read the mission-decision ledger (JSONL, one {id, decision, at,
    snapshot} per line, newest wins per id). Written by tools/serve.py
    from the surface's accept/dismiss taps; the snapshot lets --apply
    materialize an accepted suggestion even if its trigger has since
    cleared."""
    decisions = {}
    p = Path(path)
    if not p.is_file():
        return decisions
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("id") and e.get("decision"):
            decisions[e["id"]] = e
    return decisions


def mission_from_suggestion(snap, date=None):
    """Materialize an accepted suggestion as a real MIA mission record.

    mission_id is {suggestion_id}-{date} so accepting the same trigger
    on different days yields distinct missions; the suggestion_id field
    lets generate_missions() suppress re-suggestion while active.
    """
    day = (date or dt.date.today()).isoformat()
    sid = snap.get("id", "gen-unknown")
    bonus = [b for b in (snap.get("bonus_tasks") or []) if isinstance(b, dict)]
    return {
        "mission_id": f"{sid}-{day}",
        "suggestion_id": sid,
        "name": snap.get("title", "Untitled mission"),
        "epic_name": snap.get("epic_name"),
        "summary": snap.get("detail", ""),
        "region": snap.get("area") or "Missions",
        "reward_xp": snap.get("xp", 0) or 0,
        "bonus_tasks": bonus,
        "status": "active",
        "source": "mia-generated",
        "accepted_at": dt.datetime.now().isoformat(timespec="seconds"),
        "profile_id": None,
    }


def apply_decisions(decisions, missions_path):
    """Append accepted suggestions to MIA's data/missions.json.

    Idempotent: skips when the mission_id is already present. Returns
    (applied, skipped). Dismissed decisions need no write — the ledger
    itself is the novelty record generate_missions() consults.
    """
    p = Path(missions_path)
    try:
        missions = json.loads(p.read_text())
    except (OSError, ValueError):
        return 0, 0
    if not isinstance(missions, list):
        return 0, 0
    have = {m.get("mission_id") for m in missions if isinstance(m, dict)}
    applied = skipped = 0
    for mid, d in (decisions or {}).items():
        if d.get("decision") != "accepted":
            continue
        snap = d.get("snapshot") or {"id": mid}
        mission = mission_from_suggestion(snap)
        if mission["mission_id"] in have:
            skipped += 1
            continue
        missions.append(mission)
        have.add(mission["mission_id"])
        applied += 1
    if applied:
        p.write_text(json.dumps(missions, indent=2))
    return applied, skipped


def generate_missions(data_dir, today, attention, next_up, tracked, quests,
                      packs=None, enabled_packs=None, decisions=None):
    """MIA invents missions herself.

    This is the differentiator Zac asked for: not a self-managed quest
    log, but missions *proposed by her* from what she notices in the
    module state. Each suggestion carries its reason ("MIA noticed..."),
    a calm title for sections, and an epic name for the game voice.
    Suggestions are proposals — accepting one is always confirm-gated
    on the surface, never automatic.
    """
    if not data_dir:
        return []
    suggested = []
    have = {(q.get("area"), (q.get("title") or "").lower()) for q in quests}
    # Suggestion ids already live as active missions (accepted earlier and
    # applied) suppress re-suggestion while the mission is still active.
    have_ids = {q.get("suggestion_id") for q in quests if q.get("suggestion_id")}
    suppressed = {mid for mid, d in (decisions or {}).items()
                  if _decision_suppresses(mid, d)}

    def propose(mid, region, title, epic, detail, reason, xp, bonus_tasks=None,
                once=False):
        if (region, title.lower()) in have:
            return  # don't suggest what's already a quest
        if mid in have_ids or mid in suppressed:
            return  # accepted/dismissed recently — novelty ledger
        q = {"id": mid, "area": region, "title": title, "detail": detail,
             "xp": xp, "epic_name": epic, "bonus_tasks": bonus_tasks or []}
        suggested.append({
            "id": mid, "area": region, "title": title,
            "detail": detail, "xp": xp,
            "epic_name": epic, "bonus_tasks": bonus_tasks or [],
            "once": once,
            "rarity": rarity_for(xp),
            "reason": reason,
            "source": "mia-generated",
            "voice": quest_voice(q),
        })
        have.add((region, title.lower()))

    # Cross-module procurement signals, computed early: the standalone
    # grocery trigger yields when the grand supply run absorbs it, and an
    # active supply-run mission covers groceries while it is open.
    PROCURE_WORDS = ("buy ", "order ", "pick up", "parts", "replace ",
                     "refill", "restock", "low on")
    supply_signals = []  # (area, label)
    _seen_signal = set()
    def _add_signal(area, label):
        # Dedupe by title: a tool task may surface as both Property and
        # Garage cards by design; it is still one errand.
        key = (label or "").lower()
        if key and key not in _seen_signal:
            _seen_signal.add(key)
            supply_signals.append((area, label))
    for c in next_up:
        if c.get("id") == "grocery-list":
            _add_signal(c.get("area", "Kitchen"),
                        c.get("title", "groceries"))
    for c in list(attention) + list(next_up):
        cid = c.get("id", "")
        if cid in ("grocery-list",) or cid.startswith("bill-"):
            continue
        blob = (c.get("title", "") + " " + c.get("detail", "")).lower()
        if any(w in blob for w in PROCURE_WORDS):
            _add_signal(c.get("area", ""), c.get("title", ""))
    supply_areas = {a for a, _ in supply_signals if a}
    supply_run_fires = (
        len(supply_signals) >= 2 and len(supply_areas) >= 2
        and "gen-supply-run" not in have_ids
        and "gen-supply-run" not in suppressed
    )
    supply_run_active = any(q.get("suggestion_id") == "gen-supply-run"
                            for q in quests)

    # 1. Expiring food -> use-it-up quest
    for c in attention:
        if c.get("id") == "pantry-expiring":
            propose("gen-rot-horde", "Kitchen", "Use up the expiring food",
                    "Defeat the Rot Horde!",
                    f"Before they turn: {c.get('detail', '')}",
                    "MIA noticed expiring items: "
                    f"{c.get('detail', 'check the pantry')}",
                    30)
    # 2. Unchecked groceries -> provision run (yields when the grand
    # supply run absorbs it, or while an accepted supply run is open)
    for c in next_up:
        if c.get("id") == "grocery-list":
            if supply_run_fires or supply_run_active:
                continue
            propose("gen-provisions", "Kitchen", "Do the grocery run",
                    "Gather Provisions!",
                    f"Waiting on the list: {c.get('detail', '')}",
                    "MIA noticed the grocery list has unchecked items",
                    25)
    # 3. Maintenance due -> asset quests. Dedupe by title: a tool task
    # may surface as both a Property and a Garage card by design.
    _seen_maint = set()
    for c in list(attention) + list(next_up):
        cid = c.get("id", "")
        if cid.startswith(("maint-task-", "garage-task-")):
            title = c.get("title", "Maintenance task")
            if title.lower() in _seen_maint:
                continue
            _seen_maint.add(title.lower())
            asset = title.split(":")[-1].strip() if ":" in title else title
            if "mower" in asset.lower() or "deere" in asset.lower():
                epic = "Feed the Steel Beast!"
            elif c.get("area") == "Greenhouse":
                epic = "Tend the Living Machine!"
            else:
                epic = f"Tend the {asset}!"
            propose(f"gen-{cid}", c.get("area", "Property"), title, epic,
                    c.get("detail", ""),
                    f"MIA noticed this maintenance is coming due: {title}",
                    40)
    # 4. Over-target spending -> vault audit
    for c in attention:
        if "over budget" in (c.get("title", "") + c.get("detail", "")).lower():
            cat = c.get("title", "spending").replace(" over budget", "")
            propose("gen-audit-vault", "Budget",
                    f"Reign in {cat} spending", "Audit the Vault!",
                    c.get("detail", ""),
                    f"MIA noticed {cat} is over its monthly target",
                    40)
    # 5. Workout gap -> tiered rekindle quests (only if no workout quest
    # exists). days < 3 means he just worked out — no mission. (This used
    # to fire even the same day; fixed.)
    def _is_workout_quest(q):
        blob = f"{q.get('area', '')} {q.get('title', '')}".lower()
        return ("workout" in blob or "fitness" in blob or "gym" in blob
                or "run" in blob or "push" in blob or "lift" in blob)
    for c in tracked:
        if c.get("id") == "workout-last" and not any(
                _is_workout_quest(q) for q in quests):
            days = c.get("days", 0) or 0
            if days >= 7:
                propose("gen-rekindle", "Workout", "Get moving again",
                        "Reignite the Inferno!",
                        c.get("detail", ""),
                        f"MIA noticed it's been {days} days since your "
                        f"last workout",
                        50)
            elif days >= 3:
                propose("gen-rekindle", "Workout", "Get moving again",
                        "Rekindle the Flame!",
                        c.get("detail", ""),
                        f"MIA noticed it's been {days} days since your "
                        f"last workout",
                        35)
    # 6b. Seasonal pack quests — calendar-driven, only for subscribers
    packs = packs or {}
    enabled = enabled_packs if enabled_packs is not None else {
        pid for pid, p in packs.items() if p.get("default_enabled")}
    for pid in enabled:
        p = packs.get(pid, {})
        for m in p.get("quests", []):
            if not isinstance(m, dict):
                continue
            if not m.get("window"):
                continue  # library content, not a calendar trigger
            if not _in_window(today, m.get("window")):
                continue
            mid = f"gen-{m.get('mission_id')}"
            if any(g.get("id") == mid for g in suggested):
                continue
            if mid in have_ids or mid in suppressed:
                continue  # accepted/dismissed recently — novelty ledger
            q = {"id": mid, "area": m.get("region") or "Missions",
                 "title": m.get("name", "Seasonal quest"),
                 "detail": m.get("summary", ""),
                 "xp": m.get("reward_xp", 0),
                 "epic_name": m.get("epic_name"),
                 "bonus_tasks": m.get("bonus_tasks", [])}
            if (q["area"], q["title"].lower()) in have:
                continue
            suggested.append({
                "id": mid, "area": q["area"], "title": q["title"],
                "detail": q["detail"], "xp": q["xp"],
                "epic_name": m.get("epic_name"),
                "bonus_tasks": m.get("bonus_tasks", []),
                "rarity": rarity_for(q["xp"]),
                "reason": (f"MIA noticed it's that time of year — "
                           f"{p.get('name', pid)} pack"),
                "source": "mia-generated", "pack_id": pid,
                "voice": quest_voice(q),
            })
            have.add((q["area"], q["title"].lower()))

    # 6. Overdue bills -> settle reminders (reminders only — paying stays
    #    phone/desktop-only; she proposes the nudge, never the transfer)
    for c in attention:
        if c.get("area") == "Budget" and "overdue" in c.get("title", "").lower():
            bill = c.get("title", "bill").replace(" is overdue", "")
            propose(f"gen-settle-{c.get('id', 'bill')}", "Budget",
                    f"Settle the {bill}", "Settle the Debt Scroll!",
                    c.get("detail", ""),
                    f"MIA noticed the {bill} is overdue",
                    15)

    # 7. Bills due soon — same scroll, earlier. Reminder only.
    for c in next_up:
        cid = c.get("id", "")
        if not cid.startswith("bill-"):
            continue
        title = c.get("title", "")
        if " due in " not in title:
            continue
        name, when = title.split(" due in ", 1)
        try:
            days = int(when.split()[0])
        except (ValueError, IndexError):
            continue
        if days <= 3:
            propose(f"gen-{cid}-soon", "Budget",
                    f"Settle the {name}", "Settle the Debt Scroll!",
                    c.get("detail", ""),
                    f"MIA noticed the {name} is due in {days} "
                    f"{'day' if days == 1 else 'days'}",
                    15)

    # 9. Completed experiments -> review the results. One-shot: once
    # accepted it never re-suggests; dismissed it stays quiet a week.
    for c in tracked:
        cid = c.get("id", "")
        if not cid.startswith("exp-done-"):
            continue
        label = c.get("title", "experiment")
        propose(f"gen-review-{cid[len('exp-done-'):]}", "Greenhouse",
                f"Review the {label} results", "Decipher the Harvest Codex!",
                c.get("detail", ""),
                f"MIA noticed the {label} experiment finished — "
                f"results are in",
                45, once=True)

    # 10. Cross-module supply run — the "you're already going to Lowe's"
    # synthesis: errands stacking up across areas become one trip.
    if supply_run_fires:
        detail = "; ".join(f"{a}: {label}"
                           for a, label in supply_signals[:6])
        propose("gen-supply-run", "Missions",
                "Do the supply run", "The Grand Supply Run!",
                detail,
                f"MIA noticed errands stacking up across {len(supply_areas)} "
                f"areas — one trip could clear them",
                40)

    # 11. Attention avalanche — several things need him at once: one
    # focused sweep instead of scattered triage.
    if len(attention) >= 3:
        titles = "; ".join(c.get("title", "") for c in attention[:4])
        if len(attention) > 4:
            titles += "…"
        propose("gen-tame-hydra", "Missions",
                "Clear the decks", "Tame the Hydra!",
                titles,
                f"MIA noticed {len(attention)} things need you at once — "
                f"one focused sweep",
                45)
    return suggested


CONTEXT_WEIGHTS = {
    "critical": 160, "warning": 100, "info": 40,
    "overdue_per_day": 5,
    "money_bonus": 25,      # Budget-area items: money is actually at stake
    "living_bonus": 40,     # Greenhouse: living systems suffer in silence
    "quest_today": 60,
    "streak_risk": 50,      # streak >= 3 not yet done today
    "proposal_waiting": 70,  # Gate A/B approvals are decisions, not tasks
    "urgent_line": 90,       # >= this reads as "needs you", below as "deserves you"
}

_OVERDUE_DAYS_RE = re.compile(r"overdue (\d+) day", re.IGNORECASE)
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _extract_overdue_days(text):
    """Pull an overdue-day count from card text (due_line formats)."""
    m = _OVERDUE_DAYS_RE.search(text or "")
    if m:
        return int(m.group(1))
    days = 0
    today = dt.date.today()
    for m in _DATE_RE.finditer(text or ""):
        try:
            d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        delta = (today - d).days
        if 0 < delta and delta > days:
            days = delta
    return days


def _score_candidates(attention, proposals, quests):
    """Score -> [(score, kind, basis, card)] sorted best-first."""
    ranked = []
    for c in attention:
        sev = c.get("severity", "info")
        score = CONTEXT_WEIGHTS.get(sev, 40)
        basis = [sev]
        text = f"{c.get('title', '')} {c.get('detail', '')}"
        days = _extract_overdue_days(text)
        if days:
            score += days * CONTEXT_WEIGHTS["overdue_per_day"]
            basis.append(f"{days}d overdue")
        if c.get("area") == "Budget":
            score += CONTEXT_WEIGHTS["money_bonus"]
            basis.append("money at stake")
        if c.get("area") == "Greenhouse" and "overdue" in text.lower():
            score += CONTEXT_WEIGHTS["living_bonus"]
            basis.append("living system")
        ranked.append((score, "attention", basis, c))
    if proposals:
        score = CONTEXT_WEIGHTS["proposal_waiting"]
        first = proposals[0]
        ranked.append((score, "proposal",
                       [f"{len(proposals)} waiting"],
                       {"id": "proposals", "title": f"{len(proposals)} proposal"
                        f"{'s' if len(proposals) != 1 else ''} waiting",
                        "area": "Proposals",
                        "detail": first.get("title", "")}))
    for q in quests:
        score = CONTEXT_WEIGHTS["quest_today"]
        basis = ["quest due today"]
        streak = q.get("streak", 0) or 0
        if not streak:
            m = re.search(r"streak[:\s]*(\d+)", str(q.get("detail", "")),
                          re.IGNORECASE)
            streak = int(m.group(1)) if m else 0
        if streak >= 3:
            score += CONTEXT_WEIGHTS["streak_risk"]
            basis.append(f"{streak}-day streak at risk")
        ranked.append((score, "quest", basis,
                       {"id": q.get("id"), "title": q.get("title"),
                        "area": q.get("area"), "detail": q.get("detail", "")}))
    return sorted(ranked, key=lambda x: -x[0])


def load_missions_quests(missions_path, profile_id=None):
    """Extract today's active quests from MIA's data/missions.json.

    Missions are a flat JSON array (see TheKingCS/MIA core/mission_manager.py).
    A mission counts as "today's" when it is active, visible to the profile,
    and either non-recurring or a daily occurrence stamped with today's date.
    """
    import datetime
    missions = load_json(missions_path)
    if not isinstance(missions, list):
        return []
    today = datetime.date.today().isoformat()
    quests = []
    for m in missions:
        if m.get("status") != "active":
            continue
        if m.get("profile_id") not in (None, profile_id):
            continue
        recurring = m.get("recurring_kind")
        if recurring == "daily" and m.get("occurrence_key") != today:
            continue
        if recurring == "weekly":
            continue  # weekly occurrences use {start}-W{n} keys; leave to a future pass
        quests.append({
            "id": m.get("mission_id"),
            "suggestion_id": m.get("suggestion_id"),
            "area": m.get("region") or "Missions",
            "title": m.get("name", "Unnamed mission"),
            "detail": m.get("summary", ""),
            "xp": m.get("reward_xp", 0),
            "streak": m.get("streak", 0),
            "epic_name": m.get("epic_name"),
            "bonus_tasks": m.get("bonus_tasks", []),
        })
    return quests


def load_json(path):
    p = Path(path)
    if not p.is_file():
        return None
    return json.loads(p.read_text())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dashboard", default=None,
                    help="Path to homestead viewer/dashboard_state.json")
    ap.add_argument("--db", default=None,
                    help="Path to homestead mia_homestead.db — reads the "
                         "parameter-change approval queue (Gate B proposals)")
    ap.add_argument("--quests", default=None,
                    help="Path to a quests JSON file (list of {id,title,detail,xp,area})")
    ap.add_argument("--missions", default=None,
                    help="Path to MIA data/missions.json — today's active missions "
                         "become the quest list (takes precedence over --quests)")
    ap.add_argument("--mia-data", default=None,
                    help="Path to MIA's data/ directory — budget, real estate, "
                         "kitchen, workout, and property-maintenance cards "
                         "(reads the module JSON files directly)")
    ap.add_argument("--profile", default=None,
                    help="MIA profile_id for mission visibility (default: shared only)")
    ap.add_argument("--enable-packs", default=None,
                    help="Comma-separated quest pack ids to enable "
                         "(default: packs with default_enabled=true)")
    ap.add_argument("--decisions", default=None,
                    help="Path to mission_decisions.jsonl — accept/dismiss "
                         "taps from the surface (default: next to --out). "
                         "Dismissed suggestions are suppressed for "
                         "%d days; accepted ones are suppressed too." % NOVELTY_DAYS)
    ap.add_argument("--apply-decisions", action="store_true",
                    help="Materialize accepted suggestions as active "
                         "missions in the --missions file (requires "
                         "--missions). Idempotent.")
    ap.add_argument("--out", default="webapp/state.json")
    args = ap.parse_args()

    dash = load_json(args.dashboard) if args.dashboard else None

    attention, next_up, proposals, tracked = [], [], [], []
    dash_generated = None
    cell_names, hypotheses = {}, {}
    if dash:
        dash_generated = dash.get("generated_at")
        for c in dash.get("cells", []) or []:
            if isinstance(c, dict) and c.get("id") is not None:
                cell_names[c["id"]] = c.get("name") or f"Cell {c['id']}"
                if c.get("readings"):
                    tracked.append(map_tracked_cell(c))
        for a in dash.get("alerts", []) or []:
            attention.append(map_alert(a))
        for m in dash.get("maintenance", []) or []:
            next_up.append(map_maintenance(m))
        for e in dash.get("experiments", []) or []:
            if isinstance(e, dict):
                if e.get("status") == "proposed":
                    proposals.append(map_experiment_proposal(e, cell_names))
                elif e.get("status") == "completed":
                    # Finished experiments with results become a tracked
                    # card; generate_missions() turns it into a one-shot
                    # "review the results" quest. Defensive: unknown
                    # dashboard shapes simply yield no card.
                    result = (e.get("result_summary") or e.get("conclusion")
                              or e.get("outcome") or "")
                    if isinstance(result, str) and result.strip():
                        var = PARAM_LABELS.get(e.get("variable_changed") or "",
                                               e.get("variable_changed")
                                               or "experiment")
                        cell = cell_names.get(e.get("cell_id"),
                                              f"Cell {e.get('cell_id')}")
                        tracked.append({
                            "id": f"exp-done-{e.get('id')}",
                            "area": "Greenhouse",
                            "title": f"{var} ({cell})",
                            "detail": result.strip()[:220],
                            "severity": "info",
                            "action": "Open on phone",
                            "proposal": None,
                        })
                hypotheses[e.get("id")] = e.get("hypothesis", "")
    if args.db:
        for r in load_gate_b_proposals(args.db):
            proposals.append(map_param_change_proposal(r, cell_names, hypotheses))
    if args.mia_data:
        _today = dt.date.today()
        map_budget_cards(args.mia_data, _today, attention, next_up, tracked)
        map_real_estate_cards(args.mia_data, _today, tracked)
        map_kitchen_cards(args.mia_data, _today, attention, next_up, tracked)
        map_workout_cards(args.mia_data, _today, tracked)
        map_property_cards(args.mia_data, _today, attention, next_up)
        map_garage_cards(args.mia_data, _today, attention, next_up)

    quests = []
    if args.missions:
        quests = load_missions_quests(args.missions, args.profile)
    elif args.quests:
        q = load_json(args.quests)
        if isinstance(q, dict) and isinstance(q.get("quests"), list):
            # Quest deck file: {"deck": ..., "quests": [...]} — normalize
            # the mission record shape into quest cards.
            quests = []
            for m in q["quests"]:
                if not isinstance(m, dict):
                    continue
                quests.append({
                    "id": m.get("mission_id"),
                    "area": m.get("region") or "Missions",
                    "title": m.get("name", "Unnamed mission"),
                    "detail": m.get("summary", ""),
                    "xp": m.get("reward_xp", 0),
                    "streak": m.get("streak", 0),
                    "epic_name": m.get("epic_name"),
                    "bonus_tasks": m.get("bonus_tasks", []),
                })
        elif isinstance(q, list):
            quests = q
    else:
        # Preserve any quests already in the current state file so
        # re-exporting doesn't wipe the quest list.
        current = load_json(args.out)
        if current and isinstance(current.get("quests"), list):
            quests = current["quests"]

    for q in quests:
        if isinstance(q, dict) and "voice" not in q:
            q["voice"] = quest_voice(q)
    # Mission decisions: the surface's accept/dismiss taps. Apply accepted
    # ones to missions.json first so the reloaded quest list (and the
    # suggestion_id suppression in generate_missions) sees them this run.
    decisions_path = args.decisions or str(Path(args.out).parent / "mission_decisions.jsonl")
    decisions = load_decisions(decisions_path)
    if args.apply_decisions:
        if args.missions:
            applied, skipped = apply_decisions(decisions, args.missions)
            if applied or skipped:
                print(f"Applied mission decisions: {applied} accepted, "
                      f"{skipped} already present.")
                quests = load_missions_quests(args.missions, args.profile)
                for q in quests:
                    if isinstance(q, dict) and "voice" not in q:
                        q["voice"] = quest_voice(q)
        else:
            print("Note: --apply-decisions needs --missions; decisions not applied.")
    today = dt.date.today()
    packs = load_quest_packs()
    enabled = None
    if args.enable_packs:
        enabled = {p.strip() for p in args.enable_packs.split(",") if p.strip()}
    suggested = generate_missions(args.mia_data, today, attention,
                                  next_up, tracked, quests,
                                  packs=packs, enabled_packs=enabled,
                                  decisions=decisions)
    brief = build_brief(attention, proposals, tracked, quests)
    answer = build_answer(attention, proposals, quests)

    state = {
        "answer": answer,
        "brief": brief,
        "meta": {
            "source": "live" if (dash or args.mia_data) else "mock",
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "dashboard_generated_at": dash_generated,
            "note": "Generated by tools/export_glance_state.py from homestead state.",
        },
        "attention": attention,
        "proposals": proposals,
        "tracked": tracked,
        "next_up": next_up,
        "quests": quests,
        "suggested": suggested,
    }
    modules = {}
    if args.mia_data:
        modules["garage"] = map_garage_index(args.mia_data)
        modules["kitchen"] = map_kitchen_index(args.mia_data)
    state["modules"] = modules
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, indent=2))
    print(f"Wrote {out}: {len(attention)} attention, {len(proposals)} proposals, "
          f"{len(tracked)} tracked, {len(next_up)} next-up, "
          f"{len(quests)} quests, {len(suggested)} suggested "
          f"(source={state['meta']['source']}).")


if __name__ == "__main__":
    main()
