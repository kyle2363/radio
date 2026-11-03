package dev.radio.mobile.portable

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import androidx.room.withTransaction
import dev.radio.mobile.data.*
import java.security.MessageDigest
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

data class MatchingState(val loaded:Boolean=false,val busy:Boolean=false,val progress:String="",val results:List<MatchResult> = emptyList(),val error:String?=null)

/** All reads are explicit and local. Never writes source bytes, tags or playback state. */
class MatchingRepository internal constructor(private val context:Context,private val db:LibraryDatabase,private val beforeRead:suspend () -> Unit = {}) {
 private val mutable=MutableStateFlow(MatchingState()); val state=mutable.asStateFlow()
 private val mutex=Mutex(); private var job:Job?=null
 private var snapshot:PortableCatalogRevision?=null
 private var anchors:Map<String,LocalContentHash> = emptyMap()
 fun cancel(){job?.cancel()}
 suspend fun load()=withContext(Dispatchers.IO){mutex.withLock{try{refresh(false)}catch(c:CancellationException){throw c}catch(_:Exception){mutable.value=MatchingState(loaded=true,error="Could not read saved matching data. Import or scan again.")}}}
 suspend fun matchLocalSongs()=withContext(Dispatchers.IO){
  if(!mutex.tryLock()) return@withContext
  job=currentCoroutineContext()[Job]
  try {refresh(true)} catch(cancelled:CancellationException){mutable.value=mutable.value.copy(busy=false,progress="Matching cancelled. Completed hashes are cached.",results=emptyList()); throw cancelled}
  catch(_:Exception){mutable.value=MatchingState(loaded=true,error="Could not read local songs. Check folder access and retry.")}
  finally{job=null;mutable.value=mutable.value.copy(busy=false);mutex.unlock()}
 }
 private data class Fingerprint(val size:Long?,val modified:Long?)
 private fun fingerprint(song:LocalSong):Fingerprint? = try {
  context.contentResolver.query(Uri.parse(song.uri), arrayOf(DocumentsContract.Document.COLUMN_SIZE,DocumentsContract.Document.COLUMN_LAST_MODIFIED),null,null,null)?.use { cursor ->
   if(!cursor.moveToFirst()) null else Fingerprint(if(cursor.isNull(0)) null else cursor.getLong(0),if(cursor.isNull(1)) null else cursor.getLong(1))
  }
 } catch(_:Exception){null}
 private fun valid(hash:LocalContentHash,song:LocalSong,fp:Fingerprint?):Boolean = hash.sha256.matches(Regex("[a-f0-9]{64}")) && fp!=null && fp.size!=null && fp.modified!=null && fp.modified>0 && hash.uri==song.uri && hash.byteSize==fp.size && hash.modifiedAt==fp.modified && song.byteSize==fp.size && song.modifiedAt==fp.modified
 private suspend fun hash(song:LocalSong,force:Boolean=false):LocalContentHash? {
  val before=fingerprint(song) ?: return null
  if(before.size==null || before.modified==null || before.modified<=0) return null
  val cached=db.matching().hash(song.id)
  if(!force && cached!=null && valid(cached,song,before)) return cached
  beforeRead()
  currentCoroutineContext().ensureActive()
  val digest=MessageDigest.getInstance("SHA-256"); var total=0L
  context.contentResolver.openInputStream(Uri.parse(song.uri))?.use { stream ->
   val buffer=ByteArray(256*1024)
   while(true){currentCoroutineContext().ensureActive(); val count=stream.read(buffer); if(count<0) break; digest.update(buffer,0,count);total+=count;yield()}
  } ?: return null
  currentCoroutineContext().ensureActive()
  val after=fingerprint(song)
  if(before!=after || total!=before.size) return null
  // Also fence a scan/replacement that completed while bytes were being read.
  val latest=db.library().song(song.id)
  if(latest!=song) return null
  return LocalContentHash(song.id,song.uri,before.size,before.modified,digest.digest().joinToString(""){"%02x".format(it)},System.currentTimeMillis()).also{db.matching().saveHash(it)}
 }
 private fun catalogTrack(row:PortableCatalogSong):MatchTrack {
  val fields=CatalogJson.parse(row.payload.toByteArray()).obj().fields
  fun text(key:String)=(fields[key] as? CatalogJson.Str)?.value ?: ""
  val duration=(fields["duration"] as? CatalogJson.Num)?.token?.toDoubleOrNull()
  return MatchTrack(row.songId,"",text("title"),text("artist"),text("album"),duration,text("content_sha256").ifEmpty{null},text("recording_id"),text("isrc"))
 }
 private fun targetIdentity(row:PortableCatalogSong):String {
  val hash=catalogTrack(row).hash
  return if(hash?.matches(Regex("[a-fA-F0-9]{64}"))==true) "sha256:"+hash.lowercase() else "payload:"+catalogSha256(row.payload.toByteArray())
 }
 private suspend fun refresh(readBytes:Boolean, localIds:Set<String>?=null) {
  val journalGeneration=JournalIdentityProvider.invalidateMappings()
  val previousResults=mutable.value.results
  mutable.value=mutable.value.copy(busy=mutable.value.busy || readBytes,error=null)

  val catalog=db.catalogs().latest()
  if(catalog==null){snapshot=null;anchors=emptyMap();mutable.value=MatchingState(loaded=true,error="Import a catalog before matching.");return}
  val rows=db.catalogs().songs(catalog.manifestSha256)
  val songs=rows.map(::catalogTrack)
  val targetIdentities=rows.associate{it.songId to targetIdentity(it)}
  val songsById=songs.associateBy{it.id}
  val byHash=songs.filter{it.hash!=null}.groupBy{it.hash!!.lowercase()}
  val byName=songs.groupBy{ConservativeMatcher.normalize(it.artist+" "+it.title)}
  val allLocal=db.library().allSongs().filter{it.available}
  val partial=localIds!=null && snapshot==catalog && previousResults.map{it.localId}.toSet()==allLocal.map{it.id}.toSet()
  val local=if(partial) allLocal.filter{it.id in localIds!!} else allLocal
  val decisions=db.matching().decisions(catalog.serverId,catalog.catalogId)
  val currentAnchors=mutableMapOf<String,LocalContentHash>(); val results=mutableListOf<MatchResult>()
  for((index,song) in local.withIndex()) {
   currentCoroutineContext().ensureActive()
   if(readBytes) mutable.value=mutable.value.copy(progress="Reading ${index+1} of ${local.size}: ${song.fileName}")
   val identity=try {if(readBytes) hash(song) else db.matching().hash(song.id)?.takeIf{valid(it,song,fingerprint(song))}} catch(c:CancellationException){throw c} catch(_:Exception){null}
   if(identity!=null) currentAnchors[song.id]=identity
   val anchored=decisions.filter{it.localId==song.id && identity!=null && it.contentSha256==identity.sha256}
   val relevant=anchored.filter{it.targetIdentity==targetIdentities[it.songId]}
   val staleConfirmation=anchored.any{it.action=="confirm" && it !in relevant}
   val rejected=relevant.filter{it.action=="reject"}.map{it.songId}.toSet()
   val confirmations=relevant.filter{it.action=="confirm"}.map{it.songId}.toSet()
   val stem=ConservativeMatcher.normalize(song.fileName.replace(Regex("(?i)\\.mp3$"),""))
   val prefixes=stem.split(' ').indices.map{stem.split(' ').take(it+1).joinToString(" ")}
   val pool=((identity?.sha256?.let{byHash[it]} ?: emptyList()) + prefixes.flatMap{byName[it] ?: emptyList()} + relevant.mapNotNull{d->songsById[d.songId]}).distinctBy{it.id}
   val track=MatchTrack(song.id,song.fileName,duration=song.durationMs?.div(1000.0),hash=identity?.sha256)
   val result=ConservativeMatcher.match(track,pool.filter{it.id !in rejected})
   val suggestions=ConservativeMatcher.match(track,pool).candidates.map{if(it.songId in rejected) it.copy(reasons=it.reasons+"user_rejected") else it}
   // Keep every saved decision resettable, including removed targets and those
   // outside the top candidate suggestions. Changed targets need fresh review.
   val saved=anchored.map { decision ->
    val target=songsById[decision.songId]
    val reason=when { target==null -> "missing_catalog_recording"; decision !in relevant -> "catalog_recording_changed"; decision.action=="reject" -> "user_rejected"; else -> "persistent_user_confirmation" }
    MatchCandidate(decision.songId,target?.title ?: "Missing catalog recording (${decision.songId})",target?.artist ?: "",if(decision in relevant && decision.action=="confirm") 1.0 else 0.0,listOf(reason))
   }
   val visible=(saved+suggestions).distinctBy{it.songId}
   results.add(when {
    confirmations.size==1 && confirmations.single() !in rejected && songs.any{it.id==confirmations.single()} -> {
     val selected=songs.first{it.id==confirmations.single()}; result.copy(status="confirmed",songId=selected.id,candidates=(listOf(MatchCandidate(selected.id,selected.title,selected.artist,1.0,listOf("persistent_user_confirmation")))+visible.filter{it.songId!=selected.id}))
    }
    confirmations.isNotEmpty() || staleConfirmation -> result.copy(status="ambiguous",songId=null,candidates=visible)
    else -> result.copy(candidates=visible)
   })
  }
  if(db.catalogs().latest()!=catalog || db.library().allSongs().filter{it.available}!=allLocal){mutable.value=MatchingState(loaded=true,error="Library or catalog changed. Match again.");return}
  snapshot=catalog
  anchors=if(partial) (anchors-localIds!!)+currentAnchors else currentAnchors
  val merged=if(partial) {val updates=results.associateBy{it.localId};previousResults.map{updates[it.localId] ?: it}} else results
  mutable.value=MatchingState(loaded=true,busy=job!=null,results=merged,progress=if(readBytes) "Matched ${results.count{it.status=="confirmed" || it.status=="high_confidence"}} of ${local.size} local songs. ${local.size-currentAnchors.size} files could not be read." else "")
  JournalIdentityProvider.publishMappings(journalGeneration,merged.mapNotNull { result ->
   val song=allLocal.firstOrNull{it.id==result.localId}
   val anchor=anchors[result.localId]
   val target=result.songId
   if(song==null || anchor==null || target==null || result.status !in setOf("confirmed","high_confidence")) null
   else JournalIdentityProvider.Entry(song,catalog.serverId,catalog.catalogId,catalog.revision,target,
    "${result.status}:${catalog.manifestSha256}:${anchor.sha256}")
  })
 }
 suspend fun confirm(localId:String,songId:String)=decide(listOf(localId to songId),"confirm")
 suspend fun reject(localId:String,songId:String)=decide(listOf(localId to songId),"reject")
 suspend fun clearDecision(localId:String,songId:String)=decide(listOf(localId to songId),null)
 suspend fun batchConfirm(selections:List<Pair<String,String>>)=decide(selections,"confirm")
 private suspend fun decide(selections:List<Pair<String,String>>,action:String?)=withContext(Dispatchers.IO){mutex.withLock {
  if(selections.isEmpty()) return@withLock
  JournalIdentityProvider.invalidateMappings()
  val chosen=selections.distinct()
  if(chosen.distinctBy{it.first}.size!=chosen.size){mutable.value=mutable.value.copy(error="Choose one recording per local song.");return@withLock}
  job=currentCoroutineContext()[Job]
  val affected=mutableSetOf<String>()
  var saved=0
  var failed=0
  try {
   mutable.value=mutable.value.copy(busy=true,progress="Verifying selected files…",error=null)
   val catalog=snapshot ?: return@withLock
   // Read catalog metadata once for the whole explicit selection. Each local
   // file is still rehashed and fenced individually before its decision commits.
   val targets=db.catalogs().songs(catalog.manifestSha256).associateBy{it.songId}
   for((index,selection) in chosen.withIndex()) {
    currentCoroutineContext().ensureActive()
    val (localId,songId)=selection
    mutable.value=mutable.value.copy(progress="Saving ${index+1} of ${chosen.size}…")
    val expected=anchors[localId]
    val song=db.library().song(localId)?.takeIf{it.available}
    if(expected==null || song==null){failed++;continue}
    val fresh=try{hash(song,true)}catch(c:CancellationException){throw c}catch(_:Exception){null}
    if(fresh?.sha256!=expected.sha256 || db.catalogs().latest()!=catalog){
     refresh(false)
     mutable.value=mutable.value.copy(error="File or catalog changed. Review the new results.")
     return@withLock
    }
    if(action!=null && mutable.value.results.none{it.localId==localId && it.candidates.any{c->c.songId==songId}}){failed++;continue}
    val target=targets[songId]
    if(target==null && action!=null){failed++;continue}
    var adopted=false
    db.withTransaction {
     if(db.catalogs().latest()!=catalog || db.library().song(localId)!=song) return@withTransaction
     if(action==null) db.matching().clearDecision(catalog.serverId,catalog.catalogId,localId,expected.sha256,songId)
     else {
      if(action=="confirm") db.matching().clearConfirmations(catalog.serverId,catalog.catalogId,localId,expected.sha256)
      db.matching().saveDecision(PortableMatchDecision(catalog.serverId,catalog.catalogId,localId,expected.sha256,songId,action,System.currentTimeMillis(),targetIdentity(requireNotNull(target))))
     }
     adopted=true
    }
    affected.add(localId)
    if(adopted) saved++ else failed++
   }
   refresh(false,affected)
   mutable.value=mutable.value.copy(progress="$saved correction(s) saved."+(if(failed>0) " $failed could not be saved; review again." else ""))
  } catch(c:CancellationException){
   withContext(NonCancellable){refresh(false,affected);mutable.value=mutable.value.copy(progress="Correction check cancelled. Saved decisions shown.")}
   throw c
  } catch(_:Exception){
   withContext(NonCancellable){runCatching{refresh(false,affected)}}
   mutable.value=mutable.value.copy(error="Could not save all corrections. Saved decisions shown; review again.")
  } finally {job=null;mutable.value=mutable.value.copy(busy=false)}
 }}
 companion object {
  @Volatile private var instance:MatchingRepository?=null
  fun get(context:Context):MatchingRepository=instance ?: synchronized(this){instance ?: MatchingRepository(context.applicationContext,LibraryRepository.get(context).db).also{instance=it}}
 }
}
