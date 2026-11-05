package dev.radio.mobile

import androidx.room.Room
import androidx.room.withTransaction
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.playback.PlaybackState
import dev.radio.mobile.portable.*
import dev.radio.mobile.recommendation.RadioRepository
import dev.radio.mobile.recommendation.readSharedRadioProfile
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.util.Base64
import java.util.UUID

class RadioSyncLearningTest {
    @Test fun pendingEditsCompeteWithRemoteWinnersWhileUnmappedChoicesAndOtherCatalogsStaySeparate() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        val record = PortableCatalogRevision("hash", "server-a", "catalog-a", 1, "{}", 1, 0, 1)
        fun event(id: String, kind: String, clock: Long, value: Any, sid: String? = "1", catalog: String = record.catalogId, device: String = "phone") =
            JSONObject().put("protocol_version", 1).put("device_id", device).put("event_id", id).put("logical_clock", clock)
                .put("occurred_at", 100).put("kind", kind).put("song_id", sid ?: JSONObject.NULL).put("value", value)
                .apply { if (sid == null) put("local_id", "pre-match") else put("catalog_id", catalog) }.toString()
        suspend fun incoming(payload: String, server: String = record.serverId) {
            val wire = JSONObject(payload)
            db.journal().profileRegister(PortableProfileRegister(server, "${wire.getString("kind")}:${wire.optString("song_id")}",
                wire.getLong("logical_clock"), wire.getString("device_id"), wire.getString("event_id"), wire.getString("kind"), wire.get("value").toString(), payload))
        }
        try {
            incoming(event("remote-old", "favorite_changed", 20, false, device = "server"))
            val pending = event("pending", "favorite_changed", 21, true)
            db.journal().insert(LocalListeningEvent("pending", "matched", null, "favorite_changed", 100000, 21, pending, 0, null, null, "confirmed"))
            db.journal().enqueue(LocalEventOutbox("pending", record.serverId, "phone", pending))
            assertEquals(true, readSharedRadioProfile(db, record).favorites["1"])
            incoming(event("remote-new", "favorite_changed", 22, false, device = "server"))
            assertEquals(false, readSharedRadioProfile(db, record).favorites["1"])
            assertEquals(pending, db.journal().outbox().single().payload)
            assertEquals(21L, db.journal().events().single().logicalClock)
            val localOnly = event("before-match", "dislike_changed", 1, true, sid = null)
            db.journal().insert(LocalListeningEvent("before-match", "pre-match", null, "dislike_changed", 100000, 1, localOnly, 0, null, null, "local_only"))
            db.journal().enqueue(LocalEventOutbox("before-match", null, "local:installation", localOnly))
            incoming(event("shared-clear", "dislike_changed", 25, false, device = "server"))
            incoming(event("wrong-catalog", "blacklist_changed", 99, true, catalog = "catalog-b", device = "server"))
            incoming(event("wrong-server", "energy_changed", 100, .9, device = "server"), "server-b")
            val shared = readSharedRadioProfile(db, record)
            assertEquals(setOf("pre-match"), shared.localDislikes)
            assertEquals(false, shared.dislikes["1"])
            assertTrue(shared.blacklist.isEmpty())
            assertTrue(shared.energy.isEmpty())
        } finally { db.close() }
    }

    @Test fun sharedLearningRefreshesFuturePicksWithoutChangingLiveContextAndDefaultsApplyAfterRestart() = runBlocking {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val prefs = context.getSharedPreferences("radio_recommendations", 0)
        prefs.edit().clear().commit()
        val db = Room.inMemoryDatabaseBuilder(context, LibraryDatabase::class.java).build()
        var scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val directory = File(context.cacheDir, ".test-runtime/sync-radio/${UUID.randomUUID()}").apply { mkdirs() }
        val songs = listOf("a", "b").map { LocalSong("sync-$it", "root", "folder", "content://fixture/$it", "Song $it.mp3", "Music", 1, 1, 1, 10000) }
        val local = MutableStateFlow(LibraryState(songs = songs, loading = false))
        try {
            val cases = JSONObject(instrumentation.context.assets.open("mobile_catalog_golden.json").bufferedReader().use { it.readText() }).getJSONArray("cases")
            val fixture = (0 until cases.length()).map { cases.getJSONObject(it) }.first { it.getString("name") == "full_unicode_numeric" }
            val raw = fixture.getString("manifest_utf8")
            val manifest = CatalogValidator.manifest(raw.toByteArray())
            val record = PortableCatalogRevision(manifest.sha256, "sync-server", manifest.catalogId, manifest.revision, raw, 2, 3, 1)
            db.catalogs().saveRevision(record)
            db.catalogs().activate(PortableCatalogActive(record.serverId, record.catalogId, record.revision, record.manifestSha256))
            fixture.getJSONObject("blobs").let { blobs -> blobs.keys().forEach { File(directory, "$it.bin").writeBytes(Base64.getDecoder().decode(blobs.getString(it))) } }
            var journal = ListeningJournalRepository(context, db, scope)
            withTimeout(10000) { JournalIdentityProvider.mappings.first { it.loaded } }
            fun publishMappings() {
                val generation = JournalIdentityProvider.invalidateMappings()
                JournalIdentityProvider.publishMappings(generation, songs.zip(listOf("a", "b")).map { (song, id) ->
                    JournalIdentityProvider.Entry(song, record.serverId, record.catalogId, record.revision, id, "confirmed:${record.manifestSha256}:hash")
                })
            }
            publishMappings()
            var radio = RadioRepository(context, db, local, journal, scope, directory)
            radio.setMood("focus")
            radio.setExploration(.25)
            val playback = PlaybackState(queue = songs, playOrder = songs.map { it.id }, currentId = songs[0].id, queueRevision = 41, radioActive = true)
            radio.updatePlayback(playback)
            withTimeout(10000) { radio.state.first { it.loaded && !it.busy } }
            val proof = radio.recommendations()!!
            val before = radio.state.value
            val at = System.currentTimeMillis()
            suspend fun register(kind: String, value: Any, sid: String?, clock: Long, key: String? = null, server: String = record.serverId) {
                val event = JSONObject().put("protocol_version", 1).put("device_id", "server").put("event_id", "remote-$clock")
                    .put("logical_clock", clock).put("occurred_at", at / 1000).put("kind", kind).put("song_id", sid ?: JSONObject.NULL).put("value", value)
                if (sid != null) event.put("catalog_id", record.catalogId)
                if (key != null) event.put("key", key)
                db.journal().profileRegister(PortableProfileRegister(server, if (key != null) "preference:$key" else "$kind:$sid", clock, "server", "remote-$clock", kind, event.get("value").toString(), event.toString()))
            }
            db.withTransaction {
                register("favorite_changed", true, "a", 10)
                register("blacklist_changed", true, "b", 11)
                register("energy_changed", .9, "a", 12)
                register("preference_changed", .8, null, 13, "exploration_level")
                // An unrelated server with identical song IDs must have no effect.
                register("blacklist_changed", true, "a", 100, server = "other-server")
                repeat(3) { index ->
                    val event = JSONObject().put("protocol_version", 1).put("device_id", "other-phone").put("event_id", "skip-$index")
                        .put("logical_clock", 20 + index).put("occurred_at", at / 1000).put("kind", "song_skipped")
                        .put("song_id", "a").put("catalog_id", record.catalogId).put("attempt_id", "foreign-$index").put("completion", .1).put("reason", "too_intense")
                    db.journal().remoteEvent(PortableRemoteEvent(record.serverId, "other-phone", "skip-$index", (20 + index).toLong(), at, "song_skipped", null, event.toString()))
                }
                db.journal().cursor(PortableSyncCursor(record.serverId, "phone", 25, at))
            }
            assertNull("A newly blacklisted seed cannot bypass the scorer before preview refresh", radio.startRecommendations(songs[1].id))
            withTimeout(10000) { radio.state.first { it.generation > before.generation && !it.busy && it.diagnostics.eligibleTracks == 1 } }
            assertFalse("A shared-profile update invalidates a pending refill", radio.valid(proof.token))
            assertEquals("focus", radio.state.value.mood)
            assertEquals(.25, radio.state.value.exploration, 1e-6)
            assertEquals(playback.currentId, radio.token().current)
            assertEquals(playback.playOrder, radio.token().playOrder)
            assertEquals(playback.queueRevision, radio.token().queueRevision)
            assertEquals(listOf(songs[0].id), radio.state.value.suggestions.map { it.song.id })
            assertTrue(radio.isDisliked(songs[1].id))
            assertNull(radio.startRecommendations(songs[1].id))
            val baseEnergy = JSONObject(manifest.songs.getValue("a")).optDouble("energy", .5)
            assertEquals(baseEnergy * .7 + .9 * .3, radio.state.value.suggestions.single().pick.energy, 1e-6)
            assertTrue(db.journal().events().isEmpty())
            assertNull(db.library().playbackSnapshot())
            scope.coroutineContext[Job]!!.cancelAndJoin()
            scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            journal = ListeningJournalRepository(context, db, scope)
            withTimeout(10000) { JournalIdentityProvider.mappings.first { it.loaded } }
            publishMappings()
            radio = RadioRepository(context, db, local, journal, scope, directory)
            withTimeout(10000) { radio.state.first { it.loaded && !it.busy } }
            assertEquals(.8, radio.state.value.exploration, 1e-6)
            assertEquals("focus", radio.state.value.mood)
        } finally {
            scope.coroutineContext[Job]!!.cancelAndJoin()
            db.close()
            directory.deleteRecursively()
            prefs.edit().clear().commit()
        }
    }
}
