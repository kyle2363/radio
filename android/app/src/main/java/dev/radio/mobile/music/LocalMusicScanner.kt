package dev.radio.mobile.music

import android.content.ContentResolver
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.provider.DocumentsContract
import android.provider.DocumentsContract.Document
import dev.radio.mobile.data.*
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import java.io.IOException
import java.util.UUID

internal fun localDocumentId(authority: String, documentId: String): String =
    UUID.nameUUIDFromBytes((authority + "\u0000" + documentId).toByteArray(Charsets.UTF_8)).toString()

data class ScanSnapshot(val root: LibraryRoot, val folders: List<LocalFolder>, val songs: List<LocalSong>, val unknownDurations: Int)

/** Batched child-document queries; only read operations are used on the selected tree. */
class LocalMusicScanner(private val resolver: ContentResolver) {
    suspend fun scan(tree: Uri, previous: Map<String, LocalSong>, progress: (Int, String) -> Unit): ScanSnapshot {
        val authority = tree.authority ?: throw IOException("Invalid folder")
        val rootDocument = DocumentsContract.getTreeDocumentId(tree)
        val rootUri = tree.toString()
        val scanContext = currentCoroutineContext()
        val columns = arrayOf(Document.COLUMN_DOCUMENT_ID, Document.COLUMN_DISPLAY_NAME, Document.COLUMN_MIME_TYPE, Document.COLUMN_LAST_MODIFIED, Document.COLUMN_SIZE)
        data class Entry(val document: String, val name: String, val mime: String, val modified: Long?, val size: Long?)
        fun query(uri: Uri): List<Entry> {
            val cursor = resolver.query(uri, columns, null, null, null) ?: throw IOException("Folder provider did not respond")
            return cursor.use {
                val result = mutableListOf<Entry>()
                while (it.moveToNext()) {
                    scanContext.ensureActive()
                    if (result.size >= 100_000) throw IOException("Folder exceeds the scan limit")
                    result += Entry(it.getString(0), it.getString(1) ?: "Unnamed", it.getString(2) ?: "", if (it.isNull(3)) null else it.getLong(3).takeIf { n -> n > 0 }, if (it.isNull(4)) null else it.getLong(4))
                }
                result
            }
        }
        val rootEntry = query(DocumentsContract.buildDocumentUriUsingTree(tree, rootDocument)).singleOrNull()
            ?: throw IOException("Selected folder is unavailable")
        if (rootEntry.mime != Document.MIME_TYPE_DIR) throw IOException("Select a folder")
        val rootId = localDocumentId(authority, rootDocument)
        val folders = mutableListOf(LocalFolder(rootId, rootUri, null, rootEntry.name, "", rootEntry.modified))
        val songs = linkedMapOf<String, LocalSong>()
        val pending = ArrayDeque<Pair<String, LocalFolder>>()
        pending.add(rootDocument to folders.first())
        val visited = mutableSetOf(rootDocument)
        val now = System.currentTimeMillis()
        var documents = 0
        var unknown = 0
        while (pending.isNotEmpty()) {
            currentCoroutineContext().ensureActive()
            val (document, folder) = pending.removeFirst()
            progress(songs.size, folder.path.ifEmpty { rootEntry.name })
            val entries = query(DocumentsContract.buildChildDocumentsUriUsingTree(tree, document))
            for (entry in entries) {
                currentCoroutineContext().ensureActive()
                if (++documents > 100_000) throw IOException("Folder exceeds the scan limit")
                if (!visited.add(entry.document)) continue
                val id = localDocumentId(authority, entry.document)
                if (entry.mime == Document.MIME_TYPE_DIR) {
                    val child = LocalFolder(id, rootUri, folder.id, entry.name, listOf(folder.path, entry.name).filter { it.isNotEmpty() }.joinToString("/"), entry.modified)
                    folders += child
                    pending.add(entry.document to child)
                } else if (entry.name.endsWith(".mp3", ignoreCase = true) || entry.mime in setOf("audio/mpeg", "audio/mp3")) {
                    val uri = DocumentsContract.buildDocumentUriUsingTree(tree, entry.document)
                    val old = previous[id]
                    val duration = if (old?.durationMs != null && entry.modified != null && old.modifiedAt == entry.modified && old.byteSize == entry.size) old.durationMs else readDuration(uri)
                    if (duration == null) unknown++
                    songs[id] = LocalSong(id, rootUri, folder.id, uri.toString(), entry.name, folder.path, entry.modified, old?.firstSeenAt ?: now, entry.size, duration)
                    if (songs.size % 20 == 0) progress(songs.size, folder.path.ifEmpty { rootEntry.name })
                }
            }
        }
        currentCoroutineContext().ensureActive()
        return ScanSnapshot(LibraryRoot(uri = rootUri, folderId = rootId, name = rootEntry.name, scannedAt = now), folders, songs.values.toList(), unknown)
    }

    private fun readDuration(uri: Uri): Long? = try {
        MediaMetadataRetriever().use { retriever ->
            resolver.openAssetFileDescriptor(uri, "r")?.use { file ->
                if (file.declaredLength >= 0) retriever.setDataSource(file.fileDescriptor, file.startOffset, file.declaredLength)
                else retriever.setDataSource(file.fileDescriptor)
                retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull()?.takeIf { it > 0 }
            }
        }
    } catch (_: Exception) { null } // Missing tags/invalid audio never make a discovered filename disappear.
}
