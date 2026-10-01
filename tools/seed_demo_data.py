#!/usr/bin/env python3
"""
Seed clearly-labeled demo rows into the homestead database so the
database -> dashboard export -> glance cards pipeline can be verified
end to end. Idempotent: removes its own demo rows before re-inserting.

Usage:
    python tools/seed_demo_data.py --db /path/to/mia_homestead.db

Then:
    python /path/to/mia-homestead/viewer/export_dashboard_state.py \
        --db /path/to/mia_homestead.db \
        --out /path/to/mia-homestead/viewer/dashboard_state.json
    python tools/export_glance_state.py \
        --dashboard /path/to/mia-homestead/viewer/dashboard_state.json \
        --out webapp/state.json
"""

import argparse
import sqlite3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Path to mia_homestead.db")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("DELETE FROM alerts WHERE source='ph_cell_a'")
    conn.execute("DELETE FROM maintenance_tasks WHERE title='Sharpen mower blades'")
    conn.execute(
        "INSERT INTO alerts (severity, source, message, occurrence_count) "
        "VALUES ('warning','ph_cell_a','pH drift high: 7.6 vs target 6.8-7.2',2)"
    )
    conn.execute(
        "INSERT INTO maintenance_tasks (title, description, task_type, priority, status, due_date) "
        "VALUES ('Sharpen mower blades','Both rotary blades.','repair','high','open','2026-09-19')"
    )
    conn.commit()
    alerts = conn.execute("SELECT COUNT(*) FROM alerts WHERE resolved = 0").fetchone()[0]
    maint = conn.execute(
        "SELECT COUNT(*) FROM maintenance_tasks WHERE status IN ('open','in_progress')"
    ).fetchone()[0]
    print(f"Demo rows seeded: {alerts} open alert(s), {maint} open maintenance task(s) in {args.db}")


if __name__ == "__main__":
    main()
