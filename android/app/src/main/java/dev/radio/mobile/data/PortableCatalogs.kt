package dev.radio.mobile.data

import androidx.room.*

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
