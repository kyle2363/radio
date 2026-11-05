package dev.radio.mobile

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import dev.radio.mobile.connection.*
import java.io.File
import java.security.KeyPairGenerator
import java.security.Signature
import java.util.Base64
import java.security.KeyStore
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.After
import org.junit.Test

class DiscoveryRepositoryTest {
    @After fun cleanDiscoverySecrets(){
        val context=ApplicationProvider.getApplicationContext<Context>()
        context.getSharedPreferences("radio_discovery_config",Context.MODE_PRIVATE).edit().clear().commit()
        File(context.filesDir,"mobile_credentials/discovery.enc").delete()
        runCatching{KeyStore.getInstance("AndroidKeyStore").apply{load(null);deleteEntry("radio.mobile.discovery.refresh.v1")}}
    }
    private class MemoryStore(var value:StoredPairing):PairingStore {
        override fun load()=value
        override fun installationMarker()="install-marker"
        override fun save(record:PairingRecord){value=StoredPairing(record)}
        override fun forget(){value=StoredPairing()}
    }
    private fun firestore(envelope:String, server:String, generation:Long, revision:Long, available:Boolean, endpoint:String, issued:Long, expires:Long, updated:Long):String {
        val env=JSONObject(envelope);val fields=JSONObject()
            .put("server_id",JSONObject().put("stringValue",server))
            .put("connection_generation",JSONObject().put("integerValue",generation.toString()))
            .put("publication_revision",JSONObject().put("integerValue",revision.toString()))
            .put("available",JSONObject().put("booleanValue",available))
            .put("endpoint",JSONObject().put("stringValue",endpoint))
            .put("issued_at",JSONObject().put("integerValue",issued.toString()))
            .put("expires_at",JSONObject().put("integerValue",expires.toString()))
            .put("payload",JSONObject().put("stringValue",env.getString("payload")))
            .put("signature",JSONObject().put("stringValue",env.getString("signature")))
            .put("public_key",JSONObject().put("stringValue",env.getString("public_key")))
            .put("updated_at",JSONObject().put("timestampValue",java.time.Instant.ofEpochSecond(updated).toString()))
        return JSONObject().put("fields",fields).toString()
    }

    @Test fun firebaseReaderFetchVerifiesSignedDiscoveryAndAdoptsRotatedEndpointBeforeBearer()=runBlocking {
        val context=ApplicationProvider.getApplicationContext<Context>()
        val prefs=context.getSharedPreferences("radio_discovery_config",Context.MODE_PRIVATE);prefs.edit().clear().commit()
        val credentials=File(context.filesDir,"mobile_credentials/discovery.enc");credentials.delete()
        val old="https://old.trycloudflare.com";val endpoint="https://new.trycloudflare.com";var now=System.currentTimeMillis()/1000
        val kp=KeyPairGenerator.getInstance("EC").apply{initialize(java.security.spec.ECGenParameterSpec("secp256r1"))}.generateKeyPair();val b64=Base64.getUrlEncoder().withoutPadding()
        val payload="""{"protocol_version":1,"server_id":"server-1","endpoint":"$endpoint","connection_generation":2,"publication_revision":4,"issued_at":$now,"expires_at":${now+240},"available":true}""".toByteArray()
        val envelope=JSONObject().put("public_key",b64.encodeToString(kp.public.encoded)).put("payload",b64.encodeToString(payload)).put("signature",b64.encodeToString(Signature.getInstance("SHA256withECDSA").run{initSign(kp.private);update(payload);sign()})).toString()
        val record=PairingRecord(PairingBundle("server-1",DescriptorSignatures.pin(kp.public.encoded),old,"ticket","A".repeat(43),"phone",now+300),"B".repeat(43),"exchange-id","install-marker","device-1",now+300,1,3)
        val store=MemoryStore(StoredPairing(record));val radioCalls=mutableListOf<Pair<String,String?>>()
        val pairing=PairingRepository(store,RadioTransport{url,path,_,bearer->radioCalls+=path to bearer;when(path){"/identity"->RadioResponse(200,envelope);"/capabilities"->RadioResponse(200,"""{"protocol_version":1,"server_id":"server-1","authenticated_device_id":"device-1"}""");else->error(path)}},EndpointPolicy(false),now={now})
        pairing.load()
        var firestoreBody=firestore(envelope,"server-1",2,4,true,endpoint,now,now+240,now);var firestoreStatus=200;var authStatus=200;var refreshStatus=200
        val firebaseCalls=mutableListOf<String>()
        val discovery=DiscoveryRepository(context,pairing,now={now}) { url,_,_->firebaseCalls+=url;when {
            url.contains("accounts:signInWithPassword")->if(authStatus==200)RadioResponse(200,"""{"refreshToken":"refresh-secret","localId":"reader-uid"}""") else RadioResponse(authStatus,"{}")
            url.contains("securetoken.googleapis.com")->if(refreshStatus==200)RadioResponse(200,"""{"user_id":"reader-uid","refresh_token":"refresh-next","id_token":"firebase-id-token"}""") else RadioResponse(refreshStatus,"{}")
            url.contains("firestore.googleapis.com")->RadioResponse(firestoreStatus,firestoreBody)
            else->error("Unexpected Firebase URL")
        } }
        discovery.configure("x","x")
        assertTrue(discovery.state.value.message.contains("valid Firebase"))
        discovery.configure("radio-project","A".repeat(32));discovery.signIn("reader@example.test","password-only-in-memory")
        discovery.readOnResume()
        assertEquals("https://new.trycloudflare.com",store.value.record?.bundle?.endpoint)
        assertEquals(2L,store.value.record?.generation);assertEquals(4L,store.value.record?.revision)
        assertEquals(listOf("/identity" to null,"/capabilities" to "B".repeat(43)),radioCalls)
        assertTrue(firebaseCalls.last().contains("documents/discovery/server-1"))
        assertFalse(credentials.readBytes().toString(Charsets.ISO_8859_1).contains("refresh-secret"))
        assertFalse(credentials.readBytes().toString(Charsets.ISO_8859_1).contains("password-only-in-memory"))
        val count=radioCalls.size
        now+=61
        val forged=envelope.replace(Regex("\"signature\":\"[^\"]+\""),"\"signature\":\"AAAA\"")
        firestoreBody=firestore(forged,"server-1",2,4,true,endpoint,now-61,now+179,now-61)
        discovery.readOnResume()
        assertEquals(count,radioCalls.size)
        assertEquals(endpoint,store.value.record?.bundle?.endpoint)
        assertTrue(discovery.state.value.message.contains("signature",true))
        now+=61;firestoreStatus=429
        discovery.readExplicitCheck()
        val countAfterQuota=firebaseCalls.size
        now+=61 // Beyond the ordinary throttle: only the quota stop may suppress this read.
        discovery.readOnResume()
        assertEquals(countAfterQuota,firebaseCalls.size)
        now+=61;firestoreStatus=200;refreshStatus=400
        discovery.readExplicitCheck()
        assertFalse(discovery.state.value.signedIn)
        assertFalse(credentials.exists())
        discovery.signOut()
        assertTrue(prefs.getString("reader_uid",null)==null)
        authStatus=400;discovery.signIn("reader@example.test","bad-password")
        assertFalse(discovery.state.value.signedIn)
        assertTrue(discovery.state.value.message.contains("sign-in failed",true))
        assertTrue(count>=2)
        assertTrue(credentials.delete()||!credentials.exists());prefs.edit().clear().commit()
        Unit
    }
}
