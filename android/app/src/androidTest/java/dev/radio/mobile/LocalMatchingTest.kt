package dev.radio.mobile

import android.net.Uri
import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.portable.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.first
import org.junit.Assert.*
import org.junit.Test
import java.security.MessageDigest

class LocalMatchingTest {
 private val context get()=InstrumentationRegistry.getInstrumentation().targetContext
 private fun fixture(method:String,arg:String?=null)=context.contentResolver.call(Uri.parse("content://dev.radio.mobile.test.control"),method,arg,null)
 @Test fun hashesBytesCachesAndPersistsScopedCorrectionsWithoutPlaybackWrites()= runBlocking {
  fixture("reset");fixture("grant")
  val db=Room.inMemoryDatabaseBuilder(context,LibraryDatabase::class.java).build()
  val library=LibraryRepository(context,db)
  try {
   withTimeout(5000){library.state.first{!it.loading}}
   library.chooseFolder(FixtureDocumentsProvider.tree()).join()
   val local=library.state.value.songs.first()
   val bytes=context.contentResolver.openInputStream(Uri.parse(local.uri))!!.use{it.readBytes()}
   val digest=MessageDigest.getInstance("SHA-256").digest(bytes).joinToString(""){"%02x".format(it)}
   suspend fun catalog(server:String,catalog:String,revision:Long=1, targetDigest:String=digest, includeTargets:Boolean=true) {
    val manifest="${server}-${catalog}-$revision"
    db.catalogs().saveRevision(PortableCatalogRevision(manifest,server,catalog,revision,"{}",2,0,revision))
    val payload="{\"artist\":\"Artist\",\"content_sha256\":\"$targetDigest\",\"title\":\"Original\"}"
    if(includeTargets) db.catalogs().saveSongs(listOf(PortableCatalogSong(manifest,"one",payload),PortableCatalogSong(manifest,"two",payload)))
    db.catalogs().activate(PortableCatalogActive(server,catalog,revision,manifest))
   }
   catalog("server","catalog")
   var matching=MatchingRepository(context,db)
   matching.matchLocalSongs()
   assertEquals("ambiguous",matching.state.value.results.first{it.localId==local.id}.status)
   val cached=db.matching().hash(local.id)!!
   matching.matchLocalSongs();assertEquals(cached,db.matching().hash(local.id))
   val queriesBefore=fixture("stats")!!.getInt("documentQueries")
   val rowIds=matching.state.value.results.map{it.localId}
   val emittedSizes=mutableListOf<Int>()
   val observer=launch(Dispatchers.Unconfined){matching.state.collect{emittedSizes.add(it.results.size)}}
   matching.confirm(local.id,"one")
   observer.cancelAndJoin()
   assertEquals(rowIds,matching.state.value.results.map{it.localId})
   assertTrue("Saving a correction must retain the list",emittedSizes.all{it==rowIds.size})
   assertTrue("Saving one correction must not query every local file",fixture("stats")!!.getInt("documentQueries")-queriesBefore<=3)
   matching=MatchingRepository(context,db);matching.load()
   assertEquals("confirmed",matching.state.value.results.first{it.localId==local.id}.status)
   matching.reject(local.id,"one")
   assertTrue(matching.state.value.results.first{it.localId==local.id}.candidates.any{it.songId=="one" && "user_rejected" in it.reasons})
   matching.clearDecision(local.id,"one")
   val other=library.state.value.songs.first{it.id!=local.id}
   val beforeBatchIds=matching.state.value.results.map{it.localId}
   matching.batchConfirm(listOf(local.id to "two",other.id to "one"))
   assertEquals(beforeBatchIds,matching.state.value.results.map{it.localId})
   assertEquals("two",matching.state.value.results.first{it.localId==local.id}.songId)
   assertEquals("one",matching.state.value.results.first{it.localId==other.id}.songId)
   assertTrue(matching.state.value.results.filter{it.localId in setOf(local.id,other.id)}.all{it.status=="confirmed"})
   catalog("server","catalog",2);matching.load()
   assertEquals("confirmed",matching.state.value.results.first{it.localId==local.id}.status)
   catalog("server","catalog",3,"a".repeat(64));matching.load()
   assertNotEquals("confirmed",matching.state.value.results.first{it.localId==local.id}.status)
   assertTrue(matching.state.value.results.first{it.localId==local.id}.candidates.any{"catalog_recording_changed" in it.reasons})
   catalog("server","catalog",4,includeTargets=false);matching.load()
   assertTrue(matching.state.value.results.first{it.localId==local.id}.candidates.any{"missing_catalog_recording" in it.reasons})
   matching.clearDecision(local.id,"two")
   assertEquals("unmatched",matching.state.value.results.first{it.localId==local.id}.status)
   catalog("other-server","other-catalog",5);matching.load()
   assertEquals("ambiguous",matching.state.value.results.first{it.localId==local.id}.status)
   db.library().saveSongs(listOf(local.copy(uri=library.state.value.songs.last().uri+"?replacement",modifiedAt=42)))
   matching.load()
   assertNotEquals("confirmed",matching.state.value.results.first{it.localId==local.id}.status)
   assertTrue(db.library().playbackStarts().isEmpty());assertNull(db.library().playbackSnapshot())
   assertEquals(0,fixture("stats")!!.getInt("writes"))
  } finally {library.close();db.close()}
 }
 @Test fun cancelledMatchDoesNotPublishResults()=runBlocking {
  val db=Room.inMemoryDatabaseBuilder(context,LibraryDatabase::class.java).build()
  try {
   fixture("reset");fixture("grant")
   val library=LibraryRepository(context,db)
   withTimeout(5000){library.state.first{!it.loading}}
   library.chooseFolder(FixtureDocumentsProvider.tree()).join()
   db.catalogs().saveRevision(PortableCatalogRevision("cancel","server","catalog",1,"{}",0,0,1))
   db.catalogs().activate(PortableCatalogActive("server","catalog",1,"cancel"))
   var entered=CompletableDeferred<Unit>()
   var block=true
   val matching=MatchingRepository(context,db,beforeRead={if(block){entered.complete(Unit);awaitCancellation()}})
   val job=launch{matching.matchLocalSongs()}
   withTimeout(5000){entered.await()}
   assertTrue(matching.state.value.busy)
   matching.cancel();job.join()
   assertTrue(matching.state.value.results.isEmpty())
   block=false
   val local=library.state.value.songs.first()
   val bytes=context.contentResolver.openInputStream(Uri.parse(local.uri))!!.use{it.readBytes()}
   val digest=MessageDigest.getInstance("SHA-256").digest(bytes).joinToString(""){"%02x".format(it)}
   db.catalogs().saveSongs(listOf(PortableCatalogSong("cancel","one","{\"content_sha256\":\"$digest\"}")))
   matching.matchLocalSongs()
   entered=CompletableDeferred();block=true
   val correction=launch{matching.confirm(local.id,"one")}
   withTimeout(5000){entered.await()}
   matching.cancel();correction.join()
   assertTrue(db.matching().decisions("server","catalog").isEmpty())
   library.close()
   assertFalse(matching.state.value.busy)
   assertTrue(matching.state.value.progress.contains("cancelled"))
   assertNotEquals("confirmed",matching.state.value.results.first{it.localId==local.id}.status)
  } finally {db.close()}
 }
}
