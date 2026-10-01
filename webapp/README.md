# MIA Glance — web app prototype (Phase 1)

Glanceable MIA cards for Meta Ray-Ban Display: **Needs attention**,
**Next up**, and **Today's quests** — the approved Phase 1 card set.
Built to the current Web Apps spec: Chromium runtime, responsive layout
(600×600 is the validation target, never a hardcoded size), directional
navigation (↑↓ + Enter), manifest at
`.well-known/meta-wearables-manifest.json`.

## Preview it now (no glasses needed)

```bash
cd webapp
python3 -m http.server 8080      # Windows: py -m http.server 8080
# open http://localhost:8080/ in Chrome
```

- Open DevTools responsive mode at **600×600** to approximate the Display.
- Use **↑/↓** to move between cards, **Enter** to select / check in a quest.
- Quest check-ins persist in `localStorage`.

## Files

- `index.html` — the whole app (no build step, no dependencies)
- `state.json` — card data; the app falls back to embedded data if the fetch fails
- `.well-known/meta-wearables-manifest.json` — Version 1 manifest (name, description, monochrome icon, theme + gradient)
- `icon.svg` — transparent monochrome artwork (64×64 design area)

## Live data: the exporter

`tools/export_glance_state.py` builds `webapp/state.json` from the real
homestead export — no mock data, no guessing:

```bash
python tools/export_glance_state.py \
  --dashboard /path/to/mia-homestead/viewer/dashboard_state.json \
  --quests tools/quests.json \
  --out webapp/state.json
```

- **attention** ← unresolved homestead alerts (severity → card strip,
  source/last-seen/repeat count → detail)
- **next_up** ← open homestead maintenance tasks (priority → severity,
  overdue computed from `due_date`, bumped to warning when overdue)
- **quests** ← your quests JSON file (copy `tools/quests.example.json`
  to start); if omitted, existing quests in `state.json` are preserved

Every source is optional — missing data renders as an honest "All clear."
Re-run the exporter (or schedule it) and reload the app to refresh.
MIA-core sources (garage maintenance, missions) plug in here next.

## Proposal approval (read / propose / approve)

Tapping (or Enter on) an attention card opens a detail view. Cards that
carry a `proposal` show what MIA proposes, the evidence behind it, and a
safety note — with **Approve** / **Dismiss** buttons navigable by ↑↓ and
Enter (Esc or ← Back returns to the list). Decisions persist in
`localStorage` and the card shows its decided state.

Approvals are **preview-only** in this prototype: in production they will
queue with the homestead controller and Safety MCU. Nothing actuates
from glasses — the safety note on every proposal says so.

`tools/export_glance_state.py` attaches a demo proposal to pH alerts
(`demo: true`); real proposals will come from the homestead
experiment/proposal queue.

## Hosting for the glasses

The glasses need the app over **HTTPS** (plain `http://` is only accepted
for loopback hosts like `localhost`). Quickest path on Windows:

1. Download `cloudflared` (64-bit .exe) from
   <https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/>
2. In a second terminal (keep the `http.server` running):
   `.\cloudflared-windows-amd64.exe tunnel --url http://localhost:8080`
3. Use the printed `https://….trycloudflare.com` URL — the manifest is
   served automatically at `<url>/.well-known/meta-wearables-manifest.json`

The trycloudflare URL changes on every restart; fine for testing.

## Loading onto the glasses

1. Meta AI app on the paired phone → enable **Developer Mode** (tap the
   app version 5 times).
2. Load the hosted HTTPS URL as a web app.

If the Meta AI app hangs while loading account/Instagram info: update the
app, then force-stop and **clear storage** (Settings → Apps → Meta AI →
Storage → Clear data), reopen and log in fresh; reinstall as a last resort.
This only blocks on-glass loading — hosting and preview work regardless.

## Rules this prototype follows

- Read + propose, never actuate: cards link to approvals and detail on
  the phone; no control actions originate here.
- Glanceable on glass: big type, minimal text, severity at a glance.
- Offline-tolerant: embedded fallback state if the fetch fails.
