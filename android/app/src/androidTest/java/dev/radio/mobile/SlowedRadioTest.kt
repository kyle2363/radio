package dev.radio.mobile

import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.media3.common.PlaybackParameters
import androidx.room.Room
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.connection.RadioBinaryResponse
import dev.radio.mobile.connection.RadioResponse
import dev.radio.mobile.data.*
import dev.radio.mobile.playback.*
import dev.radio.mobile.recommendation.*
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/** A desktop that voices Slowed lines: the bank manifest, line downloads and play reports (checkpoint 14B wire). */
class FakeSlowedDesktop(private val audio: ByteArray) : ConnectedServerLink {
    data class Line(val id: String, val guest: String, val kind: String, val preset: String?, val bytes: ByteArray = ByteArray(0))
    val lines = CopyOnWriteArrayList<Line>()
    val retired = CopyOnWriteArrayList<String>()
    val plays = CopyOnWriteArrayList<String>()
    val downloads = CopyOnWriteArrayList<String>()
    @Volatile var revision = 0L
    @Volatile var key = "srv:device"
    @Volatile var corrupt: String? = null

    fun add(guest: String, kind: String, preset: String?): String {
        val id = "line" + (lines.size + retired.size).toString().padStart(14, '0')
        lines += Line(id, guest, kind, preset, audio); revision++
        return id
    }
    fun retire(id: String) { lines.removeIf { it.id == id }; retired += id; revision++ }

    override suspend fun access() = ConnectedAccess(key, true, true, talk = TalkCapability(true, null, slowed = true), eventEffects = true)

    override suspend fun post(path: String, body: String): RadioResponse {
        val request = ConnectedJson.parse(body) as Map<*, *>
        return when (path) {
            "/talk/slowed-lines" -> {
                val since = (request["since"] as? Long) ?: 0L
                RadioResponse(200, ConnectedJson.write(linkedMapOf("protocol_version" to 1, "revision" to revision, "full" to (since == 0L),
                    "lines" to lines.map { l -> linkedMapOf("id" to l.id, "guest" to l.guest, "kind" to l.kind, "preset" to l.preset,
                        "sha256" to sha(l.bytes), "byte_length" to l.bytes.size, "duration_ms" to 2000, "mime" to "audio/mp4", "created" to 1) },
                    "retired" to if (since == 0L) emptyList() else retired.toList())))
            }
            "/talk/slowed-lines/plays" -> { plays += (request["plays"] as Map<*, *>).keys.map { it.toString() }; RadioResponse(200, "{\"recorded\":1,\"retired\":0}") }
            else -> RadioResponse(404, "{}")
        }
    }

    override suspend fun download(path: String, maxBytes: Int): RadioBinaryResponse {
        val id = path.removePrefix("/talk/slowed-lines/")
        downloads += id
        val line = lines.firstOrNull { it.id == id } ?: return RadioBinaryResponse(404, ByteArray(0))
        return RadioBinaryResponse(200, if (id == corrupt) line.bytes.copyOf().also { it[it.size - 1] = (it.last() + 1).toByte() } else line.bytes)
    }

    companion object { fun sha(b: ByteArray) = MessageDigest.getInstance("SHA-256").digest(b).joinToString("") { "%02x".format(it) } }
}

