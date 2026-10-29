package dev.radio.mobile.data

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.room.Room
import androidx.room.withTransaction
import dev.radio.mobile.music.LocalMusicScanner
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

data class LibraryState(
    val root: LibraryRoot? = null,
    val folders: List<LocalFolder> = emptyList(),
    val songs: List<LocalSong> = emptyList(),
    val loading: Boolean = true,
    val scanning: Boolean = false,
    val accessLost: Boolean = false,
    val scannedCount: Int = 0,
    val scanningFolder: String = "",
    val message: String? = null,
    val error: String? = null
)

class LibraryRepository(context: Context, private val db: LibraryDatabase) {
    private val resolver = context.applicationContext.contentResolver
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    internal val playbackSnapshots = PlaybackSnapshotStore(db.library(), scope)
    val playlists = PlaylistRepository(db, scope)
    private val mutex = Mutex()
    private val mutable = MutableStateFlow(LibraryState())
    val state: StateFlow<LibraryState> = mutable.asStateFlow()
    private var scanJob: Job? = null
    private fun hasAccess(uri: Uri) = resolver.persistedUriPermissions.any { it.uri == uri && it.isReadPermission }
    private val ready = scope.launch {
        try { load() } catch (_: Exception) { mutable.update { it.copy(loading = false, error = "Couldn't open the saved library. Restart the app to retry.") } }
    }

    private suspend fun load(message: String? = null) {
        val dao = db.library()
        val root = dao.root()
        mutable.value = LibraryState(root, root?.let { dao.folders(it.uri) }.orEmpty(), root?.let { dao.songs(it.uri) }.orEmpty(), loading = false,
            accessLost = root != null && !hasAccess(Uri.parse(root.uri)), message = message)
    }

    fun checkAccess() { scope.launch { ready.join(); mutex.withLock {
        val root = mutable.value.root ?: return@withLock
        mutable.update { it.copy(accessLost = !hasAccess(Uri.parse(root.uri))) }
    } } }
    fun chooseFolder(uri: Uri) = startScan(uri, takeGrant = true)
    fun rescan(): Job? = state.value.root?.let { startScan(Uri.parse(it.uri), takeGrant = false) }
    fun cancelScan() { scanJob?.cancel() }
    fun recordPlaybackStart(songId: String, attemptId: String, positionMs: Long) {
        scope.launch {
            ready.join()
            try { db.library().savePlaybackStart(LocalPlaybackStart(attemptId, songId, System.currentTimeMillis(), positionMs)) }
            catch (error: Exception) { android.util.Log.w("RadioPlayback", "Couldn't save local playback start", error) }
        }
    }

    @Synchronized
    private fun startScan(uri: Uri, takeGrant: Boolean): Job {
        scanJob?.takeIf { it.isActive }?.let { return it }
        return scope.launch {
            ready.join()
            mutex.withLock {
                val oldRoot = mutable.value.root?.uri
                val alreadyGranted = hasAccess(uri)
                var adopted = false
                mutable.update { it.copy(scanning = true, scannedCount = 0, scanningFolder = "", error = null, message = null) }
                try {
                    if (takeGrant) resolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    if (!hasAccess(uri)) throw SecurityException("Folder access has expired")
                    val previous = db.library().allSongs().associateBy { it.id }
                    val snapshot = LocalMusicScanner(resolver).scan(uri, previous) { count, path ->
                        mutable.update { it.copy(scannedCount = count, scanningFolder = path) }
                    }
                    currentCoroutineContext().ensureActive()
                    db.withTransaction {
                        val dao = db.library()
                        dao.markSongsMissing(uri.toString())
                        dao.markFoldersMissing(uri.toString())
                        dao.saveFolders(snapshot.folders)
                        dao.saveSongs(snapshot.songs)
                        dao.saveRoot(snapshot.root)
                    }
                    adopted = true
                    // Publication has committed. Finish refreshing UI even if Cancel raced the commit.
                    withContext(NonCancellable) {
                        load("${snapshot.songs.size} MP3s scanned" + if (snapshot.unknownDurations > 0) " · ${snapshot.unknownDurations} durations unavailable" else "")
                        if (oldRoot != null && oldRoot != uri.toString()) runCatching { resolver.releasePersistableUriPermission(Uri.parse(oldRoot), Intent.FLAG_GRANT_READ_URI_PERMISSION) }
                    }
                } catch (cancelled: CancellationException) {
                    mutable.update { it.copy(scanning = false, message = "Scan cancelled. Previous library kept.") }
                    throw cancelled
                } catch (_: SecurityException) {
                    mutable.update { it.copy(scanning = false, accessLost = it.root?.let { root -> !hasAccess(Uri.parse(root.uri)) } ?: false,
                        error = "Folder access is unavailable. Choose the Music folder again. Previous library kept.") }
                } catch (_: Exception) {
                    mutable.update { it.copy(scanning = false, error = "Couldn't scan all folders. Previous library kept. Check folder access and try again.") }
                } finally {
                    if (!adopted && takeGrant && !alreadyGranted && oldRoot != uri.toString()) runCatching { resolver.releasePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
                }
            }
        }.also { scanJob = it }
    }

    internal suspend fun close() { scope.coroutineContext[Job]?.cancelAndJoin() }

    companion object {
        @Volatile private var instance: LibraryRepository? = null
        fun get(context: Context): LibraryRepository = instance ?: synchronized(this) {
            instance ?: LibraryRepository(context, Room.databaseBuilder(context.applicationContext, LibraryDatabase::class.java, "local-library.db").addMigrations(LIBRARY_MIGRATION_1_2, LIBRARY_MIGRATION_2_3, LIBRARY_MIGRATION_3_4).build()).also { instance = it }
        }
    }
}
