package dev.radio.mobile.data

import androidx.room.*
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.*
import java.util.Locale
import java.util.UUID

@Entity(tableName = "local_playlists", indices = [Index(value = ["nameKey"], unique = true)])
data class LocalPlaylist(@PrimaryKey val id: String, val name: String, val nameKey: String, val createdAt: Long, val updatedAt: Long)

@Entity(tableName = "local_playlist_songs", primaryKeys = ["playlistId", "songId"],
    foreignKeys = [ForeignKey(entity = LocalPlaylist::class, parentColumns = ["id"], childColumns = ["playlistId"], onDelete = ForeignKey.CASCADE)])
data class LocalPlaylistSong(val playlistId: String, val songId: String, val position: Int)

data class PlaylistWithSongs(@Embedded val playlist: LocalPlaylist,
    @Relation(parentColumn = "id", entityColumn = "playlistId") val entries: List<LocalPlaylistSong>)

data class PlaylistsState(val playlists: List<LocalPlaylist> = emptyList(), val entries: List<LocalPlaylistSong> = emptyList()) {
    fun songIds(id: String) = entries.filter { it.playlistId == id }.sortedBy { it.position }.map { it.songId }
}

@Dao
interface PlaylistDao {
    @Transaction @Query("SELECT * FROM local_playlists ORDER BY updatedAt DESC, name ASC") fun observe(): Flow<List<PlaylistWithSongs>>
    @Query("SELECT * FROM local_playlists WHERE id = :id") suspend fun playlist(id: String): LocalPlaylist?
    @Query("SELECT * FROM local_playlists WHERE nameKey = :key") suspend fun named(key: String): LocalPlaylist?
    @Query("SELECT songId FROM local_playlist_songs WHERE playlistId = :id ORDER BY position") suspend fun songs(id: String): List<String>
    @Upsert suspend fun save(playlist: LocalPlaylist)
    @Insert suspend fun insertEntries(entries: List<LocalPlaylistSong>)
    @Query("DELETE FROM local_playlist_songs WHERE playlistId = :id") suspend fun clearEntries(id: String)
    @Query("DELETE FROM local_playlists WHERE id = :id") suspend fun delete(id: String)
}

/** Playlist membership is app data. None of these operations writes to an audio URI. */
class PlaylistRepository(private val db: LibraryDatabase, scope: CoroutineScope) {
    private val dao = db.playlists()
    val state: StateFlow<PlaylistsState> = dao.observe().map { rows -> PlaylistsState(rows.map { it.playlist }, rows.flatMap { it.entries }) }
        .stateIn(scope, SharingStarted.Eagerly, PlaylistsState())

    private fun name(value: String): String = value.trim().also { require(it.isNotEmpty() && it.length <= 80) { "Use a playlist name between 1 and 80 characters." } }
    private suspend fun <T> attempt(block: suspend () -> T): Result<T> = try { Result.success(block()) }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (error: Exception) { Result.failure(error) }
    suspend fun create(value: String, songIds: List<String> = emptyList()): Result<String> = attempt {
        db.withTransaction {
            val title = name(value); val key = title.lowercase(Locale.ROOT)
            require(dao.named(key) == null) { "A playlist with that name already exists." }
            val now = System.currentTimeMillis(); val id = UUID.randomUUID().toString()
            dao.save(LocalPlaylist(id, title, key, now, now))
            replace(id, songIds.distinct())
            id
        }
    }
    suspend fun rename(id: String, value: String): Result<Unit> = attempt { db.withTransaction {
        val playlist = requireNotNull(dao.playlist(id)) { "Playlist no longer exists." }
        val title = name(value); val key = title.lowercase(Locale.ROOT)
        require(dao.named(key)?.id.let { it == null || it == id }) { "A playlist with that name already exists." }
        dao.save(playlist.copy(name = title, nameKey = key, updatedAt = System.currentTimeMillis()))
    } }
    suspend fun delete(id: String): Result<Unit> = attempt { dao.delete(id) }
    suspend fun add(id: String, songIds: List<String>): Result<Unit> = edit(id) { (it + songIds).distinct() }
    suspend fun remove(id: String, songId: String): Result<Unit> = edit(id) { it.filterNot { song -> song == songId } }
    suspend fun move(id: String, songId: String, target: Int): Result<Unit> = edit(id) { old ->
        old.toMutableList().apply { if (remove(songId)) add(target.coerceIn(0, size), songId) }
    }
    private suspend fun edit(id: String, change: (List<String>) -> List<String>): Result<Unit> = attempt { db.withTransaction {
        val playlist = requireNotNull(dao.playlist(id)) { "Playlist no longer exists." }
        replace(id, change(dao.songs(id)))
        dao.save(playlist.copy(updatedAt = System.currentTimeMillis()))
    } }
    private suspend fun replace(id: String, songIds: List<String>) {
        dao.clearEntries(id)
        dao.insertEntries(songIds.mapIndexed { index, songId -> LocalPlaylistSong(id, songId, index) })
    }
}
