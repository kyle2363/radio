package dev.radio.mobile.data

import androidx.room.*
import dev.radio.mobile.portable.PlaylistSyncRules
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.*
import org.json.JSONObject
import java.util.Locale
import java.util.UUID

/**
 * [synced] playlists share edits with the paired desktop through profile sync (checkpoint 7A);
 * the playlist id is the shared playlist id. [remoteDeleted] marks one deleted elsewhere, kept
 * until the listener chooses to remove or keep it.
 */
@Entity(tableName = "local_playlists", indices = [Index(value = ["nameKey"], unique = true)])
data class LocalPlaylist(@PrimaryKey val id: String, val name: String, val nameKey: String, val createdAt: Long, val updatedAt: Long,
    @ColumnInfo(defaultValue = "0") val synced: Boolean = false, @ColumnInfo(defaultValue = "0") val remoteDeleted: Boolean = false)

/** [positionKey] is the shared fractional order key; null for playlists that are not synced. */
@Entity(tableName = "local_playlist_songs", primaryKeys = ["playlistId", "songId"],
    foreignKeys = [ForeignKey(entity = LocalPlaylist::class, parentColumns = ["id"], childColumns = ["playlistId"], onDelete = ForeignKey.CASCADE)])
data class LocalPlaylistSong(val playlistId: String, val songId: String, val position: Int, val positionKey: String? = null)

data class PlaylistWithSongs(@Embedded val playlist: LocalPlaylist,
    @Relation(parentColumn = "id", entityColumn = "playlistId") val entries: List<LocalPlaylistSong>)

/** A shared playlist on the paired server that this phone has not added. */
data class RemotePlaylist(val id: String, val name: String, val songCount: Int)

data class PlaylistsState(val playlists: List<LocalPlaylist> = emptyList(), val entries: List<LocalPlaylistSong> = emptyList(),
    val remote: List<RemotePlaylist> = emptyList(), val paired: Boolean = false) {
    fun songIds(id: String) = entries.filter { it.playlistId == id }.sortedBy { it.position }.map { it.songId }
}

@Dao
interface PlaylistDao {
    @Transaction @Query("SELECT * FROM local_playlists ORDER BY updatedAt DESC, name ASC") fun observe(): Flow<List<PlaylistWithSongs>>
    @Query("SELECT * FROM local_playlists WHERE id = :id") suspend fun playlist(id: String): LocalPlaylist?
    @Query("SELECT * FROM local_playlists WHERE synced = 1") suspend fun syncedPlaylists(): List<LocalPlaylist>
    @Query("SELECT * FROM local_playlists WHERE nameKey = :key") suspend fun named(key: String): LocalPlaylist?
    @Query("SELECT songId FROM local_playlist_songs WHERE playlistId = :id ORDER BY position") suspend fun songs(id: String): List<String>
    @Query("SELECT * FROM local_playlist_songs WHERE playlistId = :id ORDER BY position") suspend fun entries(id: String): List<LocalPlaylistSong>
    @Upsert suspend fun save(playlist: LocalPlaylist)
    @Insert suspend fun insertEntries(entries: List<LocalPlaylistSong>)
    @Query("DELETE FROM local_playlist_songs WHERE playlistId = :id") suspend fun clearEntries(id: String)
    @Query("DELETE FROM local_playlists WHERE id = :id") suspend fun delete(id: String)
}

