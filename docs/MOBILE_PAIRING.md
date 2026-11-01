# USB pairing, catalog import and matching — checkpoints 1A–1C

These checkpoints add a verified USB connection and explicit catalog/vector import between the Android debug app and the owned Electron development backend. Checkpoint 1C adds explicit cached content hashes and local matching/review. These checkpoints do not synchronize history, request recommendations, deliver speech, or enable LAN/public hosting. Local phone music remains independent. The workflow is intended for one personal server and a couple of listeners.

The architecture and later checkpoints are in [MOBILE_CONNECTION_PLAN.md](MOBILE_CONNECTION_PLAN.md). This guide describes the implementation; the checkpoint log and final verification are in [ANDROID_APP_PLAN.md](ANDROID_APP_PLAN.md).

## First setup

1. Install the added backend dependency in the existing project venv if this checkout has not been prepared:

   ```powershell
   .\backend\venv\Scripts\python -m pip install cryptography==50.0.2
   ```

   The Windows binary wheel provides the P-256 signing implementation. Normal playback does not initialize mobile identity or start its listener. Do not replace the project's Python environment or install the complete model stack just to pair.

2. Update the phone with `android/app/build/outputs/apk/debug/app-debug.apk`, preserving its data. Enable USB debugging, connect by USB, and authorize this computer on the phone. Checkpoints 1B–1C add explicit Room v5→v6→v7 migrations for imported metadata, hashes and review decisions; local music, playlists, history, loudness results and the playback bookmark are preserved. Existing pairing remains valid.

3. With one physical phone attached, run from the repository root:

   ```powershell
   $radioAdb = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
   & $radioAdb devices -l
   & $radioAdb -d install -r .\android\app\build\outputs\apk\debug\app-debug.apk
   & $radioAdb -d reverse tcp:18766 tcp:18766
   ```

   Use your actual SDK path if different. `-d` selects the physical USB device rather than an emulator; with multiple phones, select the intended device by its real serial. No router/firewall change, LAN listener or Firebase account is involved.

4. Start Radio's Electron development build with `npm run dev` from `frontend/`, and sign in if the desktop is configured to require authentication. Restart an already-running Electron/backend after updating so the new private parent handshake is present. The backend must be the child owned by this Electron instance; a separately started backend has no pairing-control capability.

5. In desktop Settings → **Server / Paired devices**, enter a device name and choose **Create pairing bundle**. This explicitly starts the USB-only listener at `http://127.0.0.1:18766`. Copy the bundle while it is still valid (five minutes).

6. In Android Settings → **Server**, paste that bundle and choose **Pair**. Android verifies the pinned signed server identity before sending the ticket or its proposed device credential. Success shows the paired server and a verified connection status. **Check connection** repeats identity and authenticated capability checks.

Bundle copying is manual in this checkpoint. It does not install a camera/QR scanner or automatically bridge the desktop and phone clipboards. Use a trusted transfer method you control; the bundle is a short-lived authorization secret. It never contains a desktop password or long-lived device bearer. A new bundle replaces outstanding unused tickets. The displayed copy disappears on expiry, replacement, revocation or closing the settings section.

ADB forwarding may need to be repeated after USB reconnect or device restart. After quitting/relaunching Radio, the gateway is initially off: **Prepare catalog** or **Create pairing bundle** explicitly enables it. An already-paired phone does not need a new ticket. A separate remote-hosting UI is deferred to its own checkpoint.

## Import a catalog

1. Restart the updated Electron development app/backend. Keep the existing phone pairing, and restore `adb -d reverse tcp:18766 tcp:18766` if needed.
2. Let library ingestion finish. Avoid running independent maintenance scripts or changing catalog metadata/vectors while preparing the snapshot.
3. In desktop Settings → Server / Paired devices → **Phone catalog**, choose **Prepare catalog**. This explicitly enables the USB gateway and prepares existing metadata/vectors. It does not run ingestion, generate new embeddings or copy MP3s. Wait for the ready state and revision/counts.
4. In Android Settings → Server → Catalog, choose **Import catalog**. The app checks the trusted server, downloads and verifies metadata and vector bytes, then atomically adopts the revision. Revision, song count and vector count remain visible offline.
5. Keep using local playback during import. **Cancel import** before adoption retains the prior active catalog; complete verified vector blobs can be reused by a retry. If adoption already committed, the app reports completion. Failed, corrupt, incomplete, wrong-source or older revisions do not replace the working catalog. A later explicit import updates the snapshot; it is not an automatic sync service.

