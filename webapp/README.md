# MIA Glance — web app (Phase 1)

Glanceable MIA cards for Meta Ray-Ban Display, shaped to Meta's Web Apps
platform: a fixed **600×600** additive-light display, D-pad navigation
(touchpad/Neural Band arrive as arrow keys + Enter), no mouse/touch, and
design types at 16px body / 20px+ titles with bright, high-contrast color
on black.

Five sections, **one per screen** — Left/Right flips section, Up/Down
moves between cards, Enter expands a card in place, Escape (or the
glasses' Back, which rides browser history) collapses it:

1. **Needs attention** — unresolved homestead alerts
2. **Proposals** — the real homestead approval queue (see below)
3. **Tracked** — cell sensor snapshots that are quietly in band
4. **Next up** — open maintenance tasks
5. **Today's quests** — today's active MIA missions

An always-visible summary line grounds the glance: *"2 things need you ·
2 proposals waiting · 2 quests open · all else quiet."*

## Platform compliance baked in

- `<meta name="viewport" content="width=600, height=600, initial-scale=1.0, user-scalable=no">`
  and `<meta name="mrbd-web-app-capable" content="yes">`
- Every interactive element is a real button with a visible cyan focus ring
- Detail expansion uses `history.pushState` so native Back collapses it
- State re-fetches `state.json` every 60s (**R** refreshes manually)
- Decisions and quest check-ins persist in `localStorage` (guarded)
- Manifest at `.well-known/meta-wearables-manifest.json` (name,
  description, monochrome artwork, theme + gradient)
- `tools/make_icon.py` renders `icon.png` (256×256 PNG ≥ the 52px toolkit
  minimum; SVGs aren't supported for favicons). Run it once, then flip
  the manifest's `appearance.icon.src` to `../icon.png`.

## Live data: the exporter

`tools/export_glance_state.py` builds `webapp/state.json` from real
sources — no mock data, no guessing:

```bash
python tools/export_glance_state.py \
  --dashboard /path/to/mia-homestead/viewer/dashboard_state.json \
  --db /path/to/mia-homestead/mia_homestead.db \
  --missions /path/to/MIA/data/missions.json \
  --mia-data /path/to/MIA/data \
  --out webapp/state.json
```

- **attention** ← unresolved homestead alerts
- **proposals** ← Gate A: `experiments` with status `proposed`
  (dashboard JSON); Gate B: `parameter_change_proposals` with status
  `proposed` (direct DB read via `--db`)
- **tracked** ← per-cell latest sensor readings from the dashboard JSON
- **next_up** ← open maintenance tasks (overdue computed from `due_date`)
- **life modules** ← MIA's `data/` dir (`--mia-data`): over-budget and
  overdue-bill attention, budget pace, property equity/NOI, expiring
  pantry, groceries, last workout, overdue maintenance. Card map in
  `docs/life-modules-card-map.md`.
- **quests** ← today's active MIA missions (`--missions`; falls back to a
  `--quests` JSON file)

Every source is optional — missing data renders as an honest empty state,
never fabricated cards.

## Proposals (read / propose / approve)

Proposal cards carry the homestead's real queue: hypothesis, evidence,
yield rationale, and a safety note — *approval changes the target the
control loop aims for; every dose still passes the Safety MCU's hard
limits.* Approving writes to `localStorage` only until the homestead
write-back endpoint exists; nothing actuates from the glasses.

## Hosting for the glasses

The glasses need **HTTPS** (plain http only works for loopback). Quickest
path on Windows: serve `webapp/` with `python -m http.server 8080`, then
`.\cloudflared-windows-amd64.exe tunnel --url http://localhost:8080` and
use the printed `https://….trycloudflare.com` URL. Quick-tunnel URLs
change on every restart; fine for testing.

To load: Meta AI app → **App Settings** → **Apps** → **Web Apps** →
**Connect Web App** → enter the HTTPS URL → **Save**. The app appears at
the bottom of the app grid on the glasses.

For desktop iteration, Meta's **Ray-Ban Display Simulator** Chrome
extension gives a 600×600 frame with additive blending, bright/dark
scene previews, directional controls, and a quality checklist (viewport
metadata, focus targets, horizontal overflow, visible focus styles).

Validate on the real glasses: every critical control reachable in a
sensible spatial order; focus visible near display edges; activation
fires once; content readable over bright and dark surroundings;
overflow intentional; Back returns to the native app boundary.

## Rules this app follows

- Read + propose, never actuate: no control actions originate here.
- Glanceable on glass: big type, minimal text, severity at a glance.
- Full detail lives on phone/desktop; the glasses stay glanceable.
- Offline-tolerant: embedded fallback state if the fetch fails.
