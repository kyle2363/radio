# Documentation index

Start with [the repository README](../README.md) for setup and the application map. This index separates current guides, implementation evidence and future contracts. Historical records are in [archive/](archive/README.md).

## Current operation

| Document | Owns |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Current application boundaries, desktop session behavior and defaults |
| [SERVER_ACCESS.md](SERVER_ACCESS.md) | Binding, authentication and deployment boundaries |
| [TESTING.md](TESTING.md) | Safe verification, outstanding desktop acceptance and deferred TTS investigation |
| [APP_USAGE_CHECKLIST.md](APP_USAGE_CHECKLIST.md) | Detailed manual desktop walkthrough |
| [MOBILE_PAIRING.md](MOBILE_PAIRING.md) | Phone pairing, catalog import, matching and feedback workflow |
| [MOBILE_SYNC.md](MOBILE_SYNC.md) | Explicit profile sync, permissions, retry/recovery and phone acceptance |
| [REMOTE_HOSTING.md](REMOTE_HOSTING.md) | Explicit remote hosting, signed address updates and cellular acceptance |
| [AUTOMATIC_DISCOVERY.md](AUTOMATIC_DISCOVERY.md) | Firebase setup, protected discovery accounts, URL rotation and recovery |

## Android status and planning

| Document | Owns |
| --- | --- |
| [ANDROID_APP_PLAN.md](ANDROID_APP_PLAN.md) | Checkpoint delivery, verification, acceptance and proposed app follow-ups |
| [MOBILE_CONNECTION_PLAN.md](MOBILE_CONNECTION_PLAN.md) | Future connection/sync architecture and explicitly dated Phase 0 audit |
| [CONNECTED_RECOMMENDATIONS_PLAN.md](CONNECTED_RECOMMENDATIONS_PLAN.md) | Checkpoint 4 connected recommendations: design, wire contract, acceptance |
| [CONNECTED_TUNING_PLAN.md](CONNECTED_TUNING_PLAN.md) | Checkpoint 4.1 phone-only tuning of connected recommendations (in progress) |

Checkpoints through 3B automatic discovery are phone-accepted. 2C, 3A and 3B are merged into `main`. Plans are not authorization to begin another checkpoint.

## Technical contracts

| Document | Owns |
| --- | --- |
| [PORTABLE_CATALOG.md](PORTABLE_CATALOG.md) | Immutable catalog revisions, validation and vector provenance |
| [MUSIC_MATCHING.md](MUSIC_MATCHING.md) | Local recording identity, decisions and uncertainty |
| [OFFLINE_RECOMMENDATIONS.md](OFFLINE_RECOMMENDATIONS.md) | Reference scoring and Android offline Radio integration |
| [PORTABLE_SYNC_API.md](PORTABLE_SYNC_API.md) | Event/profile API, Android manual sync and immutable retry/conflict rules |
| [PORTABLE_MEDIA_PLAN.md](PORTABLE_MEDIA_PLAN.md) | Optional connected speech contract; native speech generation remains unavailable |

## Maintenance rules

Keep current behavior in guides/contracts, checkpoint evidence in the Android log, and future design in the connection plan. Date historical evidence and link to it rather than duplicating it. Test counts belong to their recorded revision, not the current repository inventory.

`understand/`, `understand_new/`, JSON manifests and lyrics work files are operational/source data, not documentation to archive or delete. `lyrics_progress.md` remains an unverified historical work note. The local handoff notes (`archive/LOCAL_HANDOFF.md`) remain ignored by Git. Agent workflow instructions are in the root `AGENTS.md`.
