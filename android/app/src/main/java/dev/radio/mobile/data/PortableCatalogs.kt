package dev.radio.mobile.data

import androidx.room.*
import java.security.MessageDigest
import dev.radio.mobile.connection.ConnectionFailure
import dev.radio.mobile.portable.CatalogJson
import dev.radio.mobile.portable.CatalogException

/** Metadata is intentionally separate from local songs, playlists and playback. */
@Entity(tableName = "portable_catalog_revisions", primaryKeys = ["serverId", "manifestSha256"])
data class PortableCatalogRevision(val manifestSha256: String, val serverId: String, val catalogId: String,
    val revision: Long, val manifest: String, val songCount: Int, val vectorCount: Int, val importedAt: Long)

@Entity(tableName = "portable_catalog_songs", primaryKeys = ["manifestSha256", "songId"])
data class PortableCatalogSong(val manifestSha256: String, val songId: String, val payload: String)

@Entity(tableName = "portable_catalog_vectors", primaryKeys = ["manifestSha256", "songId", "space"])
data class PortableCatalogVector(val manifestSha256: String, val songId: String, val space: String, val model: String,
    val modelVersion: String, val dimension: Int, val metric: String, val normalization: String, val dtype: String,
    val byteLength: Int, val blobSha256: String)

@Entity(tableName = "portable_catalog_active")
data class PortableCatalogActive(@PrimaryKey val serverId: String, val catalogId: String, val revision: Long, val manifestSha256: String)

@Dao
interface PortableCatalogDao {
    @Query("SELECT DISTINCT v.blobSha256 FROM portable_catalog_vectors v INNER JOIN portable_catalog_active a ON v.manifestSha256 = a.manifestSha256") suspend fun activeBlobDigests(): List<String>
    @Query("SELECT COALESCE(SUM(length(CAST(r.manifest AS BLOB))),0) FROM portable_catalog_revisions r INNER JOIN portable_catalog_active a ON r.serverId = a.serverId AND r.manifestSha256 = a.manifestSha256 WHERE r.manifestSha256 != :incoming") suspend fun retainedManifestBytes(incoming: String): Long
    @Query("DELETE FROM portable_catalog_revisions WHERE NOT EXISTS (SELECT 1 FROM portable_catalog_active a WHERE a.serverId = portable_catalog_revisions.serverId AND a.manifestSha256 = portable_catalog_revisions.manifestSha256)") suspend fun deleteInactiveRevisions()
    @Query("DELETE FROM portable_catalog_songs WHERE manifestSha256 NOT IN (SELECT manifestSha256 FROM portable_catalog_active)") suspend fun deleteInactiveSongs()
    @Query("DELETE FROM portable_catalog_vectors WHERE manifestSha256 NOT IN (SELECT manifestSha256 FROM portable_catalog_active)") suspend fun deleteInactiveVectors()
    @Query("SELECT * FROM portable_catalog_revisions WHERE serverId = :serverId AND manifestSha256 = :digest LIMIT 1") suspend fun revision(serverId: String, digest: String): PortableCatalogRevision?
    @Query("SELECT r.* FROM portable_catalog_revisions r INNER JOIN portable_catalog_active a ON r.serverId = a.serverId AND r.manifestSha256 = a.manifestSha256 ORDER BY r.importedAt DESC LIMIT 1")
    suspend fun latest(): PortableCatalogRevision?
    @Query("SELECT r.* FROM portable_catalog_revisions r INNER JOIN portable_catalog_active a ON r.serverId = a.serverId AND r.manifestSha256 = a.manifestSha256 WHERE a.serverId = :serverId LIMIT 1")
    suspend fun active(serverId: String): PortableCatalogRevision?
    @Query("SELECT * FROM portable_catalog_vectors WHERE manifestSha256 = :digest") suspend fun vectors(digest: String): List<PortableCatalogVector>
    @Query("SELECT * FROM portable_catalog_songs WHERE manifestSha256 = :digest") suspend fun songs(digest: String): List<PortableCatalogSong>
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun saveRevision(revision: PortableCatalogRevision)
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun saveSongs(songs: List<PortableCatalogSong>)
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun saveVectors(vectors: List<PortableCatalogVector>)
    @Upsert suspend fun activate(active: PortableCatalogActive)
}

sealed interface LyricsEnvelope {
    data class Available(val text: String, val textSha256: String) : LyricsEnvelope
    data class Unavailable(val reason: String) : LyricsEnvelope
}

/** Strict client-side envelope and text integrity validation for the optional lyrics route. */
object LyricsEnvelopeValidator {
    const val MAX_TEXT_BYTES = 65_536
    private val sha256 = Regex("[0-9a-f]{64}")

    fun parse(body: String, catalogId: String, revision: Long, manifestSha256: String, songId: String): LyricsEnvelope {
        try {
            if (body.toByteArray(Charsets.UTF_8).size > 512 * 1024) fail()
            val obj = CatalogJson.parseStrict(body.toByteArray(Charsets.UTF_8)).obj()
            fun text(key: String, max: Int = 4096) = obj[key].text(max)
            if (text("schema") != "radio.portable.lyrics" || obj["version"].integer() != 1L ||
                text("catalog_id", 256) != catalogId || obj["revision"].integer() != revision ||
                text("manifest_sha256", 64) != manifestSha256 || text("song_id", 256) != songId)
                fail()
            return when (text("status", 32)) {
                "available" -> {
                    if (obj.fields.keys != setOf("schema", "version", "catalog_id", "revision", "manifest_sha256", "song_id", "status", "text", "text_sha256")) fail()
                    val lyrics = (obj["text"] as? CatalogJson.Str)?.value ?: fail()
                    val bytes = lyrics.toByteArray(Charsets.UTF_8)
                    if (bytes.isEmpty() || bytes.size > MAX_TEXT_BYTES || lyrics != lyrics.replace("\r\n", "\n").replace('\r', '\n').trim()) fail()
                    val digest = text("text_sha256", 64)
                    if (!sha256.matches(digest) || sha256(bytes) != digest) fail()
                    LyricsEnvelope.Available(lyrics, digest)
                }
                "unavailable" -> {
                    if (obj.fields.keys != setOf("schema", "version", "catalog_id", "revision", "manifest_sha256", "song_id", "status", "reason", "text", "text_sha256") ||
                        obj["text"] !== CatalogJson.Null || obj["text_sha256"] !== CatalogJson.Null) fail()
                    val reason = text("reason", 32)
                    if (reason !in setOf("missing", "too_large")) fail()
                    LyricsEnvelope.Unavailable(reason)
                }
                else -> fail()
            }
        } catch (failure: ConnectionFailure) { throw failure }
        catch (_: CatalogException) { throw ConnectionFailure("The desktop returned invalid lyrics data. Retry the request.") }
        catch (_: Exception) { throw ConnectionFailure("The desktop returned invalid lyrics data. Retry the request.") }
    }

    private fun sha256(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
    private fun fail(): Nothing = throw ConnectionFailure("The desktop returned invalid lyrics data. Retry the request.")
}
