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
        "severity": "warning" if days >= 3 else "info",
        "action": "Open on phone",
        "proposal": None,
    })


PROPERTY_CATEGORIES = ("Appliance", "Property", "Tool")


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
    """The answer-first home object: what matters most, right now.

    Derived from the mapped cards (same source as the glance), so every
    surface states the same priority. Empty attention = the quiet state.
    """
    ranked = sorted(attention,
                    key=lambda c: SEV_RANK.get(c.get("severity", "info"), 2))
    if ranked:
        n = len(ranked)
        headline = f"{n} thing{'s' if n != 1 else ''} need{'s' if n == 1 else ''} you."
        top = ranked[0]
        focus = {"id": top.get("id"), "title": top.get("title"),
                 "area": top.get("area")}
    else:
        headline = "Everything is quiet."
        focus = None
    parts = []
    if proposals:
        n = len(proposals)
        parts.append(f"{n} proposal{'s' if n != 1 else ''} waiting")
    if quests:
        n = len(quests)
        parts.append(f"{n} quest{'s' if n != 1 else ''} open")
    sub = (" · ".join(parts) + ".") if parts else "No proposals or quests waiting."
    return {"headline": headline, "focus": focus, "sub": sub}

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
            "area": m.get("region") or "Missions",
            "title": m.get("name", "Unnamed mission"),
            "detail": m.get("summary", ""),
            "xp": m.get("reward_xp", 0),
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

    quests = []
    if args.missions:
        quests = load_missions_quests(args.missions, args.profile)
    elif args.quests:
        q = load_json(args.quests)
        if isinstance(q, list):
            quests = q
    else:
        # Preserve any quests already in the current state file so
        # re-exporting doesn't wipe the quest list.
        current = load_json(args.out)
        if current and isinstance(current.get("quests"), list):
            quests = current["quests"]

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
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, indent=2))
    print(f"Wrote {out}: {len(attention)} attention, {len(proposals)} proposals, "
          f"{len(tracked)} tracked, {len(next_up)} next-up, "
          f"{len(quests)} quests (source={state['meta']['source']}).")


if __name__ == "__main__":
    main()