Imported metadata remains separate from the phone's scanned songs. Filename labels, queue order and local playback do not change. Content hashing, matching imported song IDs to local files and recommendations arrive in later checkpoints. Missing server tracks never delete local audio.

Downloads have request and total-import limits. Large imports can briefly show “Waiting for the server” while honoring its rate limit. Retry uses verified cached blobs; unrelated inactive cache data is reclaimed while active catalogs are retained. If a catalog is too large for the configured bounds or storage is insufficient, the app reports that and keeps the previous revision. Native limits are 16 MiB per manifest, 50,000 songs, 100,000 descriptors, 256 MiB of distinct vector bytes and a 512 MiB private blob cache; ordinary personal libraries should be much smaller.

The phone stores the full manifest as the revision identity. Exact-base delta validation is separate because full and delta representations of the same revision have different checksums. Raw canonical Python JSON bytes, including numeric spellings and Unicode ordering, are validated without a lossy JSON reserialization.

Catalog bytes are private cached data outside Android backup. Restored metadata whose vector files are missing is shown as needing reimport; local media and other saved app data remain available. Explicit Room migration and interrupted-import tests cover the additive storage boundary.

## Retry, revocation and recovery

- If an exchange reply is lost, use **Retry pairing** or **Check connection**. Android saves the candidate credential and exchange ID before transmission; exact retries recover the same device receipt. It does not mint a second device just because a reply was lost. Completed receipts can be recovered for up to 24 hours, while the device remains valid.
- An unused expired ticket needs a new bundle. A conflicting pending exchange must be forgotten locally before redeeming a different bundle. Forgetting pairing clears only this connection; it leaves local music, playlists, history and bookmarks intact.
- Desktop **Refresh** lists devices without starting the gateway. **Revoke** invalidates that individual Radio credential. The phone's next authenticated request is rejected; local playback remains usable. Re-pair explicitly to obtain a new credential. Forgetting locally does not revoke the server's record; revoke the old record on the desktop when replacing it.
- A restored installation, changed installation marker, missing Keystore key or damaged credential file is quarantined. The app does not recreate trust from restored metadata or fall back to plaintext. Forget the inaccessible pairing and provision again from the trusted desktop. Existing local records remain available; restored folder grants may separately need renewal.
- The server signing identity is persisted using the Windows user's DPAPI. An unreadable identity is not silently replaced. Preserve the original identity and data backups, and diagnose Windows-account/restore issues before explicitly replacing it and re-pairing devices. Do not delete the music database or library to repair pairing.
- Check device time if signed identity/ticket freshness fails. A changed key or older accepted generation is a repair condition; the client does not silently accept a new key.
- To remove this forwarding rule when finished:

  ```powershell
  & $radioAdb -d reverse --remove tcp:18766
  ```

## Security boundary

- The listener binds **127.0.0.1:18766 only**, is off at startup, and has a separate ASGI application. No desktop route, general proxy, source-song streaming, admin API or OpenAPI surface is mounted there.
- Exposed methods are GET identity, POST pairing exchange, GET capabilities, GET prepared catalog and GET/HEAD content-addressed vectors under `/api/portable/v1`. Identity has no bearer; exchange requires the ticket; other private routes require a current portable device bearer even though the socket is loopback. Catalog/vector delivery requires `catalog:read`. Host, native-request headers, framing, body size, rate and total body deadline are constrained. Mobile requests cannot trigger preparation or ingestion.
- Electron main sends a random parent capability to its child over a bounded stdin bootstrap pipe. Local pairing administration requires that capability, a loopback peer, and the existing desktop authentication/origin policy. Narrow IPC accepts only the trusted main window. The capability is not returned to the renderer or put in an argument, URL or log.
- Android stores its pending/paired secret in AES-GCM ciphertext protected by Android Keystore. The encrypted directory is excluded from both backup rule formats and device transfer. A separate non-backup installation marker is compared with nonsecret backed-up metadata; mismatch or decryption failure quarantines the state. Catalog metadata uses the separate additive Room v6 tables.
- Identity uses P-256/SHA-256 signatures over the transmitted payload bytes, a SHA-256 SPKI pin, exact endpoint/server identity, bounded freshness and persisted generation/revision floors. Platform TLS/hostname checks stay enabled. Redirects never receive credentials; responses and complete requests are bounded.
- Cleartext is permitted only for the exact USB loopback origin in Android debug builds. Release builds reject it and keep cleartext disabled. This is not a release remote-hosting path. Packaged Electron does not offer this development pairing flow.
- The backend stores bearer/ticket digests and a nonsecret exchange receipt, not plaintext bearer responses. Pairing grants only `catalog:read` in the existing portable authority. The prepared catalog/vector routes expose no profile-writing or playback authority.
- Graceful Electron shutdown disables and closes the owned listener before backend termination. A backend listener failure closes admission. Abrupt parent-death cleanup for a future public tunnel is a separate checkpoint; no public tunnel exists here.

