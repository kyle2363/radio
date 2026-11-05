# Secure remote hosting — checkpoint 3A

Checkpoint 3A adds an explicit Cloudflare Quick Tunnel for the existing narrow
mobile gateway. It does not expose the desktop FastAPI application, source MP3s,
playback controls or device administration. The phone continues to own local
playback. Optional endpoint discovery is described in the checkpoint 3B
[setup guide](AUTOMATIC_DISCOVERY.md).

## Desktop setup

1. Restart the updated Electron development app and its owned backend. A backend
   launched separately does not have the private desktop hosting authority.
2. Obtain `cloudflared.exe` from [Cloudflare's official downloads](https://developers.cloudflare.com/tunnel/downloads/).
   A portable test copy is available at
   `.test-runtime/android-checkpoint3a/cloudflared.exe` in this checkout. No
   Windows service, account, domain or inbound firewall rule is installed.
3. In desktop Server settings, open **Remote hosting → Advanced**, enter the
   full executable path and choose **Save path**. Select **Start remote hosting**.
4. Wait for the verified available state. Copy **Phone connection update** to
   update an already-paired phone. The signed descriptor contains public server
   identity and endpoint data; it contains no phone bearer or desktop authority.

Hosting defaults to off. Restore-on-launch is a separate preference, also off
by default. Stop hosting and Quit close remote admission and terminate the owned
tunnel. A changing tunnel URL requires a new signed phone update, supplied
manually below or through configured automatic discovery.

## Phone setup and URL changes

Update the Android APK without clearing its data. Keep the existing pairing.
In **Settings → Server**, choose **Update server address**, paste the desktop's
current signed connection update and verify it. Android checks the pinned server
key and the signed origin, then fetches identity from that origin without a
bearer. Only after successful verification does it persist the endpoint and
enable authenticated requests. A URL by itself cannot authorize replacement.

Use a trusted manual transfer method, as for the original USB pairing bundle.
If the descriptor expires, copy a fresh update from the running desktop. The
same phone device ID, credential, catalog, matching decisions, journal and
pending events remain in place. Do not forget pairing just to change a URL.

For a debug phone originally paired over USB, **Reconnect over trusted USB** explicitly
revalidates the original loopback endpoint. Restore `adb reverse tcp:18766
tcp:18766` and enable the desktop USB connection first. Release builds continue
to reject cleartext transport.

## Failure and limits

- The backend admits only the exact currently assigned HTTPS tunnel Host.
  Private routes require the same scoped device bearer as USB. Cloudflare
  forwarding headers never grant device identity or desktop administration.
- The desktop renews an owner lease every five seconds. Remote admission closes
  after fifteen seconds without renewal; stale generations cannot reopen it.
  A Windows Job Object owns the tunnel child for parent-crash cleanup.
- Retry is bounded. After retry exhaustion use **Retry** explicitly. Failed
  hosting does not stop local music or erase a working catalog or journal.
- Cloudflare is the TLS intermediary. Quick Tunnels are best-effort development
  connectivity, with no uptime guarantee, rotating hostnames and network limits.
  See [Cloudflare's Quick Tunnel limitations](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
- This checkpoint uses manual signed address updates, not Firebase. It adds no
  connected AI recommendation, speech, source-song streaming or automatic sync.

## Phone acceptance

1. Update without clearing data. Start hosting and adopt the signed update on
   the already-paired phone. Check playlists, matching and pending events remain.
2. Disconnect USB and turn phone Wi-Fi off. On cellular, use **Check connection**
   and **Sync now**. Confirm the desktop records the expected phone feedback once.
3. Keep local music playing with a sleep timer. Sync over cellular; song,
   position, queue, mood and timer should remain independent.
4. Stop hosting. Remote checks must fail while local playback and offline Radio
   continue. Restart hosting, copy its fresh update and reconnect without a new
   pairing or duplicated history.
5. Quit/relaunch the desktop with restore disabled. Hosting should stay off.
   Try sleep/wake and a temporary internet outage; check the displayed state and
   explicit recovery, with no phone autoplay or queue replacement.

Automated tunnel probes do not establish cellular reachability on your phone.
Checkpoint acceptance requires these real-device checks.
