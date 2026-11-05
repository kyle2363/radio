# Automatic server discovery — checkpoint 3B

Discovery lets an already-paired phone find the desktop's new Quick Tunnel
address. Existing USB pairing, manual signed address updates and local music
remain available. Discovery does not schedule profile sync or add connected
recommendations.

## Firebase setup

Use your own [Firebase console](https://console.firebase.google.com/). No Firebase
project or live resource has been created by the implementation or emulator tests.
Keep passwords and refresh tokens out of chat, source files and screenshots.

1. Create a project on **Spark**, without linking a billing account. Google
   Analytics is optional and is not needed by this feature. Spark requires no
   payment information; see [Firebase pricing plans](https://firebase.google.com/docs/projects/billing/firebase-pricing-plans).
2. Create **Cloud Firestore**, **Standard edition**, the **(default)** database,
   in a location near you. Start in **production mode** with access denied.
   Choose the location carefully; the app uses this one database.
3. Under **Authentication → Sign-in method**, enable **Email/Password**.
   Under **Users**, add two different users: a **desktop publisher** and a
   **phone reader**. Save their passwords privately and record their UIDs.
   The phone must use the reader account. Neither account needs Google Cloud
   administrative permissions.
4. In project settings, record the **Project ID** and **Web API Key**. If necessary,
   register a Web app to obtain the Firebase configuration. This REST integration
   uses only those two configuration values; no Google service-account JSON or
   Android Gradle Firebase plugin is needed. An API key identifies the Firebase
   project; authentication and the rules below enforce access.
5. Start the updated desktop app and remote hosting. In its discovery settings,
   record the existing **Server ID**. Preserve this identity and your existing
   phone pairing.
6. Prepare the exact rules from the repository root, substituting the three
   public identifiers (not emails or passwords):

   ```powershell
   node firebase/render-rules.mjs SERVER_UUID PUBLISHER_UID READER_UID
   ```

   Review `.test-runtime/android-checkpoint3b/firestore.rules`, then paste its
   contents into **Firestore Database → Rules** and choose **Publish**. This is
   the only deployment required. The checked-in template is intentionally
   unconfigured. Do not enable general authenticated access or test-mode rules.
7. In desktop **Server → Remote hosting → Automatic endpoint discovery (Firebase)**, enter the
   project configuration and sign in with the **publisher** account.
8. Install the checkpoint APK without clearing data. On the phone, open
   **Settings → Server → Automatic server discovery**, save the same project
   configuration, and sign in with the **reader** account. Choose **Check
   discovery**. The existing pinned server identity verifies the replacement
   address before the phone sends any Radio credential there.

Firebase ID tokens authorize Firestore through its Security Rules; service-account
credentials instead use IAM and bypass these rules, so this feature does not use
them. See [Firestore REST authorization](https://firebase.google.com/docs/firestore/use-rest-api)
and [Firebase Auth REST](https://firebase.google.com/docs/reference/rest/auth).

## What to test

1. Verify discovery succeeds on the phone, then use **Check connection** and
   **Sync now** over cellular with USB disconnected and Wi-Fi off.
2. Play a local song. Stop and restart desktop remote hosting so it receives a
   different URL. Wait for desktop publication, then return to the phone app or
   choose **Check discovery** after its throttle interval. Check connection and
   manual sync must work without pasting an address or pairing again. The local
   song, position and queue must continue.
3. Close the desktop app. Local phone music must continue; discovery must not
   claim that an old online record proves reachability. Restart the desktop and
   hosting, then repeat discovery and connection checks.
4. Sign out of discovery on the phone. Local playback and the already-validated
   manual connection remain usable. Sign back in with the reader account.
5. Confirm no passwords, Firebase tokens or Radio bearers appear in copied
   connection updates, the Firestore document or ordinary diagnostics.

Phone and live Firebase acceptance require your setup and device testing. Local
emulator checks alone do not establish cellular acceptance.

## Limits and recovery

The one document is `discovery/SERVER_UUID`. It contains the signed public
endpoint descriptor, matching metadata and a server timestamp. It never contains
music, listening history or Radio credentials. Only the exact publisher UID can
write it; only the publisher and reader can get it. Listing, deletes and other
documents are denied. Rules check types, freshness and advancing revisions;
the phone independently verifies the signature against its paired key.

The publisher renews a five-minute advertisement every minute while hosting is
available. On stop or crash it stops publishing; the old record expires rather
than being deleted. Availability remains a reachability observation, not a promise
from Firestore. Phone reads are bounded and idle background polling is disabled.
Quota or authorization failures need recovery instead of unbounded retries.

Firestore currently includes 50,000 reads and 20,000 writes daily in its free
quota. One publication per minute is about 1,440 writes/day; other project usage
and retries also count. Monitor **Firestore → Usage** and **Authentication →
Usage**. These estimates do not guarantee remaining quota. See
[Firestore quotas](https://firebase.google.com/docs/firestore/quotas).

For permission errors, compare the exact server UUID and both UIDs in the
published rules. Ensure the desktop uses the publisher account and the phone
uses the reader account. For clock/freshness errors, correct device clocks.
For quota exhaustion, wait for quota recovery; local playback still works, and
you can use the existing manual signed update workflow.

Sign out in the app to remove its saved discovery credentials. Disable/delete
an account in Firebase Authentication to block future sign-in/refresh; also remove
its UID from the rules to revoke Firestore access immediately, including an
already-issued ID token. Firebase account removal does not revoke the separate
Radio device credential; use desktop device revocation for that.

Missing protected-storage keys require discovery sign-in again. Server signing-key
loss or a restored older server identity requires trusted repair; discovery must
never replace the pinned key or lower accepted revision floors. Keep the current
pairing when merely changing a tunnel URL.

## Local rules verification

From `firebase/`, run `npm ci` then `npm test`. This uses only the demo project
`demo-radio-discovery` and a loopback Firestore emulator. Node 24.12 or newer and
a Firebase-supported Java runtime (21 or newer) are required. On Windows the runner uses
Android Studio's bundled runtime when `JAVA_HOME` is unset. Logs and emulator
downloads go under `.test-runtime/`.

If `JAVA_HOME` points to an older runtime, set it for the test command, for
example `$env:JAVA_HOME='C:\Program Files\Android\Android Studio\jbr'` in PowerShell.

The tests cover publisher/reader/public/stranger permissions, forbidden listing
and deletion, exact paths and fields, stale and reordered writes, endpoint
shape, timestamps, and the REST commit transform/read path. See
[Firebase rules testing](https://firebase.google.com/docs/firestore/security/test-rules-emulator).
