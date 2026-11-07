# Checkpoint 4.1 — connected recommendation tuning plan

Date: November 7, 2026 (revision 1). Builds on checkpoint 4 ([CONNECTED_RECOMMENDATIONS_PLAN.md](CONNECTED_RECOMMENDATIONS_PLAN.md)), which is implemented on `android-4-connected-recommendations` and awaits phone acceptance.
Status: **implemented November 7, 2026 on `android-4.1-connected-tuning`; awaiting phone acceptance (§5).** Started at the user's request before checkpoint 4 phone acceptance; checkpoint 4 is committed separately as `eaac988`.

## 1. Goal

Let the phone tune what the desktop recommender returns for it, without changing how the desktop recommends for itself.

Today a connected request carries only the phone's mood, exploration, Vibe lock song, the last 20 outcomes and exclusions. Everything else comes from desktop settings (`llm_reranking_enabled`, `recency_exclude_hours`, the detected archetype), and several desktop inputs are never used for phone requests (energy trajectory, constellations, time-of-day). Checkpoint 4.1 adds phone-only overrides and shows the listener what the server actually did.

Decisions (November 7, 2026):

- **Phone-only overrides.** Tuning lives on the phone and travels with each context. The desktop's own settings and behavior are never changed by it.
- **Knobs:** variety (jitter), similar picks per song, total picks per set, energy direction, LLM ranking and taste pockets (constellations).
- **Pick count.** The set size becomes a phone setting: default 5, maximum 8.
- **Energy Auto** follows the phone's last 5 songs, like the desktop does with its own history.
- **Repeat avoidance** has no setting, but the phone's recent plays now count toward the server's recency exclusion.
- **UI.** A Server section in the existing Tune sheet, plus a short summary line on the options card.

### Out of scope

- Writing any tuning back to desktop `settings` or into the shared profile through sync.
- Folder filters, time-of-day bias and talk/guest slot bias for phone requests.
- Changing offline Radio. Offline refill ignores the new Server settings.
- Room migrations. Tuning is stored in SharedPreferences.

## 2. Settings

| Setting | Values | Default | Server effect |
| --- | --- | --- | --- |
| Variety | 0–1 slider (Steady ↔ Varied) | Unset (no jitter, as today) | Random noise added to the retrieval query vectors, magnitude 0.16 × variety |
| Picks per song | 5–8 | 5 | Total slot-tagged picks in one set |
| Similar picks | 2, 3, 4 | Server decides (normally 2–3 from exploration/archetype) | Number of `exploit` slots; the rest of the set are options |
| Energy | Auto / Rising / Steady / Falling | Auto | Energy direction and target energy passed to ranking |
| LLM ranking | Desktop default / On / Off | Desktop default | Overrides `llm_reranking_enabled` for this phone only |
| Taste pockets | On / Off | Off | Constellation reweighting and hops; active only after 50+ shared signals |

Rules:

- **Options = picks − similar.** With 5 picks and 3 similar the card offers up to 2 options; with 8 picks and 2 similar it offers up to 6.
- **Vibe lock wins.** Under Vibe lock the set stays 4 `exploit` + 1 `explore`, whatever the pick and similar settings say. Settings are kept and apply again when Vibe lock ends.
- **Refill bounds stay.** Refill still takes at most 4 similar picks and never exceeds the 5-entry bound. Unused similar picks are not re-offered as options.
- **Exploration stays** as the existing Tune slider and keeps its offline meaning. "Similar picks" only overrides the slot split it implies on the server.
- **Availability limits.** A partially matched library can return fewer picks than asked; the summary shows what came back.
- **Use desktop defaults** clears every override, so requests behave exactly like checkpoint 4.

## 3. Design

### 3.1 Wire (additive)

- **Capability.** `GET /capabilities` adds `"connected_tuning": true`. The phone sends `tuning` only when it is advertised; older servers keep working without it, because contexts reject unknown fields.
- **Context body** gains an optional object. Absent fields mean "use the desktop default":

```json
"tuning": {
  "variety": 0.4,
  "picks": 6,
  "similar": 3,
  "energy": "auto",
  "reranking": "default",
  "pockets": false
}
```

  Validation: `variety` 0–1; `picks` integer 5–8; `similar` integer 2–4 and ≤ `picks`; `energy` one of `auto|rising|steady|falling`; `reranking` one of `default|on|off`; `pockets` boolean. Anything else is 400.
