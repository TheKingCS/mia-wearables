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
python3 -m http.server 8080
# open http://localhost:8080/ in Chrome
```

- Open DevTools responsive mode at **600×600** to approximate the Display.
- Use **↑/↓** to move between cards, **Enter** to select / check in a quest.
- Quest check-ins persist in `localStorage`.

## Files

- `index.html` — the whole app (no build step, no dependencies)
- `state.json` — mock card data; the app falls back to embedded data if the fetch fails
- `.well-known/meta-wearables-manifest.json` — Version 1 manifest (name, description, monochrome icon, theme + gradient)
- `icon.svg` — transparent monochrome artwork (64×64 design area)

## Wiring to live data (next)

`state.json` is shaped to be replaced by real exports:

- `attention` ← homestead `viewer/dashboard_state.json` alerts (severity → card strip)
- `next_up` ← MIA maintenance / garage modules
- `quests` ← MIA missions module

Serve the app over **HTTPS** for the glasses (plain `http://` is only
accepted for loopback hosts like `localhost`). The manifest must be
served as `application/json; charset=utf-8`.

## Rules this prototype follows

- Read + propose, never actuate: cards link to approvals and detail on
  the phone; no control actions originate here.
- Glanceable on glass: big type, minimal text, severity at a glance.
- Offline-tolerant: embedded fallback state if the fetch fails.
