package dev.radio.mobile.data

import androidx.room.*
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Entity(tableName = "local_playback_starts", indices = [Index(value = ["songId"])])
data class LocalPlaybackStart(@PrimaryKey val attemptId: String, val songId: String, val occurredAt: Long, val positionMs: Long)

@Entity(tableName = "local_playback_snapshot")
data class LocalPlaybackSnapshot(@PrimaryKey val key: Int = 1, val payload: String)

@Entity(tableName = "local_lyrics_cache", indices = [Index(value = ["serverId", "catalogId", "revision"])])
data class LocalLyricsCache(
    @PrimaryKey val cacheKey: String,
    val serverId: String,
    val catalogId: String,
    val manifestSha256: String,
    val revision: Long,
    val localSongId: String,
    val portableSongId: String,
    val localIdentity: String,
    val ownerGeneration: Long,
    val mappingGeneration: Long,
    val text: String,
    val textSha256: String,
    val fetchedAt: Long,
    val lastViewedAt: Long,
)

@Entity(tableName = "library_root")
data class LibraryRoot(@PrimaryKey val key: Int = 1, val uri: String, val folderId: String, val name: String, val scannedAt: Long)

@Entity(tableName = "local_folders", indices = [Index(value = ["rootUri", "parentId"])])
data class LocalFolder(@PrimaryKey val id: String, val rootUri: String, val parentId: String?, val name: String, val path: String, val modifiedAt: Long?, val available: Boolean = true)

@Entity(tableName = "local_songs", indices = [Index(value = ["uri"], unique = true), Index(value = ["rootUri", "folderId"])])
data class LocalSong(
    @PrimaryKey val id: String,
    val rootUri: String,
    val folderId: String,
    val uri: String,
    val fileName: String,
    val folderPath: String,
    val modifiedAt: Long?,
    val firstSeenAt: Long,
    val byteSize: Long?,
    val durationMs: Long?,
    val available: Boolean = true
) {
    /** Read from the file name ("Artist - Title.mp3"); parsed once per name. */
    @get:Ignore val title: String get() = dev.radio.mobile.music.SongNames.of(fileName).title
    /** Empty when the file name has no artist part. */
    @get:Ignore val artist: String get() = dev.radio.mobile.music.SongNames.of(fileName).artist
    @get:Ignore val seconds: Int get() = ((durationMs ?: 0L) / 1000).toInt()
}

@Dao
interface LibraryDao {
    @Query("SELECT * FROM library_root WHERE `key` = 1") suspend fun root(): LibraryRoot?
    @Query("SELECT * FROM local_songs WHERE rootUri = :uri") suspend fun songs(uri: String): List<LocalSong>
    @Query("SELECT * FROM local_folders WHERE rootUri = :uri") suspend fun folders(uri: String): List<LocalFolder>
    @Query("SELECT * FROM local_songs") suspend fun allSongs(): List<LocalSong>
    @Query("SELECT * FROM local_songs WHERE id = :id LIMIT 1") suspend fun song(id: String): LocalSong?
    @Query("SELECT id FROM local_songs") suspend fun allSongIds(): List<String>
    @Query("UPDATE local_songs SET available = 0 WHERE rootUri = :uri") suspend fun markSongsMissing(uri: String)
    @Query("UPDATE local_folders SET available = 0 WHERE rootUri = :uri") suspend fun markFoldersMissing(uri: String)
    @Upsert suspend fun saveRoot(root: LibraryRoot)
    @Upsert suspend fun saveSongs(songs: List<LocalSong>)
    @Upsert suspend fun saveFolders(folders: List<LocalFolder>)
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun savePlaybackStart(start: LocalPlaybackStart)
    @Query("SELECT * FROM local_playback_starts ORDER BY occurredAt") suspend fun playbackStarts(): List<LocalPlaybackStart>
    @Query("SELECT * FROM local_playback_starts WHERE attemptId=:attempt LIMIT 1") suspend fun playbackStart(attempt: String): LocalPlaybackStart?
    @Query("SELECT * FROM local_playback_snapshot WHERE `key` = 1") suspend fun playbackSnapshot(): LocalPlaybackSnapshot?
    /** Row 2 holds only the latest position, so a playing queue isn't rewritten whole every few seconds. */
    @Query("SELECT * FROM local_playback_snapshot WHERE `key` = 2") suspend fun playbackPosition(): LocalPlaybackSnapshot?
    @Upsert suspend fun savePlaybackSnapshot(snapshot: LocalPlaybackSnapshot)
    @Query("DELETE FROM local_playback_snapshot") suspend fun clearPlaybackSnapshot()
}