/** Playlist membership is app data. None of these operations writes to an audio URI. */
@OptIn(ExperimentalCoroutinesApi::class)
class PlaylistRepository(private val db: LibraryDatabase, scope: CoroutineScope) {
    private val dao = db.playlists()
    private val remoteRegisters: Flow<Pair<Boolean, Map<String, String>>> = JournalIdentityProvider.ownerStatus
        .flatMapLatest { owner ->
            val server = owner.serverId
            if (!owner.paired || server == null) flowOf(false to emptyMap())
            else db.journal().observeProfileRegisters(server).map { rows ->
                true to rows.filter { it.registerKey.startsWith("playlist_") }.associate { it.registerKey to it.valueJson }
            }
        }.catch { emit(false to emptyMap()) }
    val state: StateFlow<PlaylistsState> = combine(dao.observe(), remoteRegisters) { rows, (paired, registers) ->
        val local = rows.map { it.playlist.id }.toSet()
        val remote = PlaylistSyncRules.playlistIds(registers.keys).filter { it !in local }.mapNotNull { id ->
            val shared = PlaylistSyncRules.state(id, registers)
            if (shared.deleted || shared.name == null) null else RemotePlaylist(id, shared.name, shared.members.size)
        }.sortedBy { it.name.lowercase(Locale.ROOT) }
        PlaylistsState(rows.map { it.playlist }, rows.flatMap { it.entries }, remote, paired)
    }.stateIn(scope, SharingStarted.Eagerly, PlaylistsState())

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
    /**
     * Save an imported M3U as a new playlist, adding " (2)" if the name is taken. When the phone is
     * paired it is shared at once, so the desktop gets it as a collection on the next profile sync.
     */
    suspend fun import(value: String, songIds: List<String>): Result<String> = attempt {
        val id = db.withTransaction {
            val title = uniqueName(name(value), "")
            val now = System.currentTimeMillis(); val id = UUID.randomUUID().toString()
            dao.save(LocalPlaylist(id, title, title.lowercase(Locale.ROOT), now, now))
            replace(id, songIds.distinct())
            id
        }
        if (shareIdentity() != null) share(id).getOrThrow()
        id
    }
    suspend fun rename(id: String, value: String): Result<Unit> = attempt { db.withTransaction {
        val playlist = requireNotNull(dao.playlist(id)) { "Playlist no longer exists." }
        val title = name(value); val key = title.lowercase(Locale.ROOT)
        require(dao.named(key)?.id.let { it == null || it == id }) { "A playlist with that name already exists." }
        dao.save(playlist.copy(name = title, nameKey = key, updatedAt = System.currentTimeMillis()))
        if (playlist.synced && !playlist.remoteDeleted && playlist.name != title) shareIdentity()?.let { emit(it, "playlist_renamed", id, title) }
    } }
    /** Deleting a synced playlist also deletes it for the desktop, which keeps its own collection. */
    suspend fun delete(id: String): Result<Unit> = attempt { db.withTransaction {
        val playlist = dao.playlist(id)
        if (playlist?.synced == true && !playlist.remoteDeleted) shareIdentity()?.let { emit(it, "playlist_deleted", id, true) }
        dao.delete(id)
    } }
    suspend fun add(id: String, songIds: List<String>): Result<Unit> = edit(id) { (it + songIds).distinct() }
    suspend fun remove(id: String, songId: String): Result<Unit> = edit(id) { it.filterNot { song -> song == songId } }
    suspend fun removeAll(id: String, songIds: Collection<String>): Result<Unit> { val gone = songIds.toSet(); return edit(id) { it.filterNot { song -> song in gone } } }
    suspend fun move(id: String, songId: String, target: Int): Result<Unit> = edit(id) { old ->
        old.toMutableList().apply { if (remove(songId)) add(target.coerceIn(0, size), songId) }
    }

    /** Start sharing a phone playlist with the paired desktop. Requires a paired, profile-sync identity. */
    suspend fun share(id: String): Result<Unit> = attempt { db.withTransaction {
        val playlist = requireNotNull(dao.playlist(id)) { "Playlist no longer exists." }
        if (playlist.synced && !playlist.remoteDeleted) return@withTransaction
        val identity = requireNotNull(shareIdentity()) { "Pair with your desktop before syncing playlists." }
        require(PlaylistSyncRules.validPlaylistId(id)) { "This playlist can't be shared." }
        dao.save(playlist.copy(synced = true, remoteDeleted = false, updatedAt = System.currentTimeMillis()))
        emit(identity, "playlist_renamed", id, playlist.name)
        emit(identity, "playlist_deleted", id, false)
        writeShared(identity, id, dao.entries(id).map { it.copy(positionKey = null) }, dao.songs(id))
    } }

    /** Stop sharing on this phone only. Nothing is deleted on the desktop. */
    suspend fun stopSharing(id: String): Result<Unit> = attempt { db.withTransaction {
        val playlist = requireNotNull(dao.playlist(id)) { "Playlist no longer exists." }
        dao.save(playlist.copy(synced = false, remoteDeleted = false))
        replace(id, dao.songs(id))
    } }

    /** Add a desktop-shared playlist to this phone; its contents follow at once and on every sync. */
    suspend fun addRemote(id: String): Result<Unit> = attempt { db.withTransaction {
        require(PlaylistSyncRules.validPlaylistId(id) && dao.playlist(id) == null) { "That playlist is already on this phone." }
        val identity = requireNotNull(shareIdentity()) { "Pair with your desktop before syncing playlists." }
        val shared = PlaylistSyncRules.state(id, registers(identity.serverId!!))
        val title = uniqueName(requireNotNull(shared.name) { "That playlist is no longer shared." }, id)
        val now = System.currentTimeMillis()
        dao.save(LocalPlaylist(id, title, title.lowercase(Locale.ROOT), now, now, synced = true))
        applyShared(identity.serverId)
    } }

