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

## Android status and planning

| Document | Owns |
| --- | --- |
| [ANDROID_APP_PLAN.md](ANDROID_APP_PLAN.md) | Checkpoint delivery, verification, acceptance and proposed app follow-ups |
| [MOBILE_CONNECTION_PLAN.md](MOBILE_CONNECTION_PLAN.md) | Future connection/sync architecture and explicitly dated Phase 0 audit |

Checkpoints 1A–1C and 2A are accepted. Offline Radio 2B is implemented with phone review pending. Manual sync 2C is future work. Plans are not authorization to begin another checkpoint.

## Technical contracts

| Document | Owns |
| --- | --- |
| [PORTABLE_CATALOG.md](PORTABLE_CATALOG.md) | Immutable catalog revisions, validation and vector provenance |
| [MUSIC_MATCHING.md](MUSIC_MATCHING.md) | Local recording identity, decisions and uncertainty |
| [OFFLINE_RECOMMENDATIONS.md](OFFLINE_RECOMMENDATIONS.md) | Reference scoring and Android offline Radio integration |
| [PORTABLE_SYNC_API.md](PORTABLE_SYNC_API.md) | Server event/profile API; Android manual sync remains future work |
| [PORTABLE_MEDIA_PLAN.md](PORTABLE_MEDIA_PLAN.md) | Optional connected speech contract; native speech generation remains unavailable |

## Maintenance rules

Keep current behavior in guides/contracts, checkpoint evidence in the Android log, and future design in the connection plan. Date historical evidence and link to it rather than duplicating it. Test counts belong to their recorded revision, not the current repository inventory.

`understand/`, `understand_new/`, JSON manifests and lyrics work files are operational/source data, not documentation to archive or delete. `lyrics_progress.md` remains an unverified historical work note. The local Codex handoff remains ignored by Git. Agent workflow instructions are in the root `AGENTS.md`.
