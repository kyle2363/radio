package dev.radio.mobile.playback

import android.app.PendingIntent
import android.content.Intent
import android.os.Binder
import android.os.Bundle
import android.os.IBinder
import androidx.media3.common.Player
import androidx.media3.session.CommandButton
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import dev.radio.mobile.MainActivity
import dev.radio.mobile.data.LibraryRepository
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.distinctUntilChanged

/** Owns the only production player, independent of activity and Compose lifetimes. */
@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
class RadioPlaybackService : MediaSessionService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private lateinit var engine: LocalPlaybackController
    private lateinit var clientController: InitializingPlaybackController
    private var initialized = false
    private var session: MediaSession? = null
    private lateinit var prefs: PlaybackPrefs
    // Leave previous/play-pause/next in Android's standard transport slots. Shuffle and
    // repeat use overflow custom-action slots (plain icons in the expanded system controls).
    private val shuffleCommand = SessionCommand(ACTION_TOGGLE_SHUFFLE, Bundle.EMPTY)
    private val repeatCommand = SessionCommand(ACTION_CYCLE_REPEAT, Bundle.EMPTY)

    // In-process binding avoids serializing the entire local catalog over controller IPC.
    // The service is non-exported; Android's media session supplies system/earbud controls.
    inner class LocalBinder : Binder() { val controller: PlaybackController get() = clientController }
    private val binder = LocalBinder()

    override fun onCreate() {
        super.onCreate()
        val library = repositoryFactory(this)
        prefs = PlaybackPrefs(this)
        engine = LocalPlaybackController(this, onCheckpoint = { if (initialized) library.playbackSnapshots.save(it) },
            onQueueCleared = { if (initialized) library.playbackSnapshots.clear() }) { song, attempt, position ->
            library.recordPlaybackStart(song.id, attempt, position)
        }
        // Repeat is a preference, not queue content: apply it before the session exists so the
        // system controls never show a stale icon after a restart.
        engine.setRepeat(prefs.repeat)
        clientController = InitializingPlaybackController(engine)
        scope.launch { library.loudness.state.map { it.profile }.distinctUntilChanged().collect { engine.updateNormalization(it) } }
        scope.launch {
            try {
                val saved = library.playbackSnapshots.load()
                val catalog = library.state.first { !it.loading && !it.scanning }
                engine.updateNormalization(library.loudness.state.first { it.loaded }.profile)
                if (saved != null) engine.restorePaused(saved, catalog.songs, catalog.accessLost)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) {
                android.util.Log.w("RadioPlayback", "Couldn't restore playback bookmark", error)
                engine.restorationFailed("Couldn't restore the previous session. Choose a song from your library.")
            }
            initialized = true
            createMediaSession()
            clientController.initialized()
            library.state.collect { state ->
                if (!state.loading && !state.scanning) engine.reconcileLibrary(state.songs, state.accessLost)
            }
        }
        library.checkAccess()
    }

    private fun createMediaSession() {
        val activity = PendingIntent.getActivity(this, 0,
            Intent(this, MainActivity::class.java).setAction(MainActivity.ACTION_NOW_PLAYING)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        session = MediaSession.Builder(this, engine.sessionPlayer).setSessionActivity(activity)
            .setMediaButtonPreferences(mediaButtons(clientController.state.value.shuffled, clientController.state.value.repeat))
            .setCallback(object : MediaSession.Callback {
                override fun onConnect(session: MediaSession, controller: MediaSession.ControllerInfo): MediaSession.ConnectionResult {
                    // System controls can operate the existing queue, not replace it with URIs
                    // or alter repeat/speed policies outside the app's current feature set.
                    val commands = MediaSession.ConnectionResult.DEFAULT_PLAYER_COMMANDS.buildUpon()
                        .remove(Player.COMMAND_SET_MEDIA_ITEM).remove(Player.COMMAND_CHANGE_MEDIA_ITEMS)
                        .remove(Player.COMMAND_SET_REPEAT_MODE).remove(Player.COMMAND_SET_SPEED_AND_PITCH).build()
                    return MediaSession.ConnectionResult.AcceptedResultBuilder()
                        .setAvailableSessionCommands(MediaSession.ConnectionResult.DEFAULT_SESSION_COMMANDS
                            .buildUpon().add(shuffleCommand).add(repeatCommand).build())
                        .setAvailablePlayerCommands(commands).build()
                }

                override fun onCustomCommand(session: MediaSession, controller: MediaSession.ControllerInfo,
                    customCommand: SessionCommand, args: Bundle): ListenableFuture<SessionResult> {
                    when (customCommand.customAction) {
                        ACTION_TOGGLE_SHUFFLE -> clientController.shuffle(!clientController.state.value.shuffled)
                        ACTION_CYCLE_REPEAT -> clientController.setRepeat(clientController.state.value.repeat.next())
                        else -> return super.onCustomCommand(session, controller, customCommand, args)
                    }
                    return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS))
                }
            }).build().also { addSession(it) }
        // Keep both system buttons aligned with the same shared state the app uses, and
        // remember repeat so it survives the next restart.
        scope.launch {
            clientController.state.map { it.shuffled to it.repeat }.distinctUntilChanged().collect { (shuffled, repeat) ->
                session?.setMediaButtonPreferences(mediaButtons(shuffled, repeat))
                prefs.repeat = repeat
            }
        }
    }

    private fun mediaButtons(shuffled: Boolean, repeat: RepeatMode) =
        listOf(shuffleButton(shuffled), repeatButton(repeat))

    // SystemUI doesn't support BACK_SECONDARY/FORWARD_SECONDARY: Media3 drops those
    // buttons when publishing the framework session. OVERFLOW keeps these separate from
    // native transport; Android owns their placement and background styling.
    private fun shuffleButton(shuffled: Boolean) = CommandButton.Builder(
        if (shuffled) CommandButton.ICON_SHUFFLE_ON else CommandButton.ICON_SHUFFLE_OFF)
        .setDisplayName("Shuffle")
        .setSessionCommand(shuffleCommand)
        .setSlots(CommandButton.SLOT_OVERFLOW)
        .build()

    private fun repeatButton(repeat: RepeatMode) = CommandButton.Builder(
        when (repeat) {
            RepeatMode.OFF -> CommandButton.ICON_REPEAT_OFF
            RepeatMode.ALL -> CommandButton.ICON_REPEAT_ALL
            RepeatMode.ONE -> CommandButton.ICON_REPEAT_ONE
        })
        .setDisplayName("Repeat")
        .setSessionCommand(repeatCommand)
        .setSlots(CommandButton.SLOT_OVERFLOW)
        .build()

    private fun RepeatMode.next() = when (this) {
        RepeatMode.OFF -> RepeatMode.ALL
        RepeatMode.ALL -> RepeatMode.ONE
        RepeatMode.ONE -> RepeatMode.OFF
    }

    override fun onBind(intent: Intent?): IBinder? {
        val mediaBinder = super.onBind(intent)
        return if (intent?.action == ACTION_LOCAL_BIND) binder else mediaBinder
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = session

    // MediaSessionService keeps ongoing playback after task removal, stops idle playback,
    // and handles the media notification/foreground lifetime without a second notification.
    override fun onDestroy() {
        scope.cancel()
        session?.release()
        session = null
        engine.close()
        super.onDestroy()
    }

    companion object {
        const val ACTION_LOCAL_BIND = "dev.radio.mobile.BIND_PLAYBACK"
        const val ACTION_TOGGLE_SHUFFLE = "dev.radio.mobile.TOGGLE_SHUFFLE"
        const val ACTION_CYCLE_REPEAT = "dev.radio.mobile.CYCLE_REPEAT"
        // Dependency seam lets service instrumentation use an in-memory catalog and journal.
        internal var repositoryFactory: (android.content.Context) -> LibraryRepository = LibraryRepository::get
    }
}
