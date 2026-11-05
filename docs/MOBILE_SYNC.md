# Manual profile sync — checkpoint 2C

Manual sync exchanges listening history and shared feedback with the paired
server over the existing USB development connection. Android continues to play
its own local files. Sync never transfers a playing session between devices.

## Use it

1. Update the desktop checkout and Android APK without clearing app data. Keep
   the phone's existing pairing. Restore USB forwarding with
   `adb -d reverse tcp:18766 tcp:18766` using your installed SDK's `adb`.
2. Open desktop **Settings → Server / Paired devices**. Enable profile sync for
   the existing phone with **Allow profile sync**. This grants profile read/write
   access to that device; it keeps the same device ID and credential. New pairing
   tickets still grant catalog access only.
3. In Android **Settings → Server → Sync**, choose **Sync now**. The app verifies
   the pinned server identity and permissions before exchanging private data.
   Review the pending count, last successful sync and any recovery message.
4. Repeat **Sync now** when you want to exchange newer activity. No automatic
   scheduler, background polling, remote tunnel or Firebase setup is involved.

The USB gateway is off after a desktop restart. The local desktop profile-sync
action enables it. For an already-authorized phone, choose **Enable USB connection**
in desktop Settings without changing permissions or preparing the catalog. Restore forwarding after reconnecting USB when necessary.
See [MOBILE_PAIRING.md](MOBILE_PAIRING.md) for pairing and catalog preparation.

## What is shared

Actual playback starts, completions, skips, Replay and Like/Dislike changes are
immutable events. Supported server profile changes also include blacklist,
energy and the exploration/reranking defaults. Downloaded history and matched
feedback can improve future local Radio suggestions. They do not insert songs
into the current queue or rewrite source audio or tags.

Shared exploration is a default for a newly initialized Radio context. An
existing Radio context keeps its current tuning when downloading a default.
An explicit change to Tune Radio's exploration while paired also queues that
value as the future shared default; unpaired tuning stays local. Android does not run the
desktop LLM reranker; its shared setting has no local inference effect. Active
mood, Vibe lock, queue, song position, shuffle, sleep timer and DJ state remain
local to each device.

Events keep the identity and mapping captured when they were created. Matching
a recording later does not remap old history. Phone-only events remain scoped
to their originating device. Events created before pairing or under an older
pairing are preserved locally rather than uploaded under a different identity.

## Retry and recovery

- An unavailable server or interrupted request keeps committed progress and
  pending events. Restore the connection and choose **Sync now** again.
- A lost upload acknowledgement is safe to retry: the same event IDs and
  payloads are resent and acknowledged as duplicates. Upload acknowledgements
  never skip older events in the download feed.
- Missing profile permission asks you to allow sync for that existing device
  on the desktop. It does not require erasing the pairing.
- An expired/revoked credential stops authenticated retries. Repair pairing
  explicitly; old events retain their original ownership.
- Restored data, missing installation markers or inaccessible Keystore keys
  quarantine upload. Local files, cached history and playback remain available.
- An event-identity conflict is reported and preserved for diagnosis; retry
  never invents replacement event IDs to bypass the conflict.

Already-exported legacy desktop Skip undo has no synchronized retraction
contract. Automatic sync and remote hosting are separate later work.

## Phone acceptance

1. Update without clearing data. Check playlists, matched songs and the paused
   playback bookmark survived. Enable sync for the existing device.
2. Play locally, Like/Dislike a matched song and choose Replay. Sync and inspect
   the desktop feedback/history. Sync again and check counts do not duplicate.
3. Change feedback on the desktop, then sync on the phone. Check the matching
   local song and future Radio suggestions reflect the shared feedback.
4. While music plays, sync and confirm current song, position, queue order,
   mood, Vibe lock and a running sleep timer remain intact.
5. Unplug USB during sync. Continue local listening, reconnect, restore
   forwarding and retry. Check pending events drain and history stays unique.
6. Close/reopen the app and retry. Check last success and pending activity
   persist. Try offline Radio with the desktop closed.

Automated fixtures and emulator checks do not establish phone USB ergonomics,
audible quality or real-library listening quality. Review this checkpoint before
starting remote hosting or connected recommendations.