@Dao
interface LyricsCacheDao {
    @Query("SELECT * FROM local_lyrics_cache WHERE cacheKey=:key LIMIT 1") suspend fun get(key: String): LocalLyricsCache?
    @Query("SELECT * FROM local_lyrics_cache ORDER BY lastViewedAt ASC, fetchedAt ASC") suspend fun all(): List<LocalLyricsCache>
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun save(entry: LocalLyricsCache)
    @Query("UPDATE local_lyrics_cache SET lastViewedAt=:at WHERE cacheKey=:key") suspend fun viewed(key: String, at: Long)
    @Query("DELETE FROM local_lyrics_cache WHERE cacheKey=:key") suspend fun delete(key: String)
    @Query("DELETE FROM local_lyrics_cache WHERE serverId=:serverId") suspend fun deleteServer(serverId: String)
    @Query("DELETE FROM local_lyrics_cache WHERE serverId=:serverId AND (catalogId!=:catalogId OR revision!=:revision OR manifestSha256!=:manifestSha256)") suspend fun deleteObsolete(serverId: String, catalogId: String, revision: Long, manifestSha256: String)
}

val LIBRARY_MIGRATION_1_2 = object : Migration(1, 2) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE IF NOT EXISTS local_playback_starts (attemptId TEXT NOT NULL, songId TEXT NOT NULL, occurredAt INTEGER NOT NULL, positionMs INTEGER NOT NULL, PRIMARY KEY(attemptId))")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_local_playback_starts_songId ON local_playback_starts(songId)")
    }
}

val LIBRARY_MIGRATION_2_3 = object : Migration(2, 3) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE IF NOT EXISTS local_playback_snapshot (`key` INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(`key`))")
    }
}

val LIBRARY_MIGRATION_3_4 = object : Migration(3, 4) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE IF NOT EXISTS local_playlists (id TEXT NOT NULL, name TEXT NOT NULL, nameKey TEXT NOT NULL, createdAt INTEGER NOT NULL, updatedAt INTEGER NOT NULL, PRIMARY KEY(id))")
        db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS index_local_playlists_nameKey ON local_playlists (nameKey)")
        db.execSQL("CREATE TABLE IF NOT EXISTS local_playlist_songs (playlistId TEXT NOT NULL, songId TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(playlistId, songId), FOREIGN KEY(playlistId) REFERENCES local_playlists(id) ON UPDATE NO ACTION ON DELETE CASCADE)")
    }
}

val LIBRARY_MIGRATION_4_5 = object : Migration(4, 5) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE IF NOT EXISTS local_loudness (songId TEXT NOT NULL, uri TEXT NOT NULL, modifiedAt INTEGER, byteSize INTEGER, catalogScan INTEGER NOT NULL, algorithm TEXT NOT NULL, integratedLufs REAL, samplePeak REAL, status TEXT NOT NULL, measuredAt INTEGER NOT NULL, PRIMARY KEY(songId))")
    }
}

val LIBRARY_MIGRATION_5_6 = object : Migration(5, 6) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE IF NOT EXISTS portable_catalog_revisions (manifestSha256 TEXT NOT NULL, serverId TEXT NOT NULL, catalogId TEXT NOT NULL, revision INTEGER NOT NULL, manifest TEXT NOT NULL, songCount INTEGER NOT NULL, vectorCount INTEGER NOT NULL, importedAt INTEGER NOT NULL, PRIMARY KEY(serverId, manifestSha256))")
        db.execSQL("CREATE TABLE IF NOT EXISTS portable_catalog_songs (manifestSha256 TEXT NOT NULL, songId TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(manifestSha256, songId))")
        db.execSQL("CREATE TABLE IF NOT EXISTS portable_catalog_vectors (manifestSha256 TEXT NOT NULL, songId TEXT NOT NULL, space TEXT NOT NULL, model TEXT NOT NULL, modelVersion TEXT NOT NULL, dimension INTEGER NOT NULL, metric TEXT NOT NULL, normalization TEXT NOT NULL, dtype TEXT NOT NULL, byteLength INTEGER NOT NULL, blobSha256 TEXT NOT NULL, PRIMARY KEY(manifestSha256, songId, space))")
        db.execSQL("CREATE TABLE IF NOT EXISTS portable_catalog_active (serverId TEXT NOT NULL, catalogId TEXT NOT NULL, revision INTEGER NOT NULL, manifestSha256 TEXT NOT NULL, PRIMARY KEY(serverId))")
    }
}

