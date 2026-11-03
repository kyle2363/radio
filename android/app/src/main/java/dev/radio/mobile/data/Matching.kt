package dev.radio.mobile.data
import androidx.room.*

@Entity(tableName="local_content_hashes")
data class LocalContentHash(@PrimaryKey val songId: String, val uri: String, val byteSize: Long?, val modifiedAt: Long?, val sha256: String, val hashedAt: Long)
@Entity(tableName="portable_match_decisions", primaryKeys=["serverId","catalogId","localId","contentSha256","songId"])
data class PortableMatchDecision(val serverId: String,val catalogId: String,val localId: String,val contentSha256: String,val songId: String,val action: String,val decidedAt: Long,val targetIdentity:String)
@Dao interface MatchingDao {
 @Query("SELECT * FROM local_content_hashes WHERE songId=:id") suspend fun hash(id:String):LocalContentHash?
 @Upsert suspend fun saveHash(value:LocalContentHash)
 @Query("DELETE FROM local_content_hashes WHERE songId=:id") suspend fun deleteHash(id:String)
 @Query("SELECT * FROM portable_match_decisions WHERE serverId=:server AND catalogId=:catalog") suspend fun decisions(server:String,catalog:String):List<PortableMatchDecision>
 @Query("DELETE FROM portable_match_decisions WHERE serverId=:server AND catalogId=:catalog AND localId=:local AND contentSha256=:hash AND action='confirm'") suspend fun clearConfirmations(server:String,catalog:String,local:String,hash:String)
 @Upsert suspend fun saveDecision(value:PortableMatchDecision)
 @Query("DELETE FROM portable_match_decisions WHERE serverId=:server AND catalogId=:catalog AND localId=:local AND contentSha256=:hash AND songId=:song") suspend fun clearDecision(server:String,catalog:String,local:String,hash:String,song:String)
}
