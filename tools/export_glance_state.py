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

    state = {
        "meta": {
            "source": "live" if dash else "mock",
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
