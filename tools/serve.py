#!/usr/bin/env python3
"""Dev-loop server for MIA Glance.

Serves webapp/ statically (like `python -m http.server`) and adds two
write endpoints for the confirm-gated flows:

    POST /api/mission-decision   {id, decision, at, snapshot}
      -> appends a JSON line to webapp/mission_decisions.jsonl

    POST /api/workout-log       {date, name, duration_minutes}
      -> appends a JSON line to webapp/workout_logs.jsonl
         (channel: voice; applied by exporter --apply-workouts)

    POST /api/voice-settings      {frequency, verbosity, ...}
      -> writes webapp/voice_settings.json (gitignored); the exporter
         merges it into state.voice.settings on the next export

The exporter picks those files up via --decisions / --workout-logs
(defaults: next to --out); --apply-decisions materializes accepted
suggestions as real missions in MIA's data/missions.json and
--apply-workouts appends voice logs to workout_sessions.json.
Dismissed suggestion ids feed the novelty ledger (quiet for 7 days).

This is the LOCAL dev loop only (laptop -> Cloudflare tunnel -> glasses).
The production path is the authenticated /api/bridge/* on MIA's phone
server (see docs/muse-bridge-spec.md); the webapp POSTs to these same
relative paths and falls back to on-device queueing when absent.

Usage:
    python tools/serve.py [--port 8080] [--dir webapp]
"""
import argparse
import json
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(SimpleHTTPRequestHandler):
    decisions_file = None
    workout_logs_file = None
    voice_settings_file = None

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _append_jsonl(self, path, entry):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"error": "invalid JSON"})
        if path == "/api/mission-decision":
            if payload.get("decision") not in ("accepted", "dismissed") or not payload.get("id"):
                return self._json(400, {"error": "need {id, decision: accepted|dismissed}"})
            entry = {
                "id": payload["id"],
                "decision": payload["decision"],
                "at": payload.get("at") or datetime.now(timezone.utc).isoformat(),
                "snapshot": payload.get("snapshot") or {"id": payload["id"]},
            }
            self._append_jsonl(self.decisions_file, entry)
            return self._json(200, {"ok": True})
        if path == "/api/workout-log":
            # Confirm-gated on the surface; the server only records what
            # the user explicitly confirmed. Exporter --apply-workouts
            # materializes these into workout_sessions.json.
            if not payload.get("date") or not payload.get("name"):
                return self._json(400, {"error": "need {date, name}"})
            entry = {
                "date": payload["date"],
                "name": payload["name"],
                "duration_minutes": payload.get("duration_minutes"),
                "logged_at": payload.get("logged_at")
                             or datetime.now(timezone.utc).isoformat(),
                "channel": "voice",
            }
            self._append_jsonl(self.workout_logs_file, entry)
            return self._json(200, {"ok": True})
        if path == "/api/voice-settings":
            # Per-user voice preferences: frequency/verbosity presets,
            # spoken-mission toggle, master voice switch. The exporter
            # merges this file into state.voice.settings on next export.
            if not isinstance(payload, dict):
                return self._json(400, {"error": "need a JSON object"})
            allowed = {"frequency", "verbosity", "speak_missions",
                       "voice_enabled", "max_proactive_per_day",
                       "min_spacing_minutes"}
            clean = {k: v for k, v in payload.items() if k in allowed}
            p = Path(self.voice_settings_file)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(clean, indent=2))
            return self._json(200, {"ok": True, "settings": clean})
        return self._json(404, {"error": "not found"})

    def log_message(self, fmt, *args):  # quieter than the default
        if self.path.startswith("/api/"):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--dir", default="webapp")
    args = ap.parse_args()
    root = Path(args.dir)
    Handler.decisions_file = str(root / "mission_decisions.jsonl")
    Handler.workout_logs_file = str(root / "workout_logs.jsonl")
    Handler.voice_settings_file = str(root / "voice_settings.json")
    handler = partial(Handler, directory=str(root))
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"Serving {root}/ on http://127.0.0.1:{args.port} "
          f"(POST /api/mission-decision -> {Handler.decisions_file}; "
          f"POST /api/workout-log -> {Handler.workout_logs_file}; "
          f"POST /api/voice-settings -> {Handler.voice_settings_file})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
