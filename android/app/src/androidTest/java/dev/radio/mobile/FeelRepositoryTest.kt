package dev.radio.mobile

import androidx.room.Room
import androidx.room.withTransaction
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import kotlinx.coroutines.*
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/** Checkpoint 9B: feel definitions, evidence, score cache, Energy sharing and remote merge. */
class FeelRepositoryTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private lateinit var db: LibraryDatabase
    private lateinit var scope: CoroutineScope
    private val energy = mutableListOf<Pair<String, Double>>()
    private val tracks = List(3) { LocalSong("song$it", "tree", "folder", "content://fixture/$it", "$it.mp3", "", 1, 1, 1, 60000) }
    private fun repo() = FeelRepository(db, scope, identityOf = { JournalIdentity("device", localId = it.id) },
        emitEnergy = { song, value -> energy += song.id to value })

    @Before fun open() = runBlocking {
        db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        db.library().saveSongs(tracks)
    }
    @After fun close() = runBlocking { scope.coroutineContext[Job]?.cancelAndJoin(); db.close() }

    @Test fun setUpKeepsTickedStartersAndAlwaysEnergy() = runBlocking {
        val repo = repo()
        repo.setUp(setOf("hype", "wind-down")).getOrThrow()
        repo.setUp(setOf("hype")).getOrThrow() // idempotent
        val names = db.feels().feels().sortedBy { it.sortOrder }.map { it.name }
        assertEquals(listOf("Energy", "Hype", "Wind down"), names)
        assertTrue(db.feels().feel(FeelCatalog.ENERGY_FEEL_ID)!!.scale)
        assertTrue(repo.edit(FeelCatalog.ENERGY_FEEL_ID, archived = true).isFailure)
    }

    @Test fun taggingBuildsMembershipAndNeverTouchesHistory() = runBlocking {
        val repo = repo()
        repo.setUp(setOf("hype")).getOrThrow()
        val hype = db.feels().feels().first { it.name == "Hype" }.feelId
        repo.tag(hype, tracks[0], true).getOrThrow()
        repo.tag(hype, tracks[1], false).getOrThrow()
        val states = db.feels().states(hype).associateBy { it.songId }
        assertTrue(states.getValue("song0").member)
        assertFalse(states.getValue("song1").member)
        assertFalse("song2" in states)
        assertTrue(db.library().playbackStarts().isEmpty())
        assertTrue(db.journal().events().isEmpty())
    }

    @Test fun energyBucketsShareEnergyOnlyWhenItMoves() = runBlocking {
        val repo = repo()
        repo.rate(FeelCatalog.ENERGY_FEEL_ID, tracks[0], 0.875).getOrThrow()
        assertEquals("song0", energy.single().first)
        assertTrue(energy.single().second > 0.6)
        energy.clear()
        repo.recompute(FeelCatalog.ENERGY_FEEL_ID)
        assertTrue("an unchanged score is not re-sent", energy.isEmpty())
    }

    @Test fun unpairedEditsStayOutOfTheServerOutbox() = runBlocking {
        val repo = repo()
        repo.setUp(setOf("hype")).getOrThrow()
        repo.tag(db.feels().feels().first { it.name == "Hype" }.feelId, tracks[0], true).getOrThrow()
        assertTrue(db.journal().outbox().none { it.serverId != null })
    }

    @Test fun remoteEvidenceMergesOnceAndDefinitionsAreLastWriterWins() = runBlocking {
        val repo = repo()
        val feel = "3b9e4a7c-1f2d-4c5b-8a69-0e1d2c3b4a59"
        fun remote(id: String, clock: Long, kind: String, body: JSONObject) = PortableRemoteEvent("server", "desk", id, clock, 1_000L,
            kind, body.optString("local_id").ifEmpty { null }, body.put("feel_id", feel).put("kind", kind).toString())
        val define = { id: String, clock: Long, name: String -> remote(id, clock, "feel_defined",
            JSONObject().put("value", JSONObject().put("name", name).put("color", "#5EEAD4").put("scale", false).put("archived", false))) }
        val rated = remote("r1", 3, "feel_rated", JSONObject().put("local_id", "x").put("value", 1.0))
        assertEquals(setOf(feel), db.withTransactionCompat { repo.applyRemote(listOf(define("d2", 2, "Newer"), define("d1", 1, "Older"), rated)) })
        assertEquals("Newer", db.feels().feel(feel)!!.name)
        assertTrue(db.withTransactionCompat { repo.applyRemote(listOf(rated)) }.isEmpty())
        assertEquals(1, db.feels().evidence(feel).size)
    }
}

private suspend fun <T> LibraryDatabase.withTransactionCompat(block: suspend () -> T): T = withTransaction { block() }
