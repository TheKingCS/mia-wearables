# mia-wearables — MIA on Meta glasses

The glasses interface track for **MIA (Multifunctional Intelligent
Assistant)**. One model, many interfaces: the glasses are a new
presentation surface on the existing MIA core — not new logic.

## Strategy

- **Glanceable on glass, detail on phone.** The monocular Display gets
  Needs Attention cards, Next Up, quest check-ins, and voice.
  Dashboards and the 3D twin stay on phone/desktop.
- **Read + propose, never actuate.** Glasses surface approvals through
  the existing queues (homestead control loop, MIA communication gate).
  No raw actuator control from glasses — ever.
- **No new backends.** Cards are fed by state MIA already exports
  (homestead `dashboard_state.json`, MIA server API on port 8765).

## Phases

- **Phase 0 — Foundations** (in progress): Developer Center
  registration, Display developer preview, Mock Device Kit.
- **Phase 1 — "MIA Glance" web app:** HTML/CSS/JS cards via the
  Display web-apps path. Launch set: Greenhouse (Needs Attention),
  Maintenance/Garage (overdue), Missions (today's quests).
- **Phase 2 — Voice:** "Hey Meta, open MIA" — Q&A + quest check-in
  over the existing tool surfaces (~175 MIA + ~99 homestead), with the
  existing grounding rules (never invent numbers, never soften critical
  alerts).
- **Phase 3 — Native companion:** Kotlin/Swift via the Wearables
  Device Access Toolkit; Neural Band gestures; fold into the existing
  Android companion app. Bundle all assets — no runtime CDN.
- **Phase 4 — Twin for AR:** real-geometry digital twin for
  Orion-class spatial HUD (equipment inspection overlays), building on
  `config/layout.json`.
- **Phase 5 — Revenue hooks:** paid cloud (sync, glasses companion,
  voice AI). The local core stays free forever — charge for ongoing
  value, never for access to your own data.

## Sibling repos

- [MIA](https://github.com/TheKingCS/MIA) — the core: 33 modules,
  local-first assistant (Ollama/llama3.2), phone server, Android app,
  headless Pi runtime
- [mia-homestead](https://github.com/TheKingCS/mia-homestead) —
  greenhouse automation: local control loop, Pi + Safety MCU two-tier
  safety, 99-tool assistant, digital twin

## Docs

Full build plan: `mia-meta-glasses-build-plan.md` (MIA goal workspace).
