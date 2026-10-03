#!/usr/bin/env python3
"""Build an honest demo webapp/state.json from the real exporter.

The old committed state.json was hand-written and went stale: it
predated answer/suggested/proposals/tracked, and — worse — it contained
a fabricated individual-dosing proposal, which contradicts the verified
homestead model (proposals are Gate A experiments and Gate B parameter
changes; dosing is never a proposal).

This script instead builds a clearly-labeled synthetic fixture shaped
like the real schemas, runs tools/export_glance_state.py over it, and
stamps the output as demo data. The mock is now a build artifact of the
real pipeline, not a hand-written fiction.

Usage:
    python tools/build_demo_state.py [--out webapp/state.json]

Dates are relative to today so the demo never goes stale.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent


def iso(delta_days):
    return (date.today() + timedelta(days=delta_days)).isoformat()


def build_fixture(workdir):
    workdir = Path(workdir)
    mia_data = workdir / "mia-data"
    mia_data.mkdir(parents=True, exist_ok=True)

    dashboard = {
        "generated_at": date.today().isoformat() + "T12:00:00",
        "cells": [
            {"id": 1, "name": "Basil Bay",
             "readings": [
                 {"type": "ph", "value": 6.9, "unit": ""},
                 {"type": "ec", "value": 1.9, "unit": "mS/cm"},
                 {"type": "water_temp", "value": 72.5, "unit": "°F"}]},
            {"id": 2, "name": "Lettuce Raft",
             "readings": [
                 {"type": "ph", "value": 7.6, "unit": ""},
                 {"type": "ec", "value": 2.1, "unit": "mS/cm"}]},
        ],
        "alerts": [
            # Alerts are alerts — NOT dosing proposals. Dosing is never
            # a proposal in the homestead model.
            {"id": 101, "severity": "warning", "source": "ph_cell_2",
             "message": "pH drift high: 7.6 vs target 6.8–7.2",
             "occurrence_count": 3, "last_seen": iso(0)},
            {"id": 102, "severity": "critical", "source": "feeder_a",
             "message": "Fish feeder 2 days overdue",
             "occurrence_count": 1, "last_seen": iso(0)},
        ],
        "experiments": [
            # Gate A, the real proposal shape: a proposed experiment,
            # not an individual dose.
            {"id": 201, "status": "proposed", "cell_id": 2,
             "variable_changed": "ph",
             "hypothesis": "Holding pH at 6.5 for two weeks raises lettuce "
                           "biomass vs the 6.8 baseline.",
             "baseline_value": 6.8, "test_value": 6.5,
             "planned_end_date": iso(14),
             "proposed_at": iso(-1), "created_by": "demo"},
        ],
        "maintenance": [
            {"id": 301, "title": "Sharpen mower blades",
             "detail": "Both rotary blades. Overdue 12 days.",
             "status": "open"},
        ],
    }
    (workdir / "dashboard.json").write_text(json.dumps(dashboard, indent=2))

    (mia_data / "bills.json").write_text(json.dumps([
        {"bill_id": "demo-electric", "name": "Electric",
         "due_date": iso(-2), "amount": 142.50},
        {"bill_id": "demo-internet", "name": "Internet",
         "due_date": iso(2), "amount": 79.99},
    ], indent=2))
    (mia_data / "kitchen_pantry.json").write_text(json.dumps([
        {"name": "Milk", "expiration_date": iso(0)},
        {"name": "Spinach", "expiration_date": iso(1)},
        {"name": "Rice", "expiration_date": iso(90)},
    ], indent=2))
    (mia_data / "kitchen_grocery_list.json").write_text(json.dumps([
        {"name": "Eggs", "checked": False},
        {"name": "Butter", "checked": False},
    ], indent=2))
    (mia_data / "workout_sessions.json").write_text(json.dumps([
        {"date": iso(-4), "template_id": "push", "duration_minutes": 42},
    ], indent=2))
    (mia_data / "workout_templates.json").write_text(json.dumps([
        {"template_id": "push", "name": "Push day"},
    ], indent=2))
    (mia_data / "maintenance.json").write_text(json.dumps({
        "assets": [{"asset_id": "demo-mower", "name": "Demo mower",
                    "category": "Tool"}],
        "tasks": [{"task_id": "demo-t1", "asset_id": "demo-mower",
                   "title": "Replace air filter",
                   "last_completed": iso(-28), "interval_days": 30}],
    }, indent=2))
    return workdir / "dashboard.json", mia_data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="webapp/state.json")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="mia-demo-") as tmp:
        dash, mia_data = build_fixture(tmp)
        deck = HERE / "quest_deck.json"
        cmd = [sys.executable, str(HERE / "export_glance_state.py"),
               "--dashboard", str(dash),
               "--mia-data", str(mia_data),
               "--quests", str(deck),
               "--out", args.out]
        r = subprocess.run(cmd, capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        if r.returncode != 0:
            sys.exit(r.returncode)

    out = Path(args.out)
    state = json.loads(out.read_text())
    state["meta"]["source"] = "mock"
    state["meta"]["note"] = (
        "Synthetic demo data — generated by tools/build_demo_state.py from "
        "labeled fixtures shaped like the real schemas. No real homestead "
        "or MIA data. The proposal shown is a Gate A experiment proposal; "
        "individual dosing is never a proposal.")
    # Demo-only honesty scrub: no proposal may be an individual-dosing
    # proposal (the old mock's fabrication). The safety copy may mention
    # "dose" — that is the real guarantee, not a proposal.
    for p in state.get("proposals", []):
        prop = p.get("proposal") or {}
        blob = json.dumps(prop).lower()
        assert prop.get("kind") in ("experiment", "parameter_change", None), \
            f"unexpected proposal kind: {prop.get('kind')}"
        assert "dose " not in p.get("title", "").lower(), \
            f"dosing proposal slipped into demo: {p.get('title')}"
    out.write_text(json.dumps(state, indent=2))
    print(f"Labeled {out} as synthetic demo data.")


if __name__ == "__main__":
    main()