- **Phone plays.** The context gains an optional `played` list of `{"song_id", "age_s"}` (≤ 100 distinct songs, newest first, the last 3 days of phone completions/skips) so the server can apply its recency window to plays it has not synced yet. `recent` (≤ 20 outcomes) is unchanged.
- **Fencing.** Tuning is part of the context, so changing it re-posts the context and bumps the epoch. Held sets become stale and are dropped, exactly like a mood change. The phone's `ConnectedFence` carries the tuning value.
- **Reply** adds `applied`, the tuning the server really used after Vibe lock and availability:

```json
"applied": {"variety": 0.4, "picks": 6, "similar": 3, "energy": "rising", "reranking": "llm", "pockets": "inactive"}
```

  `pockets` is `on`, `off` or `inactive` (requested but under 50 signals). `energy` reports the resolved direction when Auto was asked.

### 3.2 Backend

- **Variety as an input.** Retrieval is deterministic today: the `jitter_strength` setting and `_jitter_vector` helper exist but are never applied. Add `jitter_strength` (and an injectable RNG for tests) to `build_candidate_list` and `RecommendationInputs`. Only phone requests pass it; the desktop passes nothing, so its behavior is unchanged.
- **Slot counts.** `portable/slots.allocate_ranked_slots` and the reranker hard-code 5 picks (`exploit = 5 - …`, "exactly 5 entries", dedupe cut at 5). Add a `total` (default 5) and an optional `exploit` count. Desktop always passes the defaults. The reranker prompt and its slot repair use the requested counts; reasons are asked for every pick in phone requests, since options show their reason. Retrieval over-fetch scales with `total`.
- **Energy.** Auto derives direction from the phone's newest 5 recent songs using `engine/energy.py`'s rising/falling/stable rule (shared as `direction_of`). The direction reaches the LLM prompt (as on the desktop) and, for phone requests with an energy setting, nudges candidate scores by up to ±0.1 toward higher or lower energy than the current song, so deterministic and fallback ranking follow it too. Steady applies no nudge.
- **LLM ranking.** `default` reads `llm_reranking_enabled`; `on`/`off` override it for this request only. The 60 s deadline and deterministic fallback stay.
- **Taste pockets.** Build a constellation view from the shared long-term profile, cached per process (the desktop already caches for 5 minutes). Below `CONSTELLATION_MIN_SIGNALS` report `inactive`. Hops use the phone's exploration.
- **Phone recency.** `played` entries younger than `recency_exclude_hours` are excluded; older ones get the same graduated penalty (−0.15 under 6 h, −0.08 under 24 h, −0.03 under 3 days) desktop `play_events` get. This covers phone plays not yet synced.
- **Isolation** is unchanged: no writes to `settings`, `play_events`, `queue_history`, the desktop queue or `/ws/events`.

### 3.3 Android

- **Storage.** A `ConnectedTuning` value in the Radio SharedPreferences, one per phone (not per server). No Room migration.
- **Request.** `ConnectedRadioSource.contextBody` adds `tuning` (only overridden fields) and `played` (journal completions/skips of the last 3 days, mapped to server IDs, distinct, ≤ 100) only when the capability is advertised.
- **Fence.** `ConnectedFence` carries the tuning value; changing tuning drops the held set (card hides) and the next song's request carries the new values. Current song, position and queue are untouched.
- **Policy.** Unchanged: refill still takes at most 4 similar picks within the 5-entry bound; the server already returns the requested `similar` count. `ConnectedPolicy.summary` builds the card line.
- **Tune sheet.** Below Mood and Exploration, a **Server** section visible only when Connected Radio is on (the sheet now scrolls):
  - Variety slider labelled Steady ↔ Varied (0 = unset).
  - Picks per song: chips 5 / 6 / 7 / 8 (5 = unset).
  - Similar picks: chips Server decides / 2 / 3 / 4, with a line explaining how many become options.
  - Energy: chips Off / Follow recent / Rising / Steady / Falling. Off (unset) keeps checkpoint 4 behavior; Follow recent is `auto`.
  - **Advanced** (collapsed): LLM ranking (Desktop default / On / Off) and Taste pockets switch, with "Needs more listening history on the desktop" when the last reply said `inactive`.
  - **Use desktop defaults** button, disabled when nothing is overridden.
  - A note: "Applies from the next song. Vibe lock keeps 4 similar + 1 option." When the server lacks `connected_tuning`: "This desktop doesn't support tuning yet" and the controls are disabled.
- **Options card summary.** One line under the card from `applied` and `ranked_by`, for example `LLM · 3 similar · 3 options · variety 40% · rising`. Fallback shows `Fallback ranking`; pockets show `pockets on` or `pockets need more listening`. Each option keeps its server reason (phone requests now ask the LLM for a reason on every pick).
- **Settings → Server** keeps the last-result line, adds "Current set: …" with the same summary, and points to Radio → Tune.

