package dev.radio.mobile.connection

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.CancellationException
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.KeyStore
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.time.Instant
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.spec.GCMParameterSpec

data class DiscoveryConfig(val projectId:String, val apiKey:String, val email:String) {
    override fun toString()="DiscoveryConfig(redacted)"
}
data class DiscoveryState(val projectId:String="", val apiKey:String="", val email:String="", val signedIn:Boolean=false, val busy:Boolean=false, val message:String="Discovery is not configured")
internal data class DiscoveryDocument(val serverId:String,val generation:Long,val revision:Long,val available:Boolean,val endpoint:String,val issuedAt:Long,val expiresAt:Long,val payload:String,val signature:String,val publicKey:String,val updatedAt:String)
internal data class DiscoverySignedFields(val serverId:String,val generation:Long,val revision:Long,val available:Boolean,val endpoint:String,val issuedAt:Long,val expiresAt:Long)
internal class DiscoveryHttpFailure(val status:Int):Exception()
internal fun validateDiscoveryFields(d:DiscoveryDocument, p:DiscoverySignedFields, expectedServer:String, now:Long):DiscoveryDocument {
    val updated=try{Instant.parse(d.updatedAt).epochSecond}catch(_:Exception){throw ConnectionFailure("Discovery record has an invalid freshness timestamp.")}
    if(d.serverId!=expectedServer || d.generation<1 || d.revision<1 || d.expiresAt<=d.issuedAt || d.expiresAt<=now || d.issuedAt>now+60 || d.issuedAt<now-300 || d.expiresAt-d.issuedAt>300 || updated>now+60 || kotlin.math.abs(updated-d.issuedAt)>60)
        throw ConnectionFailure("Discovery record is stale or belongs to another server.")
    if(p.serverId!=d.serverId || p.generation!=d.generation || p.revision!=d.revision || p.available!=d.available || p.endpoint!=d.endpoint || p.issuedAt!=d.issuedAt || p.expiresAt!=d.expiresAt)
        throw ConnectionFailure("Discovery metadata does not match its signed descriptor.")
    return d
}
internal fun discoveryReadAllowed(now:Long,lastRead:Long,lastSessionRead:Long,error:Boolean,session:Boolean)=
    now-lastRead >= (if(error) 15 else 60) && (!session || now-lastSessionRead>=60)
internal fun discoveryHttpFailure(status:Int)=ConnectionFailure(if(status==429)"Firebase discovery quota or rate limit reached." else "Firebase discovery request failed.",rateLimited=status==429)
internal fun validateDiscoveryEndpoint(endpoint:String){
    if(!endpoint.matches(Regex("https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.trycloudflare\\.com")))
        throw ConnectionFailure("Discovery endpoint is not an exact HTTPS Quick Tunnel origin.")
}
internal fun parseDiscoveryDocument(text:String, expectedServer:String, now:Long):DiscoveryDocument {
    if(text.length>24576) throw ConnectionFailure("Discovery document is too large.")
    val f=json(text)
    val d=DiscoveryDocument(f.text("server_id",128),f.number("connection_generation"),f.number("publication_revision"),
        f.opt("available") as? Boolean ?: throw ConnectionFailure("Invalid discovery availability."), f.text("endpoint",2048),
        f.number("issued_at"),f.number("expires_at"),f.text("payload",12000),f.text("signature",128),f.text("public_key",512),f.text("updated_at",64))
    val payloadBytes=WireBase64.decode(d.payload,8192)
    val payloadText=try { Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(payloadBytes)).toString() }
        catch(_:Exception){throw ConnectionFailure("Invalid signed discovery payload.")}
    val payload=json(payloadText)
    val validated=validateDiscoveryFields(d,DiscoverySignedFields(payload.text("server_id",128),payload.number("connection_generation"),payload.number("publication_revision"),payload.opt("available") as? Boolean ?: throw ConnectionFailure("Invalid server response."),payload.text("endpoint",2048),payload.number("issued_at"),payload.number("expires_at")),expectedServer,now)
    validateDiscoveryEndpoint(validated.endpoint)
    return validated
}

