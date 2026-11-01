package dev.radio.mobile

import androidx.room.Room
import androidx.room.withTransaction
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.data.*
import dev.radio.mobile.recommendation.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import org.junit.Assert.*
import org.junit.Test
import dev.radio.mobile.portable.*
import dev.radio.mobile.playback.PlaybackState
import org.json.JSONObject
import java.io.File
import java.util.Base64
import java.util.UUID

class RadioRepositoryTest {
    @Test fun removingDislikesDoesNotConsumeTheTwoThousandSignalTasteWindow()=runBlocking {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        val db=Room.inMemoryDatabaseBuilder(context,LibraryDatabase::class.java).build()
        try{
            db.withTransaction {
                db.journal().insert(LocalListeningEvent("retained","song",null,"song_completed",1,1,"{}",1000,1.0,null,"local_only"))
                repeat(2001){index->db.journal().insert(LocalListeningEvent("undislike-$index","song",null,"dislike_changed",index+2L,index+2L,"{\"value\":false}",0,null,null,"local_only"))}
                db.journal().insert(LocalListeningEvent("positive-dislike","song",null,"dislike_changed",3000,3000,"{\"value\":true}",0,null,null,"local_only"))
            }
            assertEquals(listOf("positive-dislike","retained"),db.journal().radioTasteEvents().map{it.eventId})
        }finally{db.close()}
    }
    @Test fun verifiedImportedVectorsVibeLimitsAndCorruptionRemainOfflineAndLocal()=runBlocking {
        val instrument=InstrumentationRegistry.getInstrumentation();val context=instrument.targetContext
        context.getSharedPreferences("radio_recommendations",0).edit().clear().commit()
        val db=Room.inMemoryDatabaseBuilder(context,LibraryDatabase::class.java).build();var scope=CoroutineScope(SupervisorJob()+Dispatchers.IO)
        val directory=File(context.cacheDir,".test-runtime/radio/${UUID.randomUUID()}").apply{mkdirs()}
        val song=LocalSong("radio-vector","root","folder","content://fixture/vector","Song.mp3","Music",1,1,1,10000)
        val local=MutableStateFlow(LibraryState(songs=listOf(song),loading=false))
        try{
            val fixture=JSONObject(instrument.context.assets.open("mobile_catalog_golden.json").bufferedReader().use{it.readText()}).getJSONArray("cases")
            val row=(0 until fixture.length()).map{fixture.getJSONObject(it)}.first{it.getString("name")=="full_unicode_numeric"}
            val manifest=CatalogValidator.manifest(row.getString("manifest_utf8").toByteArray())
            val record=PortableCatalogRevision(manifest.sha256,"radio-server",manifest.catalogId,manifest.revision,row.getString("manifest_utf8"),2,3,1)
            db.catalogs().saveRevision(record);db.catalogs().activate(PortableCatalogActive(record.serverId,record.catalogId,record.revision,record.manifestSha256))
            val blobs=row.getJSONObject("blobs");blobs.keys().forEach{File(directory,"$it.bin").writeBytes(Base64.getDecoder().decode(blobs.getString(it)))}
            JournalIdentityProvider.invalidateMappings()
            val journal=ListeningJournalRepository(context,db,scope)
            withTimeout(10000){JournalIdentityProvider.mappings.first{it.loaded}}
            val generation=JournalIdentityProvider.invalidateMappings()
            JournalIdentityProvider.publishMappings(generation,listOf(JournalIdentityProvider.Entry(song,record.serverId,record.catalogId,record.revision,"a","confirmed:${manifest.sha256}:hash")))
            var radio=RadioRepository(context,db,local,journal,scope,directory)
            radio.updatePlayback(PlaybackState(queue=listOf(song),currentId=song.id))
            withTimeout(10000){radio.state.first{it.loaded && !it.busy && it.vibeAvailable}}
            assertEquals(1,radio.state.value.matched)
            radio.lockVibe();radio.actualStart("one");radio.actualStart("one")
            assertEquals(9,radio.state.value.vibeSongsLeft)
            scope.coroutineContext[Job]!!.cancelAndJoin();scope=CoroutineScope(SupervisorJob()+Dispatchers.IO)
            radio=RadioRepository(context,db,local,journal,scope,directory);radio.updatePlayback(PlaybackState(queue=listOf(song),currentId=song.id))
            withTimeout(10000){radio.state.first{it.loaded && !it.busy}}
            assertEquals(song.id,radio.state.value.vibeLocalId);assertEquals(9,radio.state.value.vibeSongsLeft)
            repeat(9){radio.actualStart("next-$it")};assertNull(radio.state.value.vibeLocalId)
            val damaged=manifest.descriptors.first{it.space=="audio"};File(directory,"${damaged.sha256}.bin").writeBytes(ByteArray(damaged.byteLength))
            radio.refresh();withTimeout(10000){radio.state.first{!it.busy && it.status.contains("reimport")}}
            assertEquals(0,radio.state.value.matched);assertEquals(1,radio.state.value.suggestions.size);assertTrue(radio.state.value.suggestions.single().pick.vectorFallback)
            assertTrue(db.journal().events().isEmpty());assertNull(db.library().playbackSnapshot())
        }finally{scope.coroutineContext[Job]!!.cancelAndJoin();db.close();directory.deleteRecursively();context.getSharedPreferences("radio_recommendations",0).edit().clear().commit()}
    }
    @Test fun localOnlyPreviewIsPureAndPersistedFeedbackExcludesAcrossRestart()=runBlocking {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        context.getSharedPreferences("radio_recommendations",0).edit().clear().commit()
        val db=Room.inMemoryDatabaseBuilder(context,LibraryDatabase::class.java).build()
        var scope=CoroutineScope(SupervisorJob()+Dispatchers.IO)
        val songs=(1..7).map{LocalSong("radio-$it","root","folder","content://fixture/$it","Song $it.mp3","Music",1,1,1,10000)}
        val local=MutableStateFlow(LibraryState(songs=songs,loading=false))
        try {
            var journal=ListeningJournalRepository(context,db,scope)
            var radio=RadioRepository(context,db,local,journal,scope)
            withTimeout(10000){radio.state.first{it.loaded && !it.busy}}
            assertEquals(5,radio.state.value.suggestions.size);assertEquals(7,radio.state.value.localOnly)
            radio.refresh();delay(300)
            assertTrue(db.journal().events().isEmpty());assertTrue(db.library().playbackStarts().isEmpty())
            val rejected=radio.state.value.suggestions.first().song
            journal.setDisliked(rejected,true)
            withTimeout(10000){radio.state.first{!it.busy && it.suggestions.none{p->p.song.id==rejected.id}}}
            radio.setMood("focus");radio.setExploration(.7)
            withTimeout(10000){radio.state.first{!it.busy && it.mood=="focus"}}
            scope.coroutineContext[Job]!!.cancelAndJoin()
            scope=CoroutineScope(SupervisorJob()+Dispatchers.IO)
            journal=ListeningJournalRepository(context,db,scope);radio=RadioRepository(context,db,local,journal,scope)
            withTimeout(10000){radio.state.first{it.loaded && !it.busy}}
            assertEquals("focus",radio.state.value.mood);assertEquals(.7,radio.state.value.exploration,1e-6)
            assertTrue(radio.state.value.suggestions.none{it.song.id==rejected.id})
        }finally{scope.coroutineContext[Job]!!.cancelAndJoin();db.close();context.getSharedPreferences("radio_recommendations",0).edit().clear().commit()}
    }
    @Test fun batchProofRejectsAccessMappingFeedbackAndSettingsChanges()=runBlocking {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        val db=Room.inMemoryDatabaseBuilder(context,LibraryDatabase::class.java).build();val scope=CoroutineScope(SupervisorJob()+Dispatchers.IO)
        val song=LocalSong("radio-proof","root","folder","content://fixture/proof","Song.mp3","Music",1,1,1,10000)
        val local=MutableStateFlow(LibraryState(songs=listOf(song),loading=false));val journal=ListeningJournalRepository(context,db,scope);val radio=RadioRepository(context,db,local,journal,scope)
        try{
            withTimeout(10000){radio.state.first{it.loaded && !it.busy}}
            var batch=radio.recommendations()!!;assertTrue(radio.valid(batch.token))
            local.value=local.value.copy(accessLost=true);assertFalse(radio.valid(batch.token))
            local.value=local.value.copy(accessLost=false)
            batch=radio.recommendations()!!;JournalIdentityProvider.invalidateMappings();assertFalse(radio.valid(batch.token))
            batch=radio.recommendations()!!;radio.setMood(if(radio.state.value.mood=="chill")"focus" else "chill");assertFalse(radio.valid(batch.token))
            batch=radio.recommendations()!!;journal.setDisliked(song,true);assertFalse(radio.valid(batch.token))
        }finally{scope.coroutineContext[Job]!!.cancelAndJoin();db.close()}
    }
}
