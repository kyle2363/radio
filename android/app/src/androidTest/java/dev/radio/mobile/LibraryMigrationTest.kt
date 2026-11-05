package dev.radio.mobile

import android.database.sqlite.SQLiteDatabase
import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.flow.first
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.util.UUID

class LibraryMigrationTest {
    @Test fun migrationPreservesExistingCatalogAndStartAttemptsAreIdempotent() = verifyMigration(1)
    @Test fun v2MigrationPreservesExistingListeningHistoryAndAddsBookmarkStorage() = verifyMigration(2)
    @Test fun v3MigrationAddsPlaylistsWithoutDroppingBookmarks() = verifyMigration(3)
    @Test fun v4MigrationPreservesPlaylistsAndAddsLoudnessCache() = verifyMigration(4)
    @Test fun v5MigrationPreservesLoudnessAndAddsPortableMetadataOnly() = verifyMigration(5)
    @Test fun v6MigrationAddsContentIdentityWithoutChangingPlayback() = verifyMigration(6)
    @Test fun v7MigrationAddsEmptyJournalWithoutReinterpretingHistory() = verifyMigration(7)
    @Test fun v8MigrationAddsSyncStorageAndKeepsPendingBytes() = verifyMigration(8)
    private fun verifyMigration(version: Int) = runBlocking {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val file = File(context.cacheDir, ".test-runtime/migrations/${UUID.randomUUID()}.db").also { it.parentFile!!.mkdirs() }
        val schema = JSONObject(instrumentation.context.assets.open("dev.radio.mobile.data.LibraryDatabase/$version.json").bufferedReader().use { it.readText() }).getJSONObject("database")
        SQLiteDatabase.openOrCreateDatabase(file, null).use { db ->
            val tables = schema.getJSONArray("entities")
            for (index in 0 until tables.length()) {
                val entity = tables.getJSONObject(index)
                db.execSQL(entity.getString("createSql").replace("\${TABLE_NAME}", entity.getString("tableName")))
                val indices = entity.optJSONArray("indices") ?: org.json.JSONArray()
                for (i in 0 until indices.length()) db.execSQL(indices.getJSONObject(i).getString("createSql").replace("\${TABLE_NAME}", entity.getString("tableName")))
            }
            db.execSQL("INSERT INTO library_root (`key`, uri, folderId, name, scannedAt) VALUES (1, 'content://fixture/tree', 'folder', 'Music', 1)")
            db.execSQL("INSERT INTO local_songs (id, rootUri, folderId, uri, fileName, folderPath, modifiedAt, firstSeenAt, byteSize, durationMs, available) VALUES ('keep', 'content://fixture/tree', 'folder', 'content://fixture/song', 'Keep.mp3', '', 1, 2, 3, 8000, 1)")
            if (version >= 2) db.execSQL("INSERT INTO local_playback_starts (attemptId, songId, occurredAt, positionMs) VALUES ('old-attempt', 'keep', 50, 1000)")
            if (version >= 3) db.execSQL("INSERT INTO local_playback_snapshot (`key`, payload) VALUES (1, 'old bookmark')")
            if (version >= 4) {
                db.execSQL("INSERT INTO local_playlists (id, name, nameKey, createdAt, updatedAt) VALUES ('pl', 'Keep playlist', 'keep playlist', 1, 1)")
                db.execSQL("INSERT INTO local_playlist_songs (playlistId, songId, position) VALUES ('pl', 'keep', 0)")
            }
            if (version >= 5) db.execSQL("INSERT INTO local_loudness (songId, uri, modifiedAt, byteSize, catalogScan, algorithm, integratedLufs, samplePeak, status, measuredAt) VALUES ('keep', 'content://fixture/song', 1, 3, 1, 'fixture', -12.0, 0.5, 'measured', 2)")
            if (version >= 8) {
                db.execSQL("INSERT INTO local_listening_events (eventId,localId,attemptId,kind,occurredAt,logicalClock,payload,listenedMs,completion,terminationReason,provenance) VALUES ('pending-event','keep',NULL,'favorite_changed',3,7,'{\"event_id\":\"pending-event\"}',0,NULL,NULL,'fixture')")
                db.execSQL("INSERT INTO local_event_outbox (eventId,serverId,deviceId,payload) VALUES ('pending-event','server','device','{\"event_id\":\"pending-event\"}')")
            }
            db.version = version
        }
        val db = Room.databaseBuilder(context, LibraryDatabase::class.java, file.absolutePath).addMigrations(LIBRARY_MIGRATION_1_2, LIBRARY_MIGRATION_2_3, LIBRARY_MIGRATION_3_4, LIBRARY_MIGRATION_4_5, LIBRARY_MIGRATION_5_6, LIBRARY_MIGRATION_6_7, LIBRARY_MIGRATION_7_8, LIBRARY_MIGRATION_8_9).build()
        try {
            assertEquals("Music", db.library().root()!!.name)
            assertEquals("keep", db.library().allSongs().single().id)
            val start = LocalPlaybackStart("attempt", "keep", 100, 0)
            db.library().savePlaybackStart(start); db.library().savePlaybackStart(start)
            val previous = if (version >= 2) listOf(LocalPlaybackStart("old-attempt", "keep", 50, 1000)) else emptyList()
            assertEquals(previous + start, db.library().playbackStarts())
            if (version >= 3) assertEquals("old bookmark", db.library().playbackSnapshot()!!.payload) else assertNull(db.library().playbackSnapshot())
            val bookmark = LocalPlaybackSnapshot(payload = "bookmark row")
            db.library().savePlaybackSnapshot(bookmark)
            assertEquals(bookmark, db.library().playbackSnapshot())
            if (version >= 4) assertEquals(listOf("keep"), db.playlists().songs("pl"))
            if (version >= 5) assertEquals(-12.0, db.loudness().observe().first().single().integratedLufs!!, 0.0)
            assertNull(db.catalogs().latest())
            if (version >= 8) assertEquals("pending-event", db.journal().events().single().eventId)
            else assertTrue(db.journal().events().isEmpty())
            if (version >= 8) {
                val saved = db.journal().outbox().single()
                assertEquals("pending-event", saved.eventId)
                assertEquals("{\"event_id\":\"pending-event\"}", saved.payload)
                assertFalse(saved.acknowledged)
            } else assertTrue(db.journal().outbox().isEmpty())
            assertNull(db.journal().syncCursor("server","device"))
        } finally { db.close(); context.deleteDatabase(file.absolutePath) }
        Unit
    }
}