/** Checkpoint 14C/14D: the line bank and a manual Slowed run on the real Media3 timeline. */
class SlowedRadioTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private lateinit var db: LibraryDatabase
    private lateinit var scope: CoroutineScope
    private lateinit var mainScope: CoroutineScope
    private lateinit var radio: RadioRepository
    private lateinit var desktop: FakeSlowedDesktop
    private var scenario: ActivityScenario<MainActivity>? = null
    private var controller: LocalPlaybackController? = null
    private var slowed: SlowedCoordinator? = null
    private val bankDir get() = File(context.noBackupFilesDir, "slowed_lines")
    private fun main(block: () -> Unit) = instrumentation.runOnMainSync(block)
    private fun song(id: String) = LocalSong(id, FixtureDocumentsProvider.tree().toString(), "root",
        DocumentsContract.buildDocumentUriUsingTree(FixtureDocumentsProvider.tree(), id).toString(), "$id.mp3", "", 1, 1, 1, 8000)

    @Before fun setup() {
        for (name in listOf("radio_recommendations", "radio_shell")) context.getSharedPreferences(name, 0).edit().clear().commit()
        bankDir.deleteRecursively()
        val audio = instrumentation.context.assets.open("slowed/line.m4a").use { it.readBytes() }
        desktop = FakeSlowedDesktop(audio)
        db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        mainScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
        val songs = listOf(song("long1"), song("long2"), song("short1"))
        radio = RadioRepository(context, db, MutableStateFlow(LibraryState(songs = songs, loading = false)), ListeningJournalRepository(context, db, scope), scope, connectedLink = { desktop })
    }

    @After fun cleanup() {
        controller?.let { c -> main { c.close() } }
        scenario?.close()
        runBlocking {
            if (::mainScope.isInitialized) mainScope.coroutineContext[Job]!!.cancelAndJoin()
            if (::scope.isInitialized) scope.coroutineContext[Job]!!.cancelAndJoin()
        }
        if (::db.isInitialized) db.close()
        bankDir.deleteRecursively()
        for (name in listOf("radio_recommendations", "radio_shell")) context.getSharedPreferences(name, 0).edit().clear().commit()
        runCatching { context.contentResolver.releasePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION) }
    }

    @Test fun bankSyncsVerifiesRetiresAndReportsPlays() = runBlocking {
        val bank = radio.slowedBank
        val first = desktop.add("guest_undertow", SlowedKinds.OPENER_AUTO, "slowed")
        val bad = desktop.add("guest_undertow", SlowedKinds.CLOSER, "slowed")
        val generic = desktop.add("guest_chill", SlowedKinds.CLOSER_GENERIC, null)
        desktop.corrupt = bad
        assertTrue(bank.sync())
        assertEquals(setOf(first, generic), bank.state.value.lines.map { it.id }.toSet())
        assertFalse("a corrupt download never lands", File(bankDir, "$bad.m4a").exists())
        assertEquals("an incomplete pass keeps the old revision", 0L, bank.state.value.revision)
        desktop.corrupt = null
        assertTrue(bank.sync())
        assertEquals(3, bank.state.value.count); assertEquals(3L, bank.state.value.revision)
        // Only missing lines are fetched again.
        assertEquals(1, desktop.downloads.count { it == first })
        desktop.retire(first)
        bank.played(generic)
        assertTrue(bank.sync())
        assertFalse(File(bankDir, "$first.m4a").exists())
        assertEquals(listOf(generic), desktop.plays.toList())
        // Survives a restart of the store.
        val reloaded = SlowedBank(bankDir, desktop)
        assertEquals(setOf(bad, generic), reloaded.state.value.lines.map { it.id }.toSet())
        assertEquals(listOf(generic), reloaded.state.value.recent[SlowedKinds.CLOSER_GENERIC])
        // Switching desktops clears it.
        desktop.key = "other:device"
        assertTrue(bank.sync())
        assertEquals(setOf(bad, generic), bank.state.value.lines.map { it.id }.toSet())
        assertEquals("other:device", bank.state.value.serverKey)
    }

    private fun parameters(): PlaybackParameters {
        if (android.os.Looper.myLooper() == android.os.Looper.getMainLooper()) return controller!!.sessionPlayer.playbackParameters
        var value = PlaybackParameters.DEFAULT; main { value = controller!!.sessionPlayer.playbackParameters }; return value
    }
    private fun await(message: String, timeoutMs: Long = 20_000, condition: () -> Boolean) {
        val end = android.os.SystemClock.elapsedRealtime() + timeoutMs
        while (android.os.SystemClock.elapsedRealtime() < end) {
            var ready = false; main { ready = condition() }
            if (ready) return
            Thread.sleep(25)
        }
        fail(message + ": " + parameters() + " " + controller!!.state.value + " " + slowed?.state?.value)
    }

    private fun startPlayer(random: kotlin.random.Random = kotlin.random.Random.Default): LocalPlaybackController {
        val control = Uri.parse("content://dev.radio.mobile.test.control")
        context.contentResolver.call(control, "audio", null, null)
        context.contentResolver.call(control, "grant", null, null)
        context.contentResolver.takePersistableUriPermission(FixtureDocumentsProvider.tree(), Intent.FLAG_GRANT_READ_URI_PERMISSION)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        main {
            val engine = LocalPlaybackController(context)
            val coordinator = SlowedCoordinator(engine, radio, mainScope, random)
            engine.talkFileAllowed = { coordinator.ownsFile(it) }
            engine.onTalk = { coordinator.onTalk(it) }
            engine.onUserNewMix = { coordinator.userNewMix() }
            controller = engine; slowed = coordinator
        }
        return controller!!
    }

    private fun prepareSlowed() = runBlocking {
        for (kind in listOf(SlowedKinds.OPENER_MANUAL, SlowedKinds.OPENER_AUTO, SlowedKinds.CLOSER)) {
            desktop.add("guest_undertow", kind, "slowed"); desktop.add("guest_chill", kind, "slowed")
        }
        assertTrue(radio.slowedBank.sync())
        // Plain Radio's set plays from feels that allow Slowed.
        db.feels().saveFeel(LocalFeel("rainy", "Rainy", "#336699", false, false, 0, 1, "d", "e"))
        db.feels().insertStates(listOf("long2", "short1", "a", "b", "c").map { LocalSongFeelState("rainy", it, .9, .9, true, 1) })
        radio.setSlowedAllowed("rainy", true)
        radio.setTalkSettings(TalkSettings(enabled = true))
        radio.setSlowedSettings(SlowedSettings(enabled = true, frequency = SlowedFrequency.OFF, presets = setOf(SlowedPreset.SLOWED)))
    }

    @Test fun manualRunSlowsTheNextSongsAndBackToNormalRevertsWithTheCloser() {
        prepareSlowed()
        val engine = startPlayer()
        main { engine.beginRadio(listOf(song("long1"), song("long2"), song("short1"))) }
        await("Radio didn't start") { engine.state.value.isPlaying && engine.state.value.currentId == "long1" && engine.state.value.durationMs != null }
        await("Slow it down isn't offered") { slowed!!.state.value.canStart }
        main { slowed!!.startManual(SlowedPreset.SLOWED) }
        // The interrupted song fades out; the guest's opener plays at normal speed, then the next song is slowed.
        await("Opener never played") { engine.state.value.talkPlaying && engine.state.value.talk?.info?.slowed?.moment == "opener" }
        assertEquals(1f, parameters().speed, 1e-3f)
        await("Set never committed") { slowed!!.state.value.running }
        main { engine.seek(engine.state.value.durationMs!! - 300) }
        await("Next song not slowed") { engine.state.value.currentId == "long2" && engine.state.value.slowed == SlowedPreset.SLOWED &&
            kotlin.math.abs(parameters().pitch - 0.85f) < 1e-3 }
        main { slowed!!.songStarted(song("long2")) }
        // Back to normal: the closer plays at normal speed and the effect is gone.
        main { slowed!!.backToNormal() }
        await("Closer never played") { engine.state.value.talkPlaying && engine.state.value.talk?.info?.slowed?.moment == "closer" }
        await("Effect still on") { !slowed!!.state.value.running && kotlin.math.abs(parameters().speed - 1f) < 1e-3 }
        assertNull(engine.state.value.slowed)
        assertNull(radio.slowedPool)
    }

    /** Every roll comes up: a set always starts when allowed, the first choice is always taken. */
    private object Lucky : kotlin.random.Random() { override fun nextBits(bitCount: Int) = 0 }

    @Test fun automaticSetOpensFromTheBankBeforeTheBoundaryAndSlowsTheNextSong() {
        prepareSlowed()
        radio.setSlowedSettings(SlowedSettings(enabled = true, frequency = SlowedFrequency.OFTEN, presets = setOf(SlowedPreset.SLOWED)))
        val engine = startPlayer(Lucky)
        main { engine.beginRadio(listOf(song("long1"), song("long2"), song("short1"))) }
        await("Radio didn't start") { engine.state.value.isPlaying && engine.state.value.currentId == "long1" && engine.state.value.durationMs != null }
        main { slowed!!.songStarted(song("long1")) }
        // No desktop line: the bank's opener goes in shortly before the boundary; nothing is slowed until it plays.
        await("Bank opener not queued") { engine.state.value.talk?.info?.slowed?.moment == "opener" }
        assertTrue(engine.state.value.talk!!.info.fromBank)
        assertNull(engine.state.value.slowed)
        await("Opener never played", 30_000) { engine.state.value.talkPlaying }
        await("Set never committed") { slowed!!.state.value.running && !slowed!!.state.value.manual }
        main { engine.seek(engine.state.value.durationMs!! - 300) }
        await("Next song not slowed") { engine.state.value.currentId == "long2" && kotlin.math.abs(parameters().speed - 0.85f) < 1e-3 }
        assertEquals(4, slowed!!.state.value.songsLeft)
    }

    @Test fun leavingRadioStopsSlowedImmediately() {
        prepareSlowed()
        val engine = startPlayer()
        main { engine.beginRadio(listOf(song("long1"), song("long2"), song("short1"))) }
        await("Radio didn't start") { engine.state.value.isPlaying && engine.state.value.currentId == "long1" }
        await("Slow it down isn't offered") { slowed!!.state.value.canStart }
        main { slowed!!.startManual(SlowedPreset.SLOWED) }
        await("Set never committed") { slowed!!.state.value.running }
        main { engine.endRadio(stopNow = false) }
        await("Slowed didn't stop") { !slowed!!.state.value.running && engine.slowed == SlowedPlan.OFF }
        assertNull(radio.slowedPool)
    }

    @Test fun noLinesMeansNoRun() {
        runBlocking {
            radio.setTalkSettings(TalkSettings(enabled = true))
            radio.setSlowedSettings(SlowedSettings(enabled = true))
        }
        val engine = startPlayer()
        main { engine.beginRadio(listOf(song("long1"), song("long2"), song("short1"))) }
        await("Radio didn't start") { engine.state.value.isPlaying }
        await("Reason not shown") { slowed!!.state.value.reason == "No Slowed lines yet" }
        assertFalse(slowed!!.state.value.canStart)
        main { slowed!!.startManual(null) }
        Thread.sleep(500)
        assertNull(engine.state.value.talk)
        assertFalse(slowed!!.state.value.running)
    }
}