val LIBRARY_MIGRATION_6_7 = object : Migration(6, 7) {
 override fun migrate(db: SupportSQLiteDatabase) {
  db.execSQL("CREATE TABLE IF NOT EXISTS local_content_hashes (songId TEXT NOT NULL, uri TEXT NOT NULL, byteSize INTEGER, modifiedAt INTEGER, sha256 TEXT NOT NULL, hashedAt INTEGER NOT NULL, PRIMARY KEY(songId))")
  db.execSQL("CREATE TABLE IF NOT EXISTS portable_match_decisions (serverId TEXT NOT NULL, catalogId TEXT NOT NULL, localId TEXT NOT NULL, contentSha256 TEXT NOT NULL, songId TEXT NOT NULL, action TEXT NOT NULL, decidedAt INTEGER NOT NULL, targetIdentity TEXT NOT NULL, PRIMARY KEY(serverId, catalogId, localId, contentSha256, songId))")
 }
}
val LIBRARY_MIGRATION_7_8 = object : Migration(7, 8) {
 override fun migrate(db: SupportSQLiteDatabase) {
  db.execSQL("CREATE TABLE IF NOT EXISTS local_listening_events (eventId TEXT NOT NULL, localId TEXT NOT NULL, attemptId TEXT, kind TEXT NOT NULL, occurredAt INTEGER NOT NULL, logicalClock INTEGER NOT NULL, payload TEXT NOT NULL, listenedMs INTEGER NOT NULL, completion REAL, terminationReason TEXT, provenance TEXT NOT NULL, PRIMARY KEY(eventId))")
  db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS index_local_listening_events_attemptId_kind ON local_listening_events(attemptId, kind)")
  db.execSQL("CREATE TABLE IF NOT EXISTS local_event_outbox (eventId TEXT NOT NULL, serverId TEXT, deviceId TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(eventId))")
  db.execSQL("CREATE TABLE IF NOT EXISTS local_journal_clock (`key` INTEGER NOT NULL, value INTEGER NOT NULL, PRIMARY KEY(`key`))")
  db.execSQL("CREATE TABLE IF NOT EXISTS local_song_feedback (localId TEXT NOT NULL, favorite INTEGER NOT NULL, disliked INTEGER NOT NULL, PRIMARY KEY(localId))")
 }
}
val LIBRARY_MIGRATION_8_9 = object : Migration(8, 9) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("ALTER TABLE local_event_outbox ADD COLUMN acknowledged INTEGER NOT NULL DEFAULT 0")
        db.execSQL("CREATE TABLE IF NOT EXISTS portable_sync_cursor (serverId TEXT NOT NULL, deviceId TEXT NOT NULL, cursor INTEGER NOT NULL, updatedAt INTEGER NOT NULL, lastSuccessAt INTEGER, PRIMARY KEY(serverId,deviceId))")
        db.execSQL("CREATE TABLE IF NOT EXISTS portable_remote_events (serverId TEXT NOT NULL, deviceId TEXT NOT NULL, eventId TEXT NOT NULL, logicalClock INTEGER NOT NULL, occurredAt INTEGER NOT NULL, kind TEXT NOT NULL, localId TEXT, payload TEXT NOT NULL, PRIMARY KEY(serverId,deviceId,eventId))")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_portable_remote_events_serverId_occurredAt ON portable_remote_events(serverId,occurredAt)")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_portable_remote_events_serverId_logicalClock ON portable_remote_events(serverId,logicalClock)")
        db.execSQL("CREATE TABLE IF NOT EXISTS portable_profile_registers (serverId TEXT NOT NULL, registerKey TEXT NOT NULL, logicalClock INTEGER NOT NULL, deviceId TEXT NOT NULL, eventId TEXT NOT NULL, kind TEXT NOT NULL, valueJson TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(serverId,registerKey))")
        db.execSQL("CREATE TABLE IF NOT EXISTS portable_sync_owner (ownerKey TEXT NOT NULL, serverId TEXT NOT NULL, deviceId TEXT NOT NULL, installationMarker TEXT NOT NULL, quarantined INTEGER NOT NULL, PRIMARY KEY(ownerKey))")
    }
}
val LIBRARY_MIGRATION_9_10 = object : Migration(9, 10) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE IF NOT EXISTS local_lyrics_cache (cacheKey TEXT NOT NULL, serverId TEXT NOT NULL, catalogId TEXT NOT NULL, manifestSha256 TEXT NOT NULL, revision INTEGER NOT NULL, localSongId TEXT NOT NULL, portableSongId TEXT NOT NULL, localIdentity TEXT NOT NULL, ownerGeneration INTEGER NOT NULL, mappingGeneration INTEGER NOT NULL, text TEXT NOT NULL, textSha256 TEXT NOT NULL, fetchedAt INTEGER NOT NULL, lastViewedAt INTEGER NOT NULL, PRIMARY KEY(cacheKey))")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_local_lyrics_cache_serverId_catalogId_revision ON local_lyrics_cache (serverId, catalogId, revision)")
    }
}
/** Checkpoint 7A: playlist sync flags and shared order keys; existing playlists stay local. */
val LIBRARY_MIGRATION_10_11 = object : Migration(10, 11) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("ALTER TABLE local_playlists ADD COLUMN synced INTEGER NOT NULL DEFAULT 0")
        db.execSQL("ALTER TABLE local_playlists ADD COLUMN remoteDeleted INTEGER NOT NULL DEFAULT 0")
        db.execSQL("ALTER TABLE local_playlist_songs ADD COLUMN positionKey TEXT")
    }
}
/** Checkpoint 8A: local-only play context per actual start, and journal indices for stats. */
val LIBRARY_MIGRATION_11_12 = object : Migration(11, 12) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE IF NOT EXISTS local_play_context (attemptId TEXT NOT NULL, localId TEXT NOT NULL, sourceKind TEXT NOT NULL, sourceId TEXT, stationOrigin TEXT, `trigger` TEXT NOT NULL, startedAt INTEGER NOT NULL, PRIMARY KEY(attemptId))")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_local_play_context_startedAt ON local_play_context (startedAt)")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_local_listening_events_kind_occurredAt ON local_listening_events (kind, occurredAt)")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_local_listening_events_localId_kind ON local_listening_events (localId, kind)")
    }
}
@Database(entities = [LibraryRoot::class, LocalSong::class, LocalFolder::class, LocalPlaybackStart::class, LocalPlaybackSnapshot::class, LocalPlaylist::class, LocalPlaylistSong::class, LocalLoudness::class, PortableCatalogRevision::class, PortableCatalogSong::class, PortableCatalogVector::class, PortableCatalogActive::class, LocalContentHash::class, PortableMatchDecision::class, LocalListeningEvent::class, LocalEventOutbox::class, LocalJournalClock::class, LocalSongFeedback::class, PortableSyncCursor::class, PortableRemoteEvent::class, PortableProfileRegister::class, PortableSyncOwner::class, LocalLyricsCache::class, LocalPlayContext::class, LocalFeel::class, LocalFeelEvidence::class, LocalSongFeelState::class], version = 13, exportSchema = true)
abstract class LibraryDatabase : RoomDatabase() { abstract fun library(): LibraryDao; abstract fun playlists(): PlaylistDao; abstract fun loudness(): LoudnessDao; abstract fun catalogs(): PortableCatalogDao; abstract fun matching(): MatchingDao; abstract fun journal(): ListeningJournalDao; abstract fun lyrics(): LyricsCacheDao; abstract fun feels(): FeelDao }
