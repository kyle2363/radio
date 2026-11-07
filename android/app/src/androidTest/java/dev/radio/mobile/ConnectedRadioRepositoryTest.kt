package dev.radio.mobile

import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.playback.PlaybackState
import dev.radio.mobile.recommendation.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class ConnectedRadioRepositoryTest {
    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private lateinit var db: LibraryDatabase
    private lateinit var scope: CoroutineScope
    private lateinit var radio: RadioRepository
    private lateinit var link: ScriptedConnectedLink
    private val songs = (0..11).map { LocalSong("fresh-$it", "root", "folder", "content://fixture/$it", "Song $it.mp3", "Music", 1, 1, 1, 180000) }
    private fun pb(current: Int, upcoming: List<Int>) =
        (listOf(current) + upcoming).map { songs[it] }.let { PlaybackState(queue = it, playOrder = it.map { s -> s.id }, currentId = songs[current].id, radioActive = true, playWhenReady = true) }
    private fun ids(batch: RadioBatch?) = batch!!.suggestions.map { it.song.id }

    @Before fun setup() {
        context.getSharedPreferences("radio_recommendations", 0).edit().clear().commit()
        db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        link = ScriptedConnectedLink(ScriptedConnectedLink.serverIds(12))
        val journal = ListeningJournalRepository(context, db, scope)
        radio = RadioRepository(context, db, MutableStateFlow(LibraryState(songs = songs, loading = false)), journal, scope, connectedLink = { link })
        runBlocking { withTimeout(10000) { radio.state.first { it.loaded && !it.busy } } }
        ScriptedConnectedLink.publishMappings(songs)
    }
    @After fun cleanup() {
        runBlocking { scope.coroutineContext[Job]!!.cancelAndJoin() }
        db.close()
        context.getSharedPreferences("radio_recommendations", 0).edit().clear().commit()
    }

    private fun start(current: Int = 0, upcoming: List<Int> = listOf(1, 2)): PlaybackState {
        radio.setConnectedEnabled(true)
        val state = pb(current, upcoming)
        radio.updatePlayback(state)
        radio.songStarted(songs[current], state)
        runBlocking { withTimeout(10000) { radio.connected.set.first { it != null } } }
        return state
    }

    @Test fun connectedRadioIsOffByDefault() {
        assertFalse(radio.connectedEnabled)
        radio.songStarted(songs[0], pb(0, listOf(1, 2)))
        assertTrue(link.posts.isEmpty())
        assertNull(radio.connected.set.value)
    }

    @Test fun setReadyGivesSimilarPicksFirstThenOfflineTopsUpWithinTheBound() = runBlocking {
        val state = start()
        val set = radio.connected.set.value!!
        assertEquals(2, set.continuation.size); assertEquals(3, set.options.size)
        val batch = radio.refill(setOf(songs[0].id, songs[1].id, songs[2].id), 3, songs[2].id)
        val picked = ids(batch)
        assertEquals(3, picked.size)
        assertEquals(set.continuation.map { it.song.id }, picked.take(2))
        assertTrue(batch!!.suggestions.take(2).all { it.pick.reason.startsWith("Server AI") })
        assertFalse(batch.suggestions[2].pick.reason.startsWith("Server AI"))
        assertEquals(picked.distinct(), picked)
        assertTrue(picked.none { it in setOf(songs[0].id, songs[1].id, songs[2].id) })
        // Option slots are never queued by the connected path (only the offline top-up may coincide with them).
        assertTrue(picked.take(2).none { id -> set.options.any { it.song.id == id } })
        assertTrue(radio.valid(batch.token))
        assertEquals(state.currentId, set.anchorLocalId)
    }

    @Test fun boundCapsSimilarPicksToTheRoomLeft() = runBlocking {
        start(upcoming = listOf(1, 2))
        val oneRoom = radio.refill(setOf(songs[0].id, songs[1].id, songs[2].id), 1, songs[2].id)
        assertEquals(1, ids(oneRoom).size); assertTrue(oneRoom!!.suggestions.single().pick.reason.startsWith("Server AI"))
        val twoRoom = radio.refill(setOf(songs[0].id, songs[1].id, songs[2].id), 2, songs[2].id)
        assertEquals(2, ids(twoRoom).size); assertTrue(twoRoom!!.suggestions.all { it.pick.reason.startsWith("Server AI") })
    }

    @Test fun committedSimilarPicksGetTheServerAiMarker() = runBlocking {
        start()
        val batch = radio.refill(setOf(songs[0].id, songs[1].id, songs[2].id), 3, songs[2].id)!!
        radio.committed(batch, batch.suggestions.map { it.song.id }.toSet())
        withTimeout(10000) { radio.connectedState.first { it.serverAiIds.isNotEmpty() } }
        val marked = radio.connectedState.value.serverAiIds
        assertEquals(batch.suggestions.take(2).map { it.song.id }.toSet(), marked)
    }

    @Test fun turningItOffFallsBackToTheOfflineRecommender() = runBlocking {
        start()
        radio.setConnectedEnabled(false)
        assertNull(radio.connected.set.value); assertTrue(radio.connectedState.value.serverAiIds.isEmpty())
        val batch = radio.refill(setOf(songs[0].id, songs[1].id, songs[2].id), 3, songs[2].id)
        assertTrue(batch!!.suggestions.none { it.pick.reason.startsWith("Server AI") })
        assertFalse(radio.connectedState.value.status.enabled)
    }

    @Test fun moodChangeDiscardsTheHeldSet() = runBlocking {
        start()
        radio.setMood("chill")
        assertNull(radio.connected.set.value)
        assertTrue(radio.refill(setOf(songs[0].id, songs[1].id, songs[2].id), 3, songs[2].id)!!.suggestions.none { it.pick.reason.startsWith("Server AI") })
    }

    @Test fun explorationChangeDiscardsTheHeldSet() = runBlocking {
        start()
        radio.setExploration(.9)
        assertNull(radio.connected.set.value)
    }

    @Test fun skippingToAnotherSongExpiresTheSetAndTheCardSourceImmediately() = runBlocking {
        start()
        radio.updatePlayback(pb(1, listOf(2, 3)))
        assertNull(radio.connected.set.value)
    }

    @Test fun mappingChangeDiscardsTheHeldSet() = runBlocking {
        start()
        JournalIdentityProvider.invalidateMappings()
        radio.refresh()
        assertNull(radio.connected.set.value)
    }

    @Test fun lateAnswerForASkippedSongIsDroppedNeverMerged() = runBlocking {
        link.gate = CompletableDeferred()
        radio.setConnectedEnabled(true)
        val first = pb(0, listOf(1, 2))
        radio.updatePlayback(first); radio.songStarted(songs[0], first)
        assertEquals(songs[0].id, radio.connected.status.value.pendingAnchor)
        val second = pb(1, listOf(2, 3))
        radio.updatePlayback(second); radio.songStarted(songs[1], second)
        assertEquals(songs[1].id, radio.connected.status.value.pendingAnchor)
        link.gate!!.complete(Unit)
        withTimeout(10000) { radio.connected.set.first { it != null } }
        assertEquals(songs[1].id, radio.connected.set.value!!.anchorLocalId)
        assertNull(radio.connected.status.value.pendingAnchor)
    }

    @Test fun pendingIsVisibleWhileTheDesktopThinksAndClearsWhenItFails() = runBlocking {
        link.gate = CompletableDeferred()
        radio.setConnectedEnabled(true)
        val state = pb(0, listOf(1, 2))
        radio.updatePlayback(state); radio.songStarted(songs[0], state)
        assertEquals(songs[0].id, radio.connected.status.value.pendingAnchor)
        assertNull(radio.connected.set.value)
        link.gate!!.completeExceptionally(dev.radio.mobile.connection.ConnectionFailure("Cannot reach the server."))
        withTimeout(10000) { radio.connected.status.first { it.pendingAnchor == null && it.lastFailure != null } }
        assertEquals(ConnectedFailureKind.UNREACHABLE, radio.connected.status.value.lastFailure!!.kind)
        // Failure means exactly the 2B behavior.
        assertTrue(radio.refill(setOf(songs[0].id, songs[1].id, songs[2].id), 3, songs[2].id)!!.suggestions.none { it.pick.reason.startsWith("Server AI") })
    }

    @Test fun deniedPermissionSendsNothingAndIsReported() = runBlocking {
        link.permitted = false
        radio.setConnectedEnabled(true)
        val state = pb(0, listOf(1, 2))
        radio.updatePlayback(state); radio.songStarted(songs[0], state)
        withTimeout(10000) { radio.connected.status.first { it.lastFailure != null } }
        assertEquals("Not permitted on desktop", radio.connected.status.value.lastMessage)
        assertEquals(false, radio.connected.status.value.permitted)
        assertTrue(link.posts.isEmpty())
    }

    @Test fun manualAndNonRadioPlaybackNeverRequestConnectedPicks() {
        radio.setConnectedEnabled(true)
        val manual = pb(0, listOf(1, 2)).copy(radioActive = false)
        radio.songStarted(songs[0], manual)
        assertTrue(link.posts.isEmpty()); assertNull(radio.connected.status.value.pendingAnchor)
    }

    @Test fun enablingMidSongPreparesASetForTheCurrentSong() {
        val state = pb(0, listOf(1, 2))
        radio.updatePlayback(state)
        radio.setConnectedEnabled(true)
        runBlocking { withTimeout(10000) { radio.connected.set.first { it != null } } }
        assertEquals(songs[0].id, radio.connected.set.value!!.anchorLocalId)
    }

    @Test fun toggleIsRememberedAcrossRepositoryInstances() {
        radio.setConnectedEnabled(true)
        assertTrue(context.getSharedPreferences("radio_recommendations", 0).getBoolean("connected_radio", false))
        radio.setConnectedEnabled(false)
        assertFalse(context.getSharedPreferences("radio_recommendations", 0).getBoolean("connected_radio", true))
    }

    @Test fun availabilityIsUploadedOnceAndRecentOutcomesComeFromTheJournalOnly() {
        start()
        assertEquals(1, link.count("/availability"))
        val request = ConnectedJson.parse(link.posts.last().second) as Map<*, *>
        assertEquals("s0", request["anchor_song_id"])
        assertTrue(db.let { runBlocking { it.journal().events().isEmpty() } }) // Connected requests write no history.
    }
    // --- checkpoint 4.1 tuning ---------------------------------------------------------------

    private fun lastContext() = ConnectedJson.parse(link.posts.last { it.first.startsWith("/contexts") && !it.first.endsWith("/recommendations") }.second) as Map<*, *>

    @Test fun tuningIsSavedAndSentWithTheNextSong() {
        link.tunable = true
        radio.setConnectedTuning(ConnectedTuning(picks = 7, similar = 2, energy = "auto"))
        assertEquals(ConnectedTuning(picks = 7, similar = 2, energy = "auto"),
            ConnectedTuning.decode(context.getSharedPreferences("radio_recommendations", 0).getString("connected_tuning", null)))
        start()
        assertEquals(mapOf("picks" to 7L, "similar" to 2L, "energy" to "auto"), lastContext()["tuning"])
    }

    @Test fun olderDesktopsGetNoTuning() {
        radio.setConnectedTuning(ConnectedTuning(picks = 7))
        start()
        assertFalse(lastContext().containsKey("tuning"))
        assertFalse(lastContext().containsKey("played"))
        assertEquals(false, radio.connected.status.value.tunable)
    }

    @Test fun aTuningChangeExpiresTheHeldSetButNotPlayback() = runBlocking {
        val state = start()
        radio.setConnectedTuning(ConnectedTuning(similar = 3))
        assertNull(radio.connected.set.value)
        assertEquals(state.currentId, songs[0].id)
        val batch = radio.refill(state.playOrder.toSet(), 3, state.playOrder.last())
        assertTrue(batch!!.suggestions.none { it.pick.reason.startsWith(ConnectedPolicy.SERVER_AI_PREFIX) })
    }

    @Test fun defaultsClearTheOverride() {
        radio.setConnectedTuning(ConnectedTuning(variety = .5))
        radio.setConnectedTuning(ConnectedTuning())
        assertTrue(radio.connectedTuning.value.isDefault)
        assertEquals("{}", context.getSharedPreferences("radio_recommendations", 0).getString("connected_tuning", null))
    }
}
