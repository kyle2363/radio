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
    @Test fun refreshExcludesPreviousPreviewAndExplicitSeedStartsWithoutJournalWrites()=runBlocking {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        val prefs=context.getSharedPreferences("radio_recommendations",0)
        prefs.edit().clear().commit()
        val db=Room.inMemoryDatabaseBuilder(context,LibraryDatabase::class.java).build()
        val scope=CoroutineScope(SupervisorJob()+Dispatchers.IO)
        val songs=(1..12).map{LocalSong("fresh-$it","root","folder","content://fixture/$it","Song $it.mp3","Music",1,1,1,180000)}
        val local=MutableStateFlow(LibraryState(songs=songs,loading=false))
        try {
            val journal=ListeningJournalRepository(context,db,scope)
            val radio=RadioRepository(context,db,local,journal,scope)
            withTimeout(10000){radio.state.first{it.loaded && !it.busy}}
            val previous=radio.state.value.suggestions.map{it.song.id}.toSet()
            val generation=radio.state.value.generation
            radio.refreshSuggestions()
            withTimeout(10000){radio.state.first{it.generation>generation && !it.busy}}
            assertTrue(previous.intersect(radio.state.value.suggestions.map{it.song.id}.toSet()).isEmpty())
            val batch=radio.startRecommendations(songs.first().id)!!
            assertEquals(songs.first(),batch.suggestions.first().song)
            assertEquals(5,batch.suggestions.map{it.song.id}.distinct().size)
            assertTrue(radio.valid(batch.token))
            assertNull(radio.startRecommendations("missing"))
            assertTrue(db.journal().events().isEmpty())
            assertFalse(prefs.contains("recentSelections"))
            local.value=local.value.copy(songs=songs.take(2))
            withTimeout(10000){radio.state.first{!it.busy && it.diagnostics.eligibleTracks==2}}
            val smallPrevious=radio.state.value.suggestions.map{it.song.id}.toSet()
            val smallGeneration=radio.state.value.generation
            radio.refreshSuggestions()
            withTimeout(10000){radio.state.first{!it.busy && it.generation>smallGeneration}}
            assertTrue(smallPrevious.intersect(radio.state.value.suggestions.map{it.song.id}.toSet()).isEmpty())
            assertNotNull(radio.state.value.error)
            assertNotNull(radio.startRecommendations(songs.first().id))
        } finally {scope.coroutineContext[Job]!!.cancelAndJoin();db.close();prefs.edit().clear().commit()}
    }
    @Test fun legacyDesktopVectorsRequireSnapshotOptInWithoutInventedProvenance()=runBlocking {
        val instrument=InstrumentationRegistry.getInstrumentation();val context=instrument.targetContext
        context.getSharedPreferences("radio_recommendations",0).edit().clear().commit()
        val db=Room.inMemoryDatabaseBuilder(context,LibraryDatabase::class.java).build()
        var scope=CoroutineScope(SupervisorJob()+Dispatchers.IO)
        val directory=File(context.cacheDir,".test-runtime/legacy-radio/${UUID.randomUUID()}").apply{mkdirs()}
        val songs=listOf("a","b").map{LocalSong("legacy-$it","root","folder","content://fixture/$it","Song $it.mp3","Music",1,1,1,10000)}
        val local=MutableStateFlow(LibraryState(songs=songs,loading=false))
        try {
            val cases=JSONObject(instrument.context.assets.open("mobile_catalog_golden.json").bufferedReader().use{it.readText()}).getJSONArray("cases")
            val fixture=(0 until cases.length()).map{cases.getJSONObject(it)}.first{it.getString("name")=="full_unicode_numeric"}
            val original=CatalogJson.parse(fixture.getString("manifest_utf8").toByteArray()).obj()
            val descriptors=original.array("embeddings").map { value ->
                val row=value.obj()
                CatalogJson.Obj(row.fields+mapOf("model" to CatalogJson.Str("legacy_unknown:"+if(row.text("space")=="text")"songs" else "songs_audio"),"model_version" to CatalogJson.Str("unknown")))
            }
            val unsigned=CatalogJson.Obj((original.fields-"manifest_sha256")+mapOf("embeddings" to CatalogJson.Arr(descriptors)))
            val raw=CatalogJson.Obj(unsigned.fields+mapOf("manifest_sha256" to CatalogJson.Str(catalogSha256(unsigned.canonical().toByteArray())))).canonical()
            val manifest=CatalogValidator.manifest(raw.toByteArray())
            val record=PortableCatalogRevision(manifest.sha256,"legacy-server",manifest.catalogId,manifest.revision,raw,2,3,1)
            db.catalogs().saveRevision(record);db.catalogs().activate(PortableCatalogActive(record.serverId,record.catalogId,record.revision,record.manifestSha256))
            val blobs=fixture.getJSONObject("blobs")
            blobs.keys().forEach{File(directory,"$it.bin").writeBytes(Base64.getDecoder().decode(blobs.getString(it)))}
            JournalIdentityProvider.invalidateMappings()
            var journal=ListeningJournalRepository(context,db,scope)
            withTimeout(10000){JournalIdentityProvider.mappings.first{it.loaded}}
            fun publish() {val generation=JournalIdentityProvider.invalidateMappings();JournalIdentityProvider.publishMappings(generation,songs.zip(listOf("a","b")).map{(song,id)->JournalIdentityProvider.Entry(song,record.serverId,record.catalogId,record.revision,id,"confirmed:${manifest.sha256}:hash")})}
            publish()
            var radio=RadioRepository(context,db,local,journal,scope,directory)
            radio.updatePlayback(PlaybackState(queue=songs,currentId=songs.first().id))
            withTimeout(10000){radio.state.first{it.loaded && !it.busy && it.legacyVectorTracks==2}}
            assertEquals(0,radio.state.value.diagnostics.textVectorTracks)
            assertFalse(radio.state.value.legacyVectorsEnabled)
            val strict=radio.recommendations()!!
            radio.setLegacyVectorsEnabled(true)
            assertFalse(radio.valid(strict.token))
            withTimeout(10000){radio.state.first{!it.busy && it.diagnostics.textVectorTracks==2 && it.legacyVectorsEnabled}}
            assertEquals(1,radio.state.value.diagnostics.audioVectorTracks)
            val choice=radio.recommendations(setOf(songs.first().id),1)!!.suggestions.single()
            assertFalse(choice.pick.vectorFallback)
            assertNotNull(choice.pick.textSimilarity)
            assertTrue(db.journal().events().isEmpty());assertTrue(db.journal().allFeedback().isEmpty())
            val identity=JournalIdentity(UUID.randomUUID().toString(),record.serverId,record.catalogId,"a",songs.first().id,"fixture-confirmed")
            journal.started(songs.first(),"legacy-attempt",0,identity)
            journal.finished(identity,"legacy-attempt","song_completed",10000,10000,null)
            withTimeout(10000){radio.state.first{!it.busy && it.diagnostics.contributingTasteEvents==1 && it.diagnostics.hasUsableTaste}}
            scope.coroutineContext[Job]!!.cancelAndJoin();scope=CoroutineScope(SupervisorJob()+Dispatchers.IO)
            journal=ListeningJournalRepository(context,db,scope)
            withTimeout(10000){JournalIdentityProvider.mappings.first{it.loaded}}
            publish()
            radio=RadioRepository(context,db,local,journal,scope,directory)
            withTimeout(10000){radio.state.first{it.loaded && !it.busy && it.legacyVectorsEnabled}}
            assertEquals(2,radio.state.value.diagnostics.textVectorTracks)
            radio.setLegacyVectorsEnabled(false)
            withTimeout(10000){radio.state.first{!it.busy && !it.legacyVectorsEnabled && it.diagnostics.textVectorTracks==0}}
            assertEquals(raw,db.catalogs().latest()!!.manifest)
            radio.setLegacyVectorsEnabled(true)
            withTimeout(10000){radio.state.first{!it.busy && it.diagnostics.textVectorTracks==2}}
            val nextUnsigned=CatalogJson.Obj(unsigned.fields+mapOf("revision" to CatalogJson.Num("2")))
            val nextHash=catalogSha256(nextUnsigned.canonical().toByteArray())
            val nextRaw=CatalogJson.Obj(nextUnsigned.fields+mapOf("manifest_sha256" to CatalogJson.Str(nextHash))).canonical()
            val nextRecord=record.copy(manifestSha256=nextHash,revision=2,manifest=nextRaw,importedAt=2)
            db.catalogs().saveRevision(nextRecord);db.catalogs().activate(PortableCatalogActive(record.serverId,record.catalogId,2,nextHash))
            val generation=JournalIdentityProvider.invalidateMappings()
            JournalIdentityProvider.publishMappings(generation,songs.zip(listOf("a","b")).map{(song,id)->JournalIdentityProvider.Entry(song,record.serverId,record.catalogId,2,id,"confirmed:$nextHash:hash")})
            radio.refresh()
            withTimeout(10000){radio.state.first{!it.busy && it.catalogSnapshot==nextHash && it.legacyVectorTracks==2}}
            assertFalse("Compatibility is scoped to the explicitly selected snapshot",radio.state.value.legacyVectorsEnabled)
            assertEquals(0,radio.state.value.diagnostics.textVectorTracks)
        } finally {scope.coroutineContext[Job]!!.cancelAndJoin();db.close();directory.deleteRecursively();context.getSharedPreferences("radio_recommendations",0).edit().clear().commit()}
    }
    @Test fun previewsDoNotConsumeRotationButAcceptedBatchesDo()=runBlocking {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        context.getSharedPreferences("radio_recommendations",0).edit().clear().commit()
        val db=Room.inMemoryDatabaseBuilder(context,LibraryDatabase::class.java).build()
        val scope=CoroutineScope(SupervisorJob()+Dispatchers.IO)
        val songs=(1..100).map{LocalSong("rotation-$it","root","folder","content://fixture/$it","Song $it.mp3","Music",1,1,1,10000)}
        val local=MutableStateFlow(LibraryState(songs=songs,loading=false))
        try {
            val journal=ListeningJournalRepository(context,db,scope)
            val radio=RadioRepository(context,db,local,journal,scope)
            withTimeout(10000){radio.state.first{it.loaded && !it.busy}}
            val first=radio.recommendations()!!
            assertEquals(first.suggestions,radio.recommendations()!!.suggestions)
            assertEquals(100,radio.state.value.diagnostics.eligibleTracks)
            assertEquals(0,radio.state.value.diagnostics.textVectorTracks)
            assertTrue(radio.state.value.bootstrapCandidates.isEmpty())
            assertFalse(radio.committed(first,emptySet()))
            val accepted=first.suggestions.take(2).map{it.song.id}.toSet()
            assertTrue(radio.committed(first,accepted))
            assertFalse(radio.committed(first))
            val saved=org.json.JSONArray(context.getSharedPreferences("radio_recommendations",0).getString("recentSelections","[]"))
            assertEquals(accepted,(0 until saved.length()).map{saved.getString(it)}.toSet())
            val next=radio.recommendations()!!
            assertTrue(accepted.intersect(next.suggestions.map{it.song.id}.toSet()).isEmpty())
            radio.updatePlayback(PlaybackState(queue=songs.take(2),playOrder=songs.take(2).map{it.id},currentId=songs.first().id,queueRevision=1))
            assertFalse(radio.valid(next.token))
            val batch=radio.recommendations()!!
            radio.updatePlayback(PlaybackState(queue=songs.take(3),playOrder=songs.take(3).map{it.id},currentId=songs.first().id,queueRevision=2))
            assertFalse(radio.valid(batch.token))
            assertTrue(db.journal().events().isEmpty())
            assertTrue(db.library().playbackStarts().isEmpty())
        } finally {scope.coroutineContext[Job]!!.cancelAndJoin();db.close();context.getSharedPreferences("radio_recommendations",0).edit().clear().commit()}
    }
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
            assertEquals(setOf(song.id),radio.state.value.bootstrapCandidates)
            assertFalse(radio.state.value.diagnostics.hasUsableTaste)
            var proof=radio.recommendations()!!
            radio.setBootstrap(setOf(song.id,"missing"))
            assertFalse(radio.valid(proof.token))
            withTimeout(10000){radio.state.first{!it.busy && it.bootstrapLocalIds==setOf(song.id)}}
            assertTrue(db.journal().events().isEmpty())
            assertTrue(db.journal().allFeedback().isEmpty())
            proof=radio.recommendations()!!
            radio.clearBootstrap()
            assertFalse(radio.valid(proof.token))
            radio.dismissBootstrap()
            assertTrue(radio.state.value.bootstrapDismissed)
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