## 4. Work order (Red/Green TDD)

| Step | Work | Proof |
| --- | --- | --- |
| 1 | Jitter parameter in retriever/`RecommendationInputs` | Desktop queue/retriever tests unchanged; injected jitter changes candidate order deterministically with a seeded RNG |
| 2 | `total`/`exploit` in slots, reranker prompt and repair | Defaults reproduce today's 5-pick output; 8 picks with 2 exploit gives 2/6; Vibe lock stays 4/1; fallback paths honour counts |
| 3 | Context `tuning` + `played` validation, capability flag, epoch bump on change | 422 for every invalid field; absent tuning equals checkpoint 4 output; unknown fields still rejected |
| 4 | Energy Auto/explicit, LLM override, pockets, phone recency penalty, `applied` echo | Rising/falling recents resolve correctly; `off` never calls the LLM; under 50 signals → `inactive`; old phone plays are penalised; desktop `settings`, `play_events`, queue unchanged |
| 5 | Android `ConnectedTuning`, request body, `played`, fence, `applied` parsing | JVM fake-server tests: tuning and plays sent only with capability; tuning change patches the context and drops the held set |
| 6 | Tune sheet Server section, card summary, Settings text | Compose tests: section hidden when Connected off; disabled without capability; defaults reset; summary matches `applied` |
| 7 | Full verification + APK + phone acceptance | §5 |

## 5. Verification and acceptance

Verification on November 7, 2026 (branch `android-4.1-connected-tuning`):

- Backend guarded suite: 926 passed, 9 skipped (includes `backend/tests/test_connected_tuning.py`).
- Android `testDebugUnitTest`: 128 passed. `lintDebug`: no errors; warnings only on pre-existing lines.
- `connectedDebugAndroidTest` on the API 37 emulator: 178 of 180 passed in the full run. One failure was a finder in a new tuning UI test, now fixed. The other was `CatalogImportTest.interruptedBatchesResumeVerifiedBlobsAfterRepositoryRestart`, in code this checkpoint does not touch. On rerun the affected classes passed 59/59, so that test appears flaky.
- No desktop frontend changes, so `npm test` was not rerun.
- Not yet done: phone acceptance, and a measurement of LLM time for 8-pick sets against the 60 s deadline on the real desktop.


Automated: guarded backend suite, `npm test`/`npm run build` (no desktop change expected), Android `testDebugUnitTest`, `connectedDebugAndroidTest`, `lintDebug`. Fixtures in `.test-runtime/android-checkpoint4_1/` with synthetic catalogs and a fake LLM/Chroma.

Phone acceptance:

1. With every setting at desktop defaults, results match checkpoint 4 behavior.
2. Set picks to 8 and similar to 2. The card offers up to 6 options; refills still add at most 2 similar picks.
3. Raise variety to the top. Over several songs, picks feel noticeably more varied than at the bottom.
4. Set Energy to Rising, then Falling. The summary shows the direction and picks follow it.
5. Turn LLM ranking Off. The summary shows fallback ranking and replies arrive faster.
6. Turn on Taste pockets. The summary shows on or inactive, never an error.
7. Turn on Vibe lock. The set returns to 4 similar + 1 option; turning it off restores the chosen mix.
8. Change a setting mid-song. The current card disappears and a new set arrives for the next song; the queue and current song are untouched.
9. On the desktop, Radio settings, queue and recommendations are unchanged throughout.
10. Songs played on the phone in the last hour are not recommended back, even before manual sync.

## 6. Risks

- **Bigger sets are slower.** 8 picks lengthen the LLM prompt and reply. Current replies take about 15 s, so 8 picks are expected to stay well inside the 60 s deadline; the deterministic fallback still covers a miss.
- **Slot code is shared with the desktop.** The total/exploit parameters must default to today's values; desktop tests guard this.
- **Pockets on the server under load.** Constellation builds are cached; a cold build must not push a phone request past its deadline (skip pockets and report `inactive` if it would).
- **Variety mapping.** The 0.16 maximum noise magnitude is a starting point and will be tuned on the phone.

## 7. Open questions

- **Per-server tuning.** Settings are per phone for now. Revisit if more than one desktop is paired.
- **Tuning summary on the lock screen.** Not planned; the card summary is in-app only.

Rollback: Use desktop defaults on the phone, or turn Connected Radio off. The server ignores the absence of `tuning`, so reverting either side needs no data change.
