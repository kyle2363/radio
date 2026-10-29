package dev.radio.mobile.playback

import android.app.PendingIntent
import android.content.Intent
import android.os.Binder
import android.os.IBinder
import androidx.media3.common.Player
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import dev.radio.mobile.MainActivity
import dev.radio.mobile.data.LibraryRepository
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.first

/** Owns the only production player, independent of activity and Compose lifetimes. */
@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
class RadioPlaybackService : MediaSessionService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private lateinit var engine: LocalPlaybackController
    private lateinit var clientController: InitializingPlaybackController
    private var initialized = false
    private var session: MediaSession? = null

    // In-process binding avoids serializing the entire local catalog over controller IPC.
    // The service is non-exported; Android's media session supplies system/earbud controls.
    inner class LocalBinder : Binder() { val controller: PlaybackController get() = clientController }
    private val binder = LocalBinder()

    override fun onCreate() {
        super.onCreate()
        val library = repositoryFactory(this)
        engine = LocalPlaybackController(this, onCheckpoint = { if (initialized) library.playbackSnapshots.save(it) },
            onQueueCleared = { if (initialized) library.playbackSnapshots.clear() }) { song, attempt, position ->
            library.recordPlaybackStart(song.id, attempt, position)
        }
        clientController = InitializingPlaybackController(engine)
        scope.launch {
            try {
                val saved = library.playbackSnapshots.load()
                val catalog = library.state.first { !it.loading && !it.scanning }
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
            .setCallback(object : MediaSession.Callback {
                override fun onConnect(session: MediaSession, controller: MediaSession.ControllerInfo): MediaSession.ConnectionResult {
                    // System controls can operate the existing queue, not replace it with URIs
                    // or alter repeat/speed policies outside the app's current feature set.
                    val commands = MediaSession.ConnectionResult.DEFAULT_PLAYER_COMMANDS.buildUpon()
                        .remove(Player.COMMAND_SET_MEDIA_ITEM).remove(Player.COMMAND_CHANGE_MEDIA_ITEMS)
                        .remove(Player.COMMAND_SET_REPEAT_MODE).remove(Player.COMMAND_SET_SPEED_AND_PITCH).build()
                    return MediaSession.ConnectionResult.AcceptedResultBuilder()
                        .setAvailablePlayerCommands(commands).build()
                }
            }).build().also { addSession(it) }
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
        // Dependency seam lets service instrumentation use an in-memory catalog and journal.
        internal var repositoryFactory: (android.content.Context) -> LibraryRepository = LibraryRepository::get
    }
}
