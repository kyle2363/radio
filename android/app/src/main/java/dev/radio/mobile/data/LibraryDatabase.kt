package dev.radio.mobile.data

import androidx.room.*
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Entity(tableName = "local_playback_starts", indices = [Index(value = ["songId"])])
data class LocalPlaybackStart(@PrimaryKey val attemptId: String, val songId: String, val occurredAt: Long, val positionMs: Long)

@Entity(tableName = "local_playback_snapshot")
data class LocalPlaybackSnapshot(@PrimaryKey val key: Int = 1, val payload: String)

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
    @get:Ignore val title: String get() = fileName.replace(Regex("(?i)\\.mp3$"), "")
    @get:Ignore val artist: String get() = "Local MP3"
    @get:Ignore val seconds: Int get() = ((durationMs ?: 0L) / 1000).toInt()
}

@Dao
interface LibraryDao {
    @Query("SELECT * FROM library_root WHERE `key` = 1") suspend fun root(): LibraryRoot?
    @Query("SELECT * FROM local_songs WHERE rootUri = :uri") suspend fun songs(uri: String): List<LocalSong>
    @Query("SELECT * FROM local_folders WHERE rootUri = :uri") suspend fun folders(uri: String): List<LocalFolder>
    @Query("SELECT * FROM local_songs") suspend fun allSongs(): List<LocalSong>
    @Query("UPDATE local_songs SET available = 0 WHERE rootUri = :uri") suspend fun markSongsMissing(uri: String)
    @Query("UPDATE local_folders SET available = 0 WHERE rootUri = :uri") suspend fun markFoldersMissing(uri: String)
    @Upsert suspend fun saveRoot(root: LibraryRoot)
    @Upsert suspend fun saveSongs(songs: List<LocalSong>)
    @Upsert suspend fun saveFolders(folders: List<LocalFolder>)
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun savePlaybackStart(start: LocalPlaybackStart)
    @Query("SELECT * FROM local_playback_starts ORDER BY occurredAt") suspend fun playbackStarts(): List<LocalPlaybackStart>
    @Query("SELECT * FROM local_playback_snapshot WHERE `key` = 1") suspend fun playbackSnapshot(): LocalPlaybackSnapshot?
    @Upsert suspend fun savePlaybackSnapshot(snapshot: LocalPlaybackSnapshot)
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

@Database(entities = [LibraryRoot::class, LocalSong::class, LocalFolder::class, LocalPlaybackStart::class, LocalPlaybackSnapshot::class], version = 3, exportSchema = true)
abstract class LibraryDatabase : RoomDatabase() { abstract fun library(): LibraryDao }