## Short acceptance checklist

1. Update without clearing data. Play a local song; confirm library, playlist, queue, position and sleep timer remain intact while opening the Server settings section.
2. Create and redeem a USB bundle. Check the displayed server identity and Check connection result; verify ordinary local playback continues.
3. Leave the Server section/reopen the app. Pairing persists, and Check connection succeeds after forwarding/gateway availability is restored. App restoration follows the existing paused-bookmark behavior.
4. Unplug USB or remove forwarding. Check connection reports a recoverable failure, while current/local music still works. Reconnect and restore forwarding; retry without re-pairing.
5. Revoke this device on the desktop. The next Check connection rejects it, without erasing local data. Forget and re-pair with a newly created ticket.
6. Let an unused bundle expire and verify it cannot create a device. For a deliberately interrupted exchange, retry the saved pending request and confirm only one device appears.
7. Quit Radio and verify connection is unavailable; relaunch and explicitly enable the USB gateway again. No hosting preference, tunnel or LAN listener should start automatically.

Phone acceptance, USB ergonomics, audible playback and ordinary Electron startup must be reported separately from automated emulator/fixture checks. Stop at checkpoint 1A for user review before catalog import.

Checkpoint 1A was subsequently phone-tested and accepted. For 1B, check: the Server field and Pair button stay above the software keyboard; prepare/import a catalog while local music plays; reopen offline to see the imported revision; cancel/retry an update and confirm the previous catalog survives; then retry after briefly disconnecting USB. Stop for review before content hashing/matching in 1C.

## Match phone files to the server catalog (1C)

1. Restart the updated Electron development app/backend, restore USB forwarding, and let ingestion finish. Choose **Prepare catalog**. This now explicitly hashes source files read-only; unchanged hashes are cached. The first pass may take time. **Cancel preparation** retains completed hash work and the previous prepared catalog. After five minutes of polling, reopen Settings to resume status checks; the server job continues.
2. Install the checkpoint 1C APK without clearing data. Choose **Import catalog** in Android Settings → Server, then open **Local matching** for its own screen and choose **Match local songs**. Keep Settings open during the explicit scan; leaving cancels it while retaining completed hashes. No audio is uploaded or modified.
3. Inspect matched, needs-review and unmatched counts. Renamed identical files match by SHA-256. Duplicate server copies require review. Filename/suffix suggestions alone never auto-confirm. Use the Matching screen’s All / Review / Unmatched / Matched filters and filename/title/artist search. Tap a row for candidate details and Confirm, Reject or Reset decision. Unique candidates can be explicitly selected by row checkbox; choose a specific candidate in details for duplicates. **Confirm selected** applies the selected choices together. Rows remain in place while reviewing; tap the current filter again to refresh its membership. All includes existing matches for correction.
4. Reopen Settings offline and confirm decisions remain. Changed local files invalidate cached identities when provider size/timestamp changes; each explicit correction rereads bytes. Missing size/timestamp metadata prevents hashing. Providers that conceal same-size/timestamp edits cannot guarantee cache invalidation; this is not acoustic matching. Re-encoded/retagged files may need review or remain unmatched.
5. Confirm/reject decisions are scoped to server/catalog plus local bytes and target content identity (or exact target payload when no hash exists). A replaced server recording invalidates its old decision; a removed target remains visible/resettable. Matching never rewrites historic listening records or the active queue.

Phone acceptance: run matching while playing local music, try cancelling/restarting, review a duplicate or uncertain filename, save/reset a decision, then reopen offline. Report the three counts and any files you expected to match. Actual personal-library coverage remains a phone check, not a synthetic-test claim. Recommendations remain a later checkpoint.

## Local listening feedback (2A)

The 2A APK adds persistent Like/Dislike, explicit Replay and saved Skip reasons in Now Playing. This works without USB or a server. The local journal records observed listening time and immutable attempt outcomes; it does not upload or synchronize history yet. Update without clearing data: the explicit v7→v8 migration retains existing library, matching decisions, bookmarks and playlists. Historical starts are not backfilled. Unpaired or unavailable mappings remain local-only for each captured attempt; later matching does not rewrite earlier events. If saving fails, local audio continues and Now Playing offers Retry. Committed data survives restart, while process death before a pending write commits can lose that observation.
