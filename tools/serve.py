#!/usr/bin/env python3
"""Dev-loop server for MIA Glance.

Serves webapp/ statically (like `python -m http.server`) and adds one
write endpoint for the mission-acceptance flow:

    POST /api/mission-decision   {id, decision, at, snapshot}
      -> appends a JSON line to webapp/mission_decisions.jsonl

The exporter picks that file up via --decisions (default: next to
--out) and --apply-decisions materializes accepted suggestions as real
missions in MIA's data/missions.json. Dismissed ids feed the novelty
ledger (quiet for 7 days).

This is the LOCAL dev loop only (laptop -> Cloudflare tunnel -> glasses).
The production path is the authenticated /api/bridge/* on MIA's phone
server (see docs/muse-bridge-spec.md); the webapp POSTs to this same
relative path and falls back to on-device queueing when it is absent.

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

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split("?")[0] != "/api/mission-decision":
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"error": "invalid JSON"})
        if payload.get("decision") not in ("accepted", "dismissed") or not payload.get("id"):
            return self._json(400, {"error": "need {id, decision: accepted|dismissed}"})
        entry = {
            "id": payload["id"],
            "decision": payload["decision"],
            "at": payload.get("at") or datetime.now(timezone.utc).isoformat(),
            "snapshot": payload.get("snapshot") or {"id": payload["id"]},
        }
        p = Path(self.decisions_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        return self._json(200, {"ok": True})

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
    handler = partial(Handler, directory=str(root))
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"Serving {root}/ on http://127.0.0.1:{args.port} "
          f"(POST /api/mission-decision -> {Handler.decisions_file})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
