# MIA Glance — Phase 3: Native Companion Scope

**Status:** Draft scope. Phase 1 (web app on Ray-Ban Display) and the Phase 2
voice spec ship first; this is the plan for the native companion that follows.
**Constraint that governs everything below:** one model, many interfaces. The
native app is a new surface on MIA Core — not a new brain. Glasses remain
**read / propose / approve**: they never touch an actuator, never bypass the
homestead approval queue or the Safety MCU.

## 1. Scope: in / out

**In scope**
- Android companion via Meta's **Device Access Toolkit (DAT)**, folding the
  Glance card model into the existing MIA companion app (`android/` in
  TheKingCS/MIA — Kotlin, plain-Android UI built in code, no third-party
  libs, sideloaded APK via GitHub Releases).
- The five card sections, driven by the same state contract as the web app:
  **Needs Attention / Proposals / Tracked / Next Up / Quests**.
- Native rendering to the Ray-Ban Display via DAT's display capability
  (`mwdat-display`), replacing the WebView surface.
- Neural Band gesture navigation (below).
- Background refresh of card state; communication-gated notifications.
- Offline packaging: every asset bundled in the APK. No runtime CDN.

**Out of scope**
- iOS companion (Swift/DAT-iOS) — later parity phase, after Android proves
  the model.
- Any new backend, database, or state format.
- The digital twin / spatial rendering (Phase 4, Orion trajectory).
- Revenue surfaces (Phase 5).

## 2. Architecture

```
Homestead DB ──> viewer/export_dashboard_state.py ──> dashboard_state.json
MIA missions.json ──────────────────────────────────┐
                                                    v
                          tools/export_glance_state.py ──> state.json
                                                    │
                          ┌─────────────────────────┴─────────────────┐
                          v (Phase 1, today)                          v (Phase 3, this doc)
                   Web app (glance pages)              Native companion (DAT display)
```

- **Same contract, two renderers.** `state.json` is the interface. The web
  app and the native app both consume it; neither fork knows about the other.
- **Where state comes from on-device.** The companion already talks to the
  MIA phone server (port 8765, bearer auth per profile, over Tailscale).
  Phase 3 adds one read endpoint serving the exported glance state (or the
  companion fetches the hosted `state.json` — same file, same schema).
- **Approvals stay on-device until the write-back exists.** The web app
  persists decisions in `localStorage`; the native app mirrors that exactly
  (SharedPreferences/DataStore). When the homestead write-back endpoint
  lands (Phase 1 follow-up), both surfaces switch to it — the card payloads
  already carry `{kind, row_id}` for that call.
- **DAT integration shape** (Android, from official docs):
  - Gradle artifacts from Maven Central, group `com.meta.wearable`:
    `mwdat-core`, `mwdat-display`; `mwdat-mockdevice` for dev without
    hardware. No GitHub Packages token needed (DAT moved to Maven Central,
    Sep 2026). Snapshot version at drafting: **0.9.0** — re-verify on
    Maven Central before pinning.
  - App manifest carries `com.meta.wearable.mwdat.APPLICATION_ID` and
    `CLIENT_TOKEN` (real values from Wearables Developer Center; `0` /
    omitted only works under Developer Mode).
  - Session lifecycle must handle: SDK init at startup, device observation
    & selection, session start/stop, permission denied / device
    unavailable / disconnected states — not just the happy path.
  - Display model: `display.send(rootView)` replaces the whole screen each
    call. Our paged sections map naturally: one root view per section,
    re-rendered on navigation or state change.

## 3. What native buys us

- **Neural Band gestures as first-class input.** The web app gets arrows +
  Enter; native gets the real event stream — swipe to flip sections,
  tap/pinch to select and expand in place, fist-pinch (or the DAT
  back-gesture equivalent) to collapse/return. Gesture map mirrors the
  D-pad model so the information architecture is unchanged:
  swipe ↔ → page flip, focus move ↔ section scroll, select ↔ Enter,
  back-gesture ↔ Escape/history-back.
- **Richer widgets.** Native display views (FlexBox/Text/Button/Image/Icon
  per the DAT display API) give us real layout: severity strips, progress
  bars (XP), two-line evidence rows — at display-native fidelity instead
  of WebView approximations.
- **Background refresh.** The companion can poll/export state on a
  schedule and keep the glance current without the app being foregrounded
  — the web app's 60-second foreground refresh becomes a background sync.
- **Notifications, inside the communication gate.** MIA speaks only when
  the user would want it: a critical attention item (e.g. pH critical)
  may surface; routine tracked/quest state never does. "Less friction,
  not more notifications" is a hard rule — default is silence, and every
  notification class is individually opt-in.
- **Survival of the existing app.** The companion already does hands-free
  voice to MIA at home over the phone server. Phase 3 adds the visual
  surface to the same app — voice asks (Phase 2 spec) can then render
  their answer cards natively.

## 4. Milestones

- **M1 — Hello DAT.** `mwdat-core` + `mwdat-display` integrated; official
  sample session runs against real glasses (or `mwdat-mockdevice` if the
  Display is unavailable); a static "MIA" root view renders on-glass.
  Confirms credentials, manifest metadata, and session lifecycle end to end.
- **M2 — Card model port.** The five sections render natively from real
  `state.json`; proposals expand in place with evidence + safety note;
  approve/dismiss persists on-device; quest check-ins toggle. Feature
  parity with the web app, pixel-honest at 600×600.
- **M3 — Gesture navigation.** Swipe/tap/back-gesture mapped per §3;
  focus order validated on-device (sensible spatial order, focus visible
  near edges, activation fires exactly once).
- **M4 — Offline packaging + background sync.** All assets bundled (no
  runtime CDN — the homestead twin's CDN dependencies are the cautionary
  tale); background state refresh; communication-gated notifications for
  critical attention only; airplane-mode test passes with honest
  stale-data states.

## 5. Risks & open questions

- **DAT maturity.** Developer Preview; API has already churned between
  releases (permission/session renames 0.8→0.9). Pin a version, re-verify
  against the version-dependencies matrix (DAT 0.9.0 ↔ Meta AI app V282,
  Display firmware V125) before debugging app code.
- **Registration.** Production (non-Developer-Mode) builds need real
  `MetaAppID`/`ClientToken` from Zac's Wearables Developer Center org.
  App attestation is skipped in Developer Mode; the release channel path
  is unverified for us. *(Needs Zac's sign-in; never paste tokens in chat.)*
- **Mock-device fidelity.** `mwdat-mockdevice` helps layout work but
  cannot validate gesture feel, brightness over real surroundings, or
  session drop behavior — hardware playtests remain mandatory.
- **iOS parity.** Deferred by decision, not by accident; DAT-iOS (Swift
  PM) exists and the state contract is platform-neutral, so the port is a
  renderer job when it comes.
- **Open:** does the companion fetch glance state from the phone server
  (8765) or from hosted `state.json`? Server is the better long-term home
  (auth, freshness); hosted file is simpler for M2. Decide at M2 kickoff.

## 6. Explicit non-goals

- **No actuator control.** No dose, pump, relay, or parameter write
  originates from the glasses or the companion's glance surface. Approval
  changes a *target*; the Safety MCU's hard limits still gate every dose.
- **No new backend.** If a datum isn't in the exporter or the phone
  server today, it doesn't appear in Phase 3.
- **No dashboard on the glasses.** Full detail stays on phone/desktop;
  the glance stays glanceable.
- **No store release in this phase.** Sideloaded APK (current companion
  distribution model) until the release-channel path is verified.
