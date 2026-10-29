package dev.radio.mobile

import android.app.Notification
import android.app.NotificationManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.net.Uri
import android.os.PowerManager
import android.provider.DocumentsContract
import android.view.KeyEvent
import androidx.lifecycle.Lifecycle
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.room.Room
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.playback.*
import kotlinx.coroutines.runBlocking
import org.junit.*
import org.junit.Assert.*
import java.util.concurrent.TimeUnit

/** Service tests use an in-memory catalog/journal and test-provider silence, never phone media. */
class BackgroundPlaybackTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private lateinit var db: LibraryDatabase
    private lateinit var library: LibraryRepository
    private var scenario: ActivityScenario<MainActivity>? = null
    private lateinit var playback: ServicePlaybackController
    private var media: MediaController? = null
    private lateinit var songs: List<LocalSong>
    private val notifications get() = context.getSystemService(NotificationManager::class.java).activeNotifications
    private fun main(block: () -> Unit) = instrumentation.runOnMainSync(block)
    private fun await(message: String, condition: () -> Boolean) {
        val until = android.os.SystemClock.elapsedRealtime() + 10000
        while (android.os.SystemClock.elapsedRealtime() < until) {
            var ready = false; main { ready = condition() }
            if (ready) return
            Thread.sleep(40)
        }
        fail(message + ": " + playback.state.value)
    }
    private fun shell(command: String) {
        instrumentation.uiAutomation.executeShellCommand(command).use { descriptor ->
            java.io.FileInputStream(descriptor.fileDescriptor).use { it.readBytes() }
        }
    }
    private fun starts() = runBlocking { db.library().playbackStarts().size }
    @Before fun setup() {
        shell("input keyevent 224")
        shell("wm dismiss-keyguard")
        val root = FixtureDocumentsProvider.tree()
        val control = Uri.parse("content://dev.radio.mobile.test.control")
        context.contentResolver.call(control, "background", null, null)
        context.contentResolver.call(control, "grant", null, null)
        context.contentResolver.takePersistableUriPermission(root, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        songs = (1..2).map { i -> LocalSong("background$i", root.toString(), "root",
            DocumentsContract.buildDocumentUriUsingTree(root, "background$i").toString(),
            "Background $i.mp3", "Music", i.toLong(), 1, 1, 60000) }
        runBlocking {
            db.library().saveRoot(LibraryRoot(uri = root.toString(), folderId = "root", name = "Music", scannedAt = 1))
            db.library().saveSongs(songs)
        }
        library = LibraryRepository(context, db)
        main { RadioPlaybackService.repositoryFactory = { library } }
        scenario = ActivityScenario.launch(MainActivity::class.java)
        await("Library didn't load") { !library.state.value.loading }
        main { playback = ServicePlaybackController(context); playback.playFrom(songs, songs.first().id, "Background test") }
        await("Service didn't play") { playback.state.value.isPlaying && playback.state.value.positionMs > 100 }
    }
    @After fun cleanup() {
        shell("input keyevent 224")
        shell("wm dismiss-keyguard")
        main { media?.stop(); media?.release(); media = null; playback.pause(); playback.close() }
        scenario?.close(); scenario = null
        context.stopService(Intent(context, RadioPlaybackService::class.java))
        instrumentation.waitForIdleSync()
        main { RadioPlaybackService.repositoryFactory = LibraryRepository::get }
        runBlocking { library.close() }
        db.close()
        runCatching { context.contentResolver.releasePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION) }
    }
    @Test fun screenOffAndClosedActivityKeepOneForegroundPlaybackSession() {
        await("Media foreground notification missing") { notifications.any { it.notification.flags and Notification.FLAG_FOREGROUND_SERVICE != 0 } }
        val notification = notifications.first { it.notification.extras.containsKey(Notification.EXTRA_MEDIA_SESSION) }.notification
        assertEquals("Background 1", notification.extras.getCharSequence(Notification.EXTRA_TITLE).toString())
        assertNotNull(notification.contentIntent)
        var before = 0L
        main { before = playback.state.value.positionMs }
        scenario!!.moveToState(Lifecycle.State.CREATED)
        shell("input keyevent 223")
        await("Playback stalled with screen off") {
            !context.getSystemService(PowerManager::class.java).isInteractive && playback.state.value.isPlaying && playback.state.value.positionMs > before + 350
        }
        shell("input keyevent 224")
        shell("wm dismiss-keyguard")
        scenario!!.moveToState(Lifecycle.State.RESUMED)
        scenario!!.recreate()
        await("Activity recreation interrupted playback") { playback.state.value.isPlaying && playback.state.value.positionMs > before + 350 }
        main { before = playback.state.value.positionMs; playback.close() }
        scenario!!.close(); scenario = null
        // No UI connection or test MediaController remains bound. Rebinding must find the
        // same ongoing service rather than an empty replacement player.
        Thread.sleep(250)
        main { playback = ServicePlaybackController(context) }
        await("Closing the UI lost the active session") { playback.state.value.isPlaying && playback.state.value.positionMs > before }
        assertEquals(songs.map { it.id }, playback.state.value.queue.map { it.id })
        assertEquals(songs.first().id, playback.state.value.currentId)
        await("Expected exactly one actual start") { starts() == 1 }
    }
    @Test fun systemTransportAndFocusUseTheSameQueueAndJournal() {
        lateinit var future: com.google.common.util.concurrent.ListenableFuture<MediaController>
        main { future = MediaController.Builder(context, SessionToken(context, ComponentName(context, RadioPlaybackService::class.java))).buildAsync() }
        media = future.get(10, TimeUnit.SECONDS)
        await("Start wasn't journaled") { starts() == 1 }
        main { media!!.pause(); media!!.seekTo(2000) }
        await("System pause/seek didn't reach app state") { !playback.state.value.isPlaying && playback.state.value.positionMs in 1950..2100 }
        scenario!!.moveToState(Lifecycle.State.CREATED)
        main { media!!.play() }
        await("System Play didn't resume in background") { playback.state.value.isPlaying && playback.state.value.positionMs > 2000 }
        assertEquals(1, starts())
        main { media!!.setShuffleModeEnabled(true) }
        await("System shuffle didn't reach shared state") { playback.state.value.shuffled }
        assertEquals(playback.state.value.currentId, playback.state.value.playOrder.first())
        main { media!!.seekToNextMediaItem() }
        await("System Next didn't follow queue") { playback.state.value.currentId == songs.last().id && playback.state.value.isPlaying && starts() == 2 }
        val audio = context.getSystemService(AudioManager::class.java)
        fun key(code: Int) { main {
            audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
            audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
        } }
        key(KeyEvent.KEYCODE_MEDIA_PAUSE)
        await("Headset Pause wasn't handled") { !playback.state.value.playWhenReady }
        key(KeyEvent.KEYCODE_MEDIA_PLAY)
        await("Headset Play wasn't handled") { playback.state.value.isPlaying }
        assertEquals(2, starts())
        scenario!!.moveToState(Lifecycle.State.RESUMED)
        val focus = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
            .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).build())
            .setOnAudioFocusChangeListener { }.build()
        try {
            main { assertEquals(AudioManager.AUDIOFOCUS_REQUEST_GRANTED, audio.requestAudioFocus(focus)) }
            await("Transient interruption didn't pause audio") { !playback.state.value.isPlaying }
        } finally { main { audio.abandonAudioFocusRequest(focus) } }
        await("Audio didn't resume after transient focus returned") { playback.state.value.isPlaying }
        assertEquals(2, starts())
        // Physical route disconnection needs an earbud check: Android protects the actual
        // becoming-noisy broadcast from app/test injection. Media3 handles that route event.
        key(KeyEvent.KEYCODE_MEDIA_PAUSE)
        await("Final headset Pause wasn't handled") { !playback.state.value.playWhenReady }
        scenario!!.moveToState(Lifecycle.State.CREATED)
        scenario!!.moveToState(Lifecycle.State.RESUMED)
        assertFalse("Reopening must not override an explicit pause", playback.state.value.playWhenReady)
        assertEquals(2, starts())
    }
}