    /** Project winning shared registers onto synced local playlists. Runs inside the sync transaction. */
    internal suspend fun applyShared(serverId: String) {
        val registers = registers(serverId)
        val localFor = localSongsByMember(serverId)
        for (playlist in dao.syncedPlaylists()) {
            if (playlist.remoteDeleted) continue
            val shared = PlaylistSyncRules.state(playlist.id, registers)
            if (shared.deleted) { dao.save(playlist.copy(remoteDeleted = true)); continue }
            var next = playlist
            if (shared.name != null && shared.name != playlist.name) {
                val title = uniqueName(shared.name, playlist.id)
                next = playlist.copy(name = title, nameKey = title.lowercase(Locale.ROOT))
            }
            val entries = shared.members.mapNotNull { (member, position) -> localFor[member]?.let { it to position } }
                .distinctBy { it.first }
            val old = dao.entries(playlist.id)
            if (old.map { it.songId to it.positionKey } != entries) {
                dao.clearEntries(playlist.id)
                dao.insertEntries(entries.mapIndexed { index, (song, position) -> LocalPlaylistSong(playlist.id, song, index, position) })
                next = next.copy(updatedAt = System.currentTimeMillis())
            }
            if (next != playlist) dao.save(next)
        }
    }

    private suspend fun edit(id: String, change: (List<String>) -> List<String>): Result<Unit> = attempt { db.withTransaction {
        val playlist = requireNotNull(dao.playlist(id)) { "Playlist no longer exists." }
        val entries = dao.entries(id)
        val songs = change(entries.map { it.songId })
        val identity = if (playlist.synced && !playlist.remoteDeleted) shareIdentity() else null
        if (identity == null) replace(id, songs) else writeShared(identity, id, entries, songs)
        dao.save(playlist.copy(updatedAt = System.currentTimeMillis()))
    } }

    /** Saves [songs] in order with shared position keys and queues only the member changes. */
    private suspend fun writeShared(identity: JournalIdentity, id: String, old: List<LocalPlaylistSong>, songs: List<String>) {
        val members = memberKeys(identity.serverId!!)
        val memberOf = { song: String -> members[song] ?: "local:$song" }
        val current = old.mapNotNull { entry -> entry.positionKey?.let { memberOf(entry.songId) to it } }.toMap()
        val desired = songs.map(memberOf).distinct()
        val changes = PlaylistSyncRules.changes(current, desired)
        val positions = current + changes.filter { it.present }.associate { it.member to it.position }
        dao.clearEntries(id)
        dao.insertEntries(songs.distinct().mapIndexed { index, song -> LocalPlaylistSong(id, song, index, positions[memberOf(song)]) })
        for (change in changes) emit(identity, "playlist_member_changed", id, change.present, change.member, change.position)
    }

    private suspend fun replace(id: String, songIds: List<String>) {
        dao.clearEntries(id)
        dao.insertEntries(songIds.mapIndexed { index, songId -> LocalPlaylistSong(id, songId, index) })
    }

    private suspend fun uniqueName(base: String, id: String): String {
        var title = base; var suffix = 2
        while (dao.named(title.lowercase(Locale.ROOT))?.id.let { it != null && it != id })
            title = "${base.take(PlaylistSyncRules.NAME_LIMIT - 5)} (${suffix++})"
        return title
    }

    private fun shareIdentity(): JournalIdentity? = JournalIdentityProvider.currentProfileIdentity()?.takeIf { it.serverId != null }

    private suspend fun registers(serverId: String) = db.journal().profileRegisters(serverId)
        .filter { it.registerKey.startsWith("playlist_") }.associate { it.registerKey to it.valueJson }

    /** Local song id to shared member key: the matched catalog song id for this server. */
    private fun memberKeys(serverId: String): Map<String, String> = JournalIdentityProvider.mappings.value.entries.values
        .filter { it.serverId == serverId && it.song.available }.associate { it.song.id to it.songId }

    private suspend fun localSongsByMember(serverId: String): Map<String, String> {
        val result = HashMap<String, String>()
        memberKeys(serverId).forEach { (local, catalog) -> result.putIfAbsent(catalog, local) }
        db.library().allSongIds().forEach { result.putIfAbsent("local:$it", it) }
        return result
    }

    private suspend fun emit(identity: JournalIdentity, kind: String, playlistId: String, value: Any,
        member: String? = null, position: String? = null) {
        val journal = db.journal()
        val clock = (journal.clock() ?: 0L) + 1
        check(clock <= 9007199254740991L) { "Journal clock exhausted" }
        val eventId = UUID.randomUUID().toString()
        val payload = JSONObject().apply {
            put("protocol_version", 1); put("device_id", identity.deviceId); put("event_id", eventId)
            put("logical_clock", clock); put("kind", kind); put("occurred_at", System.currentTimeMillis() / 1000)
            put("playlist_id", playlistId); put("value", value)
            if (member != null) {
                put("position", position)
                if (member.startsWith("local:")) { put("song_id", JSONObject.NULL); put("local_id", member.removePrefix("local:")) }
                else {
                    val catalogId = JournalIdentityProvider.mappings.value.entries.values.firstOrNull { it.songId == member }?.catalogId
                    put("song_id", member); put("catalog_id", catalogId)
                }
            }
        }.toString()
        journal.clock(LocalJournalClock(value = clock))
        journal.enqueue(LocalEventOutbox(eventId, identity.serverId, identity.deviceId, payload))
    }
}
