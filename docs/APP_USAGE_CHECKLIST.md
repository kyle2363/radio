# Manual App Usage Checklist

Use the isolated QA server for this walkthrough. It runs the real Radio web app
and playback/session API against three generated, silent WAV files. Recommendation
and model generation are mocked; no original library audio, metadata, history,
or taste data is loaded. Each run writes to a new `.test-runtime/qa-*` folder.

## Start the isolated app

From the repository root in PowerShell, run:

```powershell
backend\venv\Scripts\python scripts\run_isolated_app.py --port 18765
```

Open [http://127.0.0.1:18765](http://127.0.0.1:18765) and sign in with:

- **Password:** `isolated-test`

The terminal prints the URL and test password. Keep it open during the check;
press **Ctrl+C** there to stop the server. The launcher disables TTS and managed
model processes and blocks outbound connections. Its recommendation queue is a
deterministic three-song sequence. The WAVs are silent, so use the playback
position, labels, and state changes to check behavior rather than sound quality.

## 1. Load and start playback

- [ y] The app opens without the seed-your-taste onboarding screen.
- [ y] The library contains `Silent Test 1`, `Silent Test 2`, and `Silent Test 3`
      by `QA Ensemble`.
- [ y] Select **This browser** as the output. If the browser displays a user
      gesture prompt, click **Play in this browser**.
- [ y] Start playback. The status changes to playing, the current song appears,
      and its position advances. The queue shows the remaining synthetic songs.
- [ y] Pause, wait a few seconds, and confirm the position stops. Resume and
      confirm it advances again.

## 2. Player controls and feedback

- [ n] Seek to a different position. The displayed position moves to the chosen
      point and continues from there.
      note: clicking to srub freeze playback but the lryics still move normally. due to this other playback keys nolonger work. refeshing results in the home screen but trying to click play will say it failed to load a song. there was a reconnect session button but clicking it caused The station could not complete this playback request.
- [ y] Skip to the next song. The song label changes and progress starts over.
 
- [ y] Replay the current song. It restarts without consuming a second queue item.
      note reply works but it resets the queue to new songs
- [ y] Heart the current song, then confirm the heart state is visible and a
      second click does not add duplicate feedback.
- [ y] Thumbs-down a song, then use **Undo** when offered. Confirm the action
      reverses and the session remains usable. (I only tested this visually)
- [ y] Change mood using the mood buttons. Confirm the selected mood changes and
      remains selected after a brief pause or seek.(I only tested this visually)
- [ y] Adjust volume and EQ if those controls are available in the current view.
      The preference should update without restarting the song.(I only tested this visually)

## 3. Queue and session isolation

- [ ] Add a song to the manual queue, reorder it, then remove it. The displayed
      queue follows each change.
- [ ] Open a fresh blank browser tab, type the same URL, and sign in. (Avoid
      duplicating the current tab, which can copy its tab storage.) The new tab
      creates an independent listening session; starting, pausing, changing
      mood, or changing its queue does not change the first tab's state.
- [ ] Return to the first tab and confirm its current song, queue, mood, and
      position are unchanged.
- [ ] If the UI offers a sleep timer, set a short timer, confirm it appears,
      then cancel it. Optionally let it expire to check the end-of-song stop.

## 4. Transfer the same session between outputs

This checks state transfer only. The isolated launcher configures native VLC to
use dummy output, so **Desktop speakers will remain silent** even if the handoff
succeeds. If VLC is not installed, skip the desktop-output portions; browser
playback remains available.

- [ ] In the first tab, start a song and note its title, approximate position,
      queue order, selected mood, and any active vibe lock or timer.
- [ ] Change **Output** from **This browser** to **Desktop speakers**. If asked,
      confirm the output switch and wait for it to complete.
- [ ] Confirm the same session and song remain active, the queue and mood remain
      intact, and the reported position is near the handoff point.
- [ ] Switch back to **This browser**. If prompted, click **Play in this
      browser** to satisfy the browser's audio gesture requirement.
- [ ] Confirm playback state, queue order, mood, vibe lock, and timer remain
      attached to this session. The former output should not start a duplicate
      copy of the track.
- [ ] If a DJ guest or speech segment is already active in a non-isolated
      environment, repeat the transfer and confirm it remains associated with
      the same session. Isolated QA disables TTS and talk-segment generation,
      so it cannot verify guest audio or voice quality.

## 5. Reconnect and stale-tab checks

- [ ] Reload the active browser tab. If the app offers **Reconnect listening
      session**, use it and confirm the session recovers without an extra song
      starting.
- [ ] If the browser requires a new gesture, click **Play in this browser**.
- [ ] Keep an older tab open after reconnecting or transferring. Confirm it
      cannot control the active output or cause duplicate playback. Use the
      current tab for further controls.

## 6. Stop and data-safety checks

- [ ] Stop playback and confirm the session reaches a stopped/idle state.
- [ ] Press **Ctrl+C** in the launcher terminal. The server exits cleanly.
- [ ] The original `data/`, `audio_files/`, and `docs/understand/` folders are
      not used by this walkthrough. Do not start the production backend for this
      check or run database wipe/delete actions.
- [ ] A later isolated run creates a fresh random QA workspace; it does not
      carry over the prior run's synthetic listening history.

## What this walkthrough does not verify

The synthetic tracks have no audible content. This checklist cannot establish
real-library playback, audible EQ or DJ-effect quality, generated TTS or guest
segments, GPU/model behavior, LAN access, Electron tray/media-key behavior, or
OS sleep and lock integration. Those need a separate review against the actual
environment after the isolated app passes.

## Reliability retest after October 28 fixes

Historical marks above record earlier user feedback; they do not mean the new
build is accepted. Restart the backend/Electron and reload the browser after
updating. Use a fresh isolated run for automated UI checks. Its tracks are silent.

On October 28, after the fixes, the user reported that everything seemed to be
working well and requested a commit. Keep the longer listening scenarios below
open until their individual results and durations have been recorded.

- [ ] Start from idle, pause, scrub forward/back while paused, resume, rapidly
      seek twice, then pause immediately. Verify position and controls stay usable.
- [ ] Note the upcoming order. Replay repeatedly, including while paused.
      Position resets to zero, pause intent and upcoming order remain unchanged.
- [ ] Next still advances once and retains existing skip-feedback/undo behavior.
- [ ] Briefly interrupt only the control connection: buffered audio continues,
      local pause/seek/volume remain usable, and recovery does not restart it.
- [ ] With all server communication unavailable, expect safe local interruption
      when the current producer permit expires (at most about twelve seconds).
      No unavailable recommendation starts while completion is unacknowledged.
- [ ] Reload an active page and explicitly reconnect. The new page has a fresh
      producer identity and can wait for the old permit to expire. A duplicated
      tab must not become a simultaneous producer for the existing grant.
- [ ] Transfer a playing and paused session in both directions with the same
      queue, mood and timer; confirm stale pages cannot control the new output.
- [ ] Test server restart and media failure separately from a WebSocket drop.
      Record whether the last accepted checkpoint, pause intent and attempt
      recover. Unsent observations do not survive destruction of the page.

Keep an actual listening log separately for the sixty-minute browser/Electron
and VLC acceptance sessions: start/end time, output, interactions, disconnect
duration and any audible interruption. Synthetic/native dummy-output passes do
not count as those listening sessions.

### Bug report template

```text
Output mode (This browser / Desktop speakers):
Song ID or title:
Steps to reproduce:
Expected:
Actual:
Browser console output (remove secrets):
Backend log excerpt (remove passwords/tokens):
```