/** Firebase REST adapter. Only refresh credential is retained, encrypted with this install's Keystore key. */
class DiscoveryRepository(context:Context, private val pairing:PairingRepository, private val now:()->Long={System.currentTimeMillis()/1000}, private val request:(String,String?,String?)->RadioResponse={url,body,token->firebaseRequest(url,body,token)}) {
    private val app=context.applicationContext
    private val prefs=app.getSharedPreferences("radio_discovery_config",Context.MODE_PRIVATE)
    private val file=AtomicFile(File(app.filesDir,"mobile_credentials/discovery.enc"))
    private val alias="radio.mobile.discovery.refresh.v1"
    private val mutex=sharedMutex
    @Volatile private var lastRead=prefs.getLong("last_read_at",0)
    @Volatile private var lastSessionRead=prefs.getLong("last_session_read_at",0)
    private val _state=kotlinx.coroutines.flow.MutableStateFlow(DiscoveryState())
    val state=_state
    suspend fun load()=withContext(Dispatchers.IO){mutex.withLock{
        val signed=readRefresh()!=null
        val message=when{signed->"Signed in to Firebase discovery";prefs.getString("email",null)!=null->"Firebase reader credential expired or lost. Sign in again.";else->"Configure Firebase discovery"}
        _state.value=DiscoveryState(prefs.getString("projectId","")?:"",prefs.getString("apiKey","")?:"",prefs.getString("email","")?:"",signedIn=signed,message=message)
    }}
    suspend fun configure(project:String,apiKeyInput:String){
        if(!project.matches(Regex("[a-z0-9-]{5,100}"))||apiKeyInput.length !in 20..256){_state.value=_state.value.copy(message="Enter a valid Firebase project ID and Web API key.");return}
        try { withContext(Dispatchers.IO){mutex.withLock{val changed=prefs.getString("projectId","")!=project||prefs.getString("apiKey","")!=apiKeyInput;if(changed){file.delete();runCatching{deleteKey()};if(file.baseFile.exists()&&runCatching{key(false)!=null}.getOrDefault(true))throw ConnectionFailure("Could not safely replace the saved Firebase credential.");prefs.edit().remove("email").remove("reader_uid").putBoolean("quota_blocked",false).commit()};prefs.edit().putString("projectId",project).putString("apiKey",apiKeyInput).commit()}};load()
        }catch(cancelled:CancellationException){throw cancelled}catch(_:Exception){_state.value=_state.value.copy(message="Could not safely replace Firebase configuration. The previous credentials remain protected.")}
    }
    suspend fun signIn(email:String,password:String){ withContext(Dispatchers.IO){ mutex.withLock {
        val c=config() ?: run{_state.value=_state.value.copy(message="Configure Firebase project and API key first.");return@withLock}
        _state.value=_state.value.copy(busy=true,message="Signing in to Firebase discovery…")
        try { val result=request("https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${enc(c.apiKey)}",JSONObject().put("email",email).put("password",password).put("returnSecureToken",true).toString(),null)
            checkHttp(result);currentCoroutineContext().ensureActive();val j=json(result.body); val refresh=j.text("refreshToken",4096);val uid=j.text("localId",128);saveRefresh(refresh);prefs.edit().putString("email",email).putString("reader_uid",uid).putBoolean("quota_blocked",false).putBoolean("auth_blocked",false).commit();_state.value=DiscoveryState(c.projectId,c.apiKey,email,true,false,"Signed in to Firebase discovery")
        } catch(cancelled:CancellationException){_state.value=_state.value.copy(busy=false);throw cancelled
        } catch(e:Exception){_state.value=_state.value.copy(busy=false,message="Firebase sign-in failed. Check the project, API key and reader account.")}
    } } }
    suspend fun signOut(){withContext(Dispatchers.IO){mutex.withLock{file.delete();runCatching{deleteKey()};prefs.edit().remove("reader_uid").commit();_state.value=_state.value.copy(signedIn=false,message=if(readRefresh()==null)"Signed out of Firebase discovery" else "Could not remove the saved Firebase credential. Retry sign out.")}}}
    suspend fun readOnResume()=read(false)
    suspend fun readAfterConnectionError()=read(true)
    suspend fun readExplicitCheck()=read(true,explicit=true)
    suspend fun readDuringSession()=read(false,true)
    private suspend fun read(error:Boolean,session:Boolean=false,explicit:Boolean=false)=withContext(Dispatchers.IO){ mutex.withLock {
        val t=now();lastRead=prefs.getLong("last_read_at",lastRead);lastSessionRead=prefs.getLong("last_session_read_at",lastSessionRead)
        if(prefs.getBoolean("quota_blocked",false)&&!explicit){_state.value=_state.value.copy(busy=false,message="Firebase discovery quota limit reached. Use Check discovery after the quota resets.");return@withLock}
        if(prefs.getBoolean("auth_blocked",false)&&!explicit){_state.value=_state.value.copy(busy=false,message="Firebase discovery access is blocked. Use Check discovery after updating Firebase rules.");return@withLock}
        if(explicit)prefs.edit().putBoolean("quota_blocked",false).apply()
        if(explicit)prefs.edit().putBoolean("auth_blocked",false).apply()
        if(!discoveryReadAllowed(t,lastRead,lastSessionRead,error,session)) return@withLock
        val c=config()?:return@withLock; val refresh=readRefresh()?:return@withLock
        _state.value=_state.value.copy(busy=true)
        try {
            val token=refresh(refresh,c)
            currentCoroutineContext().ensureActive()
            val server=pairing.state.value.serverId ?: throw ConnectionFailure("Pair this phone before using discovery.")
            val url="https://firestore.googleapis.com/v1/projects/${enc(c.projectId)}/databases/(default)/documents/discovery/${enc(server)}"
            markAttempt(t,session)
            val response=request(url,null,token);checkHttp(response)
            currentCoroutineContext().ensureActive()
            val doc=decodeFirestore(response.body)
            val d=parseDiscoveryDocument(doc,server,now())
            val envelope=JSONObject().put("payload",d.payload).put("signature",d.signature).put("public_key",d.publicKey).toString()
            // PairingRepository verifies signature+identity and commits revision before bearer use.
            pairing.updateConnection(envelope)
            if(pairing.state.value.error) throw ConnectionFailure(pairing.state.value.message)
            _state.value=_state.value.copy(busy=false,message="Discovery checked · ${if(d.available)"server advertised" else "server offline"}")
        } catch(e:CancellationException){_state.value=_state.value.copy(busy=false);throw e}
        catch(e:Exception){if(lastRead<t)markAttempt(t,session);val failure=e as? ConnectionFailure;val http=e as? DiscoveryHttpFailure
            if(failure?.rateLimited==true||http?.status==429)prefs.edit().putBoolean("quota_blocked",true).apply()
            if(http?.status==401)clearReaderCredential()
            if(http?.status==403)prefs.edit().putBoolean("auth_blocked",true).apply()
            val message=when(http?.status){400,401->"Firebase reader access expired. Sign in again.";403->"Firebase denied the reader account. Check its document read rule, then use Check discovery.";429->"Firebase discovery quota or rate limit reached.";else->failure?.safeMessage?:"Discovery check failed"}
            _state.value=_state.value.copy(busy=false,message=message)
        }
    } }
    private fun markAttempt(t:Long,session:Boolean){lastRead=t;prefs.edit().putLong("last_read_at",t).apply();if(session){lastSessionRead=t;prefs.edit().putLong("last_session_read_at",t).apply()}}
    private fun decodeFirestore(text:String):String {
        val fields=json(text).getJSONObject("fields")
        val allowed=setOf("server_id","connection_generation","publication_revision","available","endpoint","issued_at","expires_at","payload","signature","public_key","updated_at")
        val keys=fields.keys().asSequence().toSet()
        if(keys!=allowed)throw ConnectionFailure("Discovery document has unexpected fields.")
        fun value(n:String)=fields.getJSONObject(n)
        fun typed(n:String,k:String)=value(n).also{if(it.length()!=1 || !it.has(k))throw ConnectionFailure("Discovery document has invalid field types.")}
        fun s(n:String)=typed(n,"stringValue").opt("stringValue") as? String ?: throw ConnectionFailure("Discovery document has invalid string fields.")
        fun i(n:String)=(typed(n,"integerValue").opt("integerValue") as? String)?.takeIf{it.matches(Regex("-?(0|[1-9][0-9]{0,18})"))}?.toLongOrNull()?:throw ConnectionFailure("Discovery document has invalid integer fields.")
        fun b(n:String)=typed(n,"booleanValue").opt("booleanValue") as? Boolean ?: throw ConnectionFailure("Discovery document has invalid boolean fields.")
        val timestamp=typed("updated_at","timestampValue").opt("timestampValue") as? String ?: throw ConnectionFailure("Discovery document has invalid timestamp field.")
        try{Instant.parse(timestamp)}catch(_:Exception){throw ConnectionFailure("Discovery document has invalid freshness timestamp.")}
        return JSONObject().put("server_id",s("server_id")).put("connection_generation",i("connection_generation")).put("publication_revision",i("publication_revision"))
            .put("available",b("available")).put("endpoint",s("endpoint")).put("issued_at",i("issued_at")).put("expires_at",i("expires_at"))
            .put("payload",s("payload")).put("signature",s("signature")).put("public_key",s("public_key")).put("updated_at",timestamp).toString()
    }
    private fun config()=DiscoveryConfig(prefs.getString("projectId","")?:"",prefs.getString("apiKey","")?:"",prefs.getString("email","")?:"").takeIf{it.projectId.isNotEmpty()&&it.apiKey.isNotEmpty()}
    private fun refresh(refresh:String,c:DiscoveryConfig):String { val r=request("https://securetoken.googleapis.com/v1/token?key=${enc(c.apiKey)}", "grant_type=refresh_token&refresh_token=${enc(refresh)}",null);try{checkHttp(r)}catch(e:DiscoveryHttpFailure){if(e.status in setOf(400,401,403))clearReaderCredential();throw e};val j=json(r.body);if(j.text("user_id",128)!=prefs.getString("reader_uid",null)){clearReaderCredential();throw ConnectionFailure("Firebase reader identity changed. Sign in again.")};val next=j.text("refresh_token",4096);saveRefresh(next);return j.text("id_token",4096) }
    private fun key(create:Boolean):javax.crypto.SecretKey? { val ks=KeyStore.getInstance("AndroidKeyStore").apply{load(null)}; (ks.getKey(alias,null) as? javax.crypto.SecretKey)?.let{return it};if(!create)return null;return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore").run{init(KeyGenParameterSpec.Builder(alias,KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setKeySize(256).build());generateKey()} }
    private fun deleteKey(){val ks=KeyStore.getInstance("AndroidKeyStore").apply{load(null)};if(ks.containsAlias(alias))ks.deleteEntry(alias);if(ks.containsAlias(alias))throw ConnectionFailure("Could not remove the Firebase encryption key.")}
    private fun aad()="radio-mobile-discovery-v1:${prefs.getString("projectId","")}:${prefs.getString("apiKey","")}".toByteArray(Charsets.UTF_8)
    private fun saveRefresh(s:String){val c=Cipher.getInstance("AES/GCM/NoPadding");c.init(Cipher.ENCRYPT_MODE,key(true));c.updateAAD(aad());val out=byteArrayOf(1)+c.iv+c.doFinal(s.toByteArray());file.baseFile.parentFile?.mkdirs();val f=file.startWrite();try{f.write(out);file.finishWrite(f)}catch(e:Exception){file.failWrite(f);throw e}}
    private fun readRefresh():String?=try{val b=file.readFully();if(b.size<30||b[0]!=1.toByte())null else Cipher.getInstance("AES/GCM/NoPadding").run{init(Cipher.DECRYPT_MODE,key(false),GCMParameterSpec(128,b.copyOfRange(1,13)));updateAAD(aad());doFinal(b.copyOfRange(13,b.size)).toString(Charsets.UTF_8)}}catch(_:Exception){null}
    private fun checkHttp(r:RadioResponse){if(r.status !in 200..299)throw DiscoveryHttpFailure(r.status)}
    private fun clearReaderCredential(){file.delete();runCatching{deleteKey()};prefs.edit().remove("reader_uid").commit();_state.value=_state.value.copy(signedIn=false,message="Firebase reader credential expired or revoked. Sign in again.")}
    private fun enc(s:String)=java.net.URLEncoder.encode(s,"UTF-8")
    companion object { private val sharedMutex=Mutex() }
}
private fun firebaseRequest(url:String,body:String?,token:String?):RadioResponse {
    val c=URL(url).openConnection() as HttpURLConnection
    try {
        c.connectTimeout=8000;c.readTimeout=8000;c.instanceFollowRedirects=false;c.requestMethod=if(body==null)"GET" else "POST"
        if(token!=null)c.setRequestProperty("Authorization","Bearer $token")
        if(body!=null){c.doOutput=true;c.setRequestProperty("Content-Type",if(url.contains("securetoken"))"application/x-www-form-urlencoded" else "application/json");c.outputStream.use{it.write(body.toByteArray())}}
        val status=c.responseCode;val stream=if(status<400)c.inputStream else c.errorStream
        val data=stream?.use{it.readNBytes(32769)}?:ByteArray(0)
        if(data.size>32768)throw ConnectionFailure("Firebase response is too large.")
        return RadioResponse(status,data.toString(Charsets.UTF_8))
    } finally { c.disconnect() }
}
