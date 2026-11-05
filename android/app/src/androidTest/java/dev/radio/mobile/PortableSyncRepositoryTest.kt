package dev.radio.mobile

import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import dev.radio.mobile.connection.*
import dev.radio.mobile.data.*
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.Signature
import java.util.Base64
import java.util.UUID
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class PortableSyncRepositoryTest {
    @Test fun lostAckRetriesIdenticalEventsThenPersistsPageCursorAndLamportClock() = runBlocking {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        val db=Room.inMemoryDatabaseBuilder(context,LibraryDatabase::class.java).addMigrations(
            LIBRARY_MIGRATION_1_2,LIBRARY_MIGRATION_2_3,LIBRARY_MIGRATION_3_4,LIBRARY_MIGRATION_4_5,
            LIBRARY_MIGRATION_5_6,LIBRARY_MIGRATION_6_7,LIBRARY_MIGRATION_7_8,LIBRARY_MIGRATION_8_9).build()
        val identity=identityFixture()
        val event=JSONObject().put("protocol_version",1).put("device_id","device-a").put("event_id","retry-event")
            .put("logical_clock",7).put("occurred_at",1000).put("kind","favorite_changed").put("song_id",JSONObject.NULL).put("local_id","private-song").put("value",true).toString()
        db.journal().insert(LocalListeningEvent("retry-event","private-song",null,"favorite_changed",1_000_000,7,event,0,null,null,"paired"))
        db.journal().enqueue(LocalEventOutbox("retry-event","server-a","device-a",event))
        db.journal().clock(LocalJournalClock(value=7))
        val store=MemoryPairingStore(identity.record)
        val transport=LostAckTransport(identity.keys,event)
        val pairing=PairingRepository(store,transport,EndpointPolicy(true),now={1000L})
        val repo=PortableSyncRepository(context,pairing,db)
        try {
            repo.syncNow()
            assertNotNull(repo.state.value.error)
            assertEquals(1,repo.state.value.pendingEvents)
            assertEquals(0L,db.journal().syncCursor("server-a","device-a")?.cursor ?: 0L)
            repo.syncNow()
            assertNull(repo.state.value.error)
            assertEquals(0,repo.state.value.pendingEvents)
            val receipt=db.journal().outbox().single()
            assertTrue(receipt.acknowledged)
            assertEquals(2,transport.postBodies.size)
            assertEquals("Lost acknowledgement must resend the exact event envelope.",transport.postBodies[0],transport.postBodies[1])
            assertEquals(1,db.journal().remoteEvents("server-a").size)
            assertEquals(1L,db.journal().syncCursor("server-a","device-a")!!.cursor)
            assertEquals(120L,db.journal().clock())
            assertEquals("favorite_changed:local:[\"device-a\",\"private-song\"]",db.journal().profileRegisters("server-a").single().registerKey)
            assertTrue(db.journal().syncCursor("server-a","device-a")!!.lastSuccessAt!! > 0)
        } finally { repo.close(); db.close() }
    }

    @Test fun incompleteAcknowledgementNeverDeletesOrAcknowledgesTheOutbox() = runBlocking {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        val db=Room.inMemoryDatabaseBuilder(context,LibraryDatabase::class.java).build()
        val identity=identityFixture()
        val event=JSONObject().put("protocol_version",1).put("device_id","device-a").put("event_id","ack-event")
            .put("logical_clock",1).put("occurred_at",1000).put("kind","favorite_changed").put("song_id",JSONObject.NULL).put("local_id","private-song").put("value",true).toString()
        db.journal().enqueue(LocalEventOutbox("ack-event","server-a","device-a",event))
        val transport=LostAckTransport(identity.keys,event,malformedAck=true)
        val repo=PortableSyncRepository(context,PairingRepository(MemoryPairingStore(identity.record),transport,EndpointPolicy(true),now={1000L}),db)
        try {
            repo.syncNow()
            assertNotNull(repo.state.value.error)
            assertFalse(db.journal().outbox().single().acknowledged)
            assertEquals(event,db.journal().outbox().single().payload)
        } finally { repo.close(); db.close() }
    }

    @Test fun largeOfflineOutboxSplitsAtFiveHundredAndKeepsAllEventIds() = runBlocking {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        val db=Room.inMemoryDatabaseBuilder(context,LibraryDatabase::class.java).build()
        val identity=identityFixture()
        val events=(1..1001).map { index -> JSONObject().put("protocol_version",1).put("device_id","device-a").put("event_id","bulk-$index")
            .put("logical_clock",index).put("occurred_at",1000).put("kind","song_replayed").put("song_id","17").put("catalog_id","catalog-a").put("attempt_id","attempt-$index").toString() }
        events.forEachIndexed { index,payload -> db.journal().enqueue(LocalEventOutbox("bulk-${index+1}","server-a","device-a",payload)) }
        val transport=LostAckTransport(identity.keys,events.first(),loseFirstAck=false)
        val repo=PortableSyncRepository(context,PairingRepository(MemoryPairingStore(identity.record),transport,EndpointPolicy(true),now={1000L}),db)
        try {
            repo.syncNow()
            assertNull(repo.state.value.error)
            assertEquals(0,repo.state.value.pendingEvents)
            assertEquals(3,transport.postBodies.size)
            assertEquals(listOf(500,500,1),transport.postBodies.map { JSONObject(it).getJSONArray("events").length() })
            assertEquals(1001,db.journal().remoteEvents("server-a").size)
            assertTrue(db.journal().outbox().all { it.acknowledged })
        } finally { repo.close(); db.close() }
    }

    @Test fun interruptedSecondPageKeepsFirstPageStateAndDoesNotClaimSuccess() = runBlocking {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        val db=Room.inMemoryDatabaseBuilder(context,LibraryDatabase::class.java).build()
        val identity=identityFixture()
        val events=(1..101).map { index -> JSONObject().put("protocol_version",1).put("device_id","desktop")
            .put("event_id",if(index==1)"remote-😀" else "remote-$index").put("logical_clock",index).put("occurred_at",if(index==1)1000.125 else 1000).put("kind",if(index==1)"favorite_changed" else "song_replayed")
            .put("song_id","17").put("catalog_id","catalog-a").apply {
                if(index==1) put("value",true) else put("attempt_id","remote-attempt-$index")
            }.toString() }
        val transport=LostAckTransport(identity.keys,initialEvents=events,failAtCursor=100)
        val repo=PortableSyncRepository(context,PairingRepository(MemoryPairingStore(identity.record),transport,EndpointPolicy(true),now={1000L}),db)
        try {
            repo.syncNow()
            assertNotNull(repo.state.value.error)
            assertEquals(100L,db.journal().syncCursor("server-a","device-a")!!.cursor)
            assertNull(db.journal().syncCursor("server-a","device-a")!!.lastSuccessAt)
            assertEquals(120L,db.journal().clock())
            assertEquals(100,db.journal().remoteEvents("server-a").size)
            assertEquals("true",db.journal().profileRegisters("server-a").single().valueJson)
            assertEquals(1_000_125L,db.journal().remoteEvents("server-a").first { it.eventId=="remote-😀" }.occurredAt)
            repo.syncNow()
            assertNull(repo.state.value.error)
            assertEquals(101L,db.journal().syncCursor("server-a","device-a")!!.cursor)
            assertNotNull(db.journal().syncCursor("server-a","device-a")!!.lastSuccessAt)
            assertEquals(101,db.journal().remoteEvents("server-a").size)
        } finally { repo.close(); db.close() }
    }

    @Test fun eventsBelongingToAnotherServerOrDeviceAreNeverUploaded() = runBlocking {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        val db=Room.inMemoryDatabaseBuilder(context,LibraryDatabase::class.java).build()
        val identity=identityFixture()
        val oldEvent=JSONObject().put("protocol_version",1).put("device_id","old-device").put("event_id","old-event")
            .put("logical_clock",9).put("occurred_at",1000).put("kind","favorite_changed").put("song_id",JSONObject.NULL).put("local_id","old-local").put("value",true).toString()
        db.journal().enqueue(LocalEventOutbox("old-event","server-b","old-device",oldEvent))
        val transport=LostAckTransport(identity.keys)
        val repo=PortableSyncRepository(context,PairingRepository(MemoryPairingStore(identity.record),transport,EndpointPolicy(true),now={1000L}),db)
        try {
            repo.syncNow()
            assertNull(repo.state.value.error)
            assertTrue(transport.postBodies.isEmpty())
            assertEquals(oldEvent,db.journal().outbox().single().payload)
            assertFalse(db.journal().outbox().single().acknowledged)
        } finally { repo.close(); db.close() }
    }

    private data class IdentityFixture(val keys:KeyPair,val record:PairingRecord)
    private fun identityFixture():IdentityFixture {
        val keys=KeyPairGenerator.getInstance("EC").apply { initialize(java.security.spec.ECGenParameterSpec("secp256r1")) }.generateKeyPair()
        val pin=DescriptorSignatures.pin(keys.public.encoded)
        val bundle=PairingBundle("server-a",pin,"http://127.0.0.1:18766","ticket","secret","Phone",2000)
        val record=PairingRecord(bundle,WireBase64.encode(ByteArray(32){it.toByte()}),UUID.randomUUID().toString(),WireBase64.encode(ByteArray(32){(it+1).toByte()}),"device-a",2000)
        return IdentityFixture(keys,record)
    }

    private class MemoryPairingStore(private var record:PairingRecord):PairingStore {
        override fun load()=StoredPairing(record)
        override fun installationMarker()=record.installationMarker
        override fun save(record:PairingRecord){this.record=record}
        override fun forget(){ }
    }

    private class LostAckTransport(private val keys:KeyPair,private val localEvent:String?=null,private val loseFirstAck:Boolean=true,private val malformedAck:Boolean=false,
        initialEvents:List<String> = emptyList(),private var failAtCursor:Long?=null):RadioTransport {
        val postBodies=mutableListOf<String>()
        private val storedEvents=initialEvents.toMutableList()
        private var postCount=0
        override suspend fun request(endpoint:String,path:String,body:String?,bearer:String?):RadioResponse = when {
            path=="/identity" -> identity()
            path=="/capabilities" -> RadioResponse(200,JSONObject().put("protocol_version",1).put("server_id","server-a").put("authenticated_device_id","device-a").put("profile_sync",true).put("scopes",JSONArray(listOf("catalog:read","profile:read","profile:write"))).toString())
            path=="/events" -> {
                val request=requireNotNull(body);postBodies+=request;postCount++
                val submitted=JSONObject(request).getJSONArray("events")
                val fresh=mutableListOf<String>()
                for(index in 0 until submitted.length()) {
                    val event=submitted.getJSONObject(index);val id=event.getString("event_id")
                    if(storedEvents.none { JSONObject(it).getString("event_id")==id }) {storedEvents+=event.toString();fresh+=id}
                }
                when { loseFirstAck && postCount==1 -> RadioResponse(500,"{}"); malformedAck -> RadioResponse(200,"{\"protocol_version\":1,\"accepted\":[\"wrong-id\"],\"duplicates\":[]}")
                    else -> RadioResponse(200,JSONObject().put("protocol_version",1).put("accepted",JSONArray(fresh)).put("duplicates",JSONArray((0 until submitted.length()).map { submitted.getJSONObject(it).getString("event_id") }.filter { it !in fresh })).put("cursor",999).toString()) }
            }
            path.startsWith("/events?cursor=") -> {
                val cursor=path.substringAfter("cursor=").substringBefore('&').toLong()
                if(failAtCursor==cursor){failAtCursor=null;RadioResponse(500,"{}")} else {
                    val page=JSONArray();val start=cursor.toInt().coerceAtLeast(0);val end=minOf(start+100,storedEvents.size)
                    for(index in start until end) page.put(JSONObject().put("revision",index+1).put("event",JSONObject(storedEvents[index])))
                    val next=if(end>start)end.toLong() else cursor
                    RadioResponse(200,JSONObject().put("protocol_version",1).put("events",page).put("cursor",next).put("has_more",end<storedEvents.size).put("logical_clock",maxOf(120,storedEvents.size)).toString())
                }
            }
            else -> error("Unexpected path $path")
        }
        private fun identity():RadioResponse {
            val payload=JSONObject().put("protocol_version",1).put("server_id","server-a").put("endpoint","http://127.0.0.1:18766").put("connection_generation",1).put("publication_revision",1).put("issued_at",1000).put("expires_at",1300).put("available",true).toString().toByteArray()
            val b64=Base64.getUrlEncoder().withoutPadding()
            val signature=Signature.getInstance("SHA256withECDSA").run{initSign(keys.private);update(payload);sign()}
            return RadioResponse(200,JSONObject().put("protocol_version",1).put("public_key",b64.encodeToString(keys.public.encoded)).put("payload",b64.encodeToString(payload)).put("signature",b64.encodeToString(signature)).toString())
        }
    }
}
