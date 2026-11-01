package dev.radio.mobile.recommendation

import android.content.Context
import dev.radio.mobile.data.*
import dev.radio.mobile.playback.PlaybackState
import dev.radio.mobile.portable.*
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONObject

data class RadioSuggestion(val song:LocalSong,val pick:RadioPick)
data class RadioInputToken(val library:LibraryState,val mappings:JournalIdentityProvider.MappingSnapshot,val feedback:ListeningJournalState,val mood:String,val exploration:Double,val vibe:String?,val current:String?)
data class RadioBatch(val suggestions:List<RadioSuggestion>,val token:RadioInputToken)
data class RadioState(val loaded:Boolean=false,val busy:Boolean=false,val suggestions:List<RadioSuggestion> = emptyList(),val mood:String="neutral",val exploration:Double=.3,val matched:Int=0,val localOnly:Int=0,val status:String="Loading local Radio…",val error:String?=null,val vibeLocalId:String?=null,val vibeUntil:Long=0,val vibeSongsLeft:Int=0,val generation:Long=0,val vibeAvailable:Boolean=false)

/** Local-only recommendation snapshots. Publishing a preview never calls playback. */
class RadioRepository internal constructor(context:Context,private val db:LibraryDatabase,private val library:StateFlow<LibraryState>,private val journal:ListeningJournalRepository,private val scope:CoroutineScope,
    private val directory:File=File(context.applicationContext.noBackupFilesDir,"portable_catalog_blobs")) {
    private val prefs=context.getSharedPreferences("radio_recommendations",Context.MODE_PRIVATE)
    private val mutex=Mutex()
    private val mutable=MutableStateFlow(RadioState(mood=prefs.getString("mood","neutral") ?: "neutral",exploration=prefs.getFloat("exploration",.3f).toDouble(),vibeLocalId=prefs.getString("vibe",null),vibeUntil=prefs.getLong("vibeUntil",0),vibeSongsLeft=prefs.getInt("vibeLeft",0)))
    val state=mutable.asStateFlow()
    @Volatile private var playback=PlaybackState()
    private var refreshJob:Job?=null
    private var lastAttempt:String?=null
    fun token()=RadioInputToken(library.value,JournalIdentityProvider.mappings.value,journal.state.value,mutable.value.mood,mutable.value.exploration,mutable.value.vibeLocalId,playback.currentId)
    fun valid(value:RadioInputToken)=token()==value && !library.value.accessLost && !library.value.scanning && journal.state.value.pendingWrites==0
    init {
        scope.launch { combine(library,JournalIdentityProvider.mappings,journal.state) { local,mappings,feedback-> Triple(local,mappings,feedback) }.collect { (local,_,feedback)->
            if(!local.loading && !local.scanning && feedback.feedbackLoaded && feedback.pendingWrites==0) refresh()
        } }
        scope.launch { while(isActive){delay(1000);if(mutable.value.vibeLocalId!=null && System.currentTimeMillis()>=mutable.value.vibeUntil) clearVibe()} }
    }
    fun updatePlayback(value:PlaybackState) { val changed=playback.currentId!=value.currentId;playback=value;if(changed)refresh() }
    fun setMood(value:String) {if(value !in OfflineRecommender.MOODS)return;prefs.edit().putString("mood",value).putLong("moodAt",System.currentTimeMillis()).apply();mutable.update{it.copy(mood=value)};refresh()}
    fun setExploration(value:Double) {val bounded=value.coerceIn(0.0,1.0);prefs.edit().putFloat("exploration",bounded.toFloat()).apply();mutable.update{it.copy(exploration=bounded)};refresh()}
    private fun saveVibe(){val value=mutable.value;prefs.edit().putString("vibe",value.vibeLocalId).putLong("vibeUntil",value.vibeUntil).putInt("vibeLeft",value.vibeSongsLeft).apply()}
    fun clearVibe(){mutable.update{it.copy(vibeLocalId=null,vibeUntil=0,vibeSongsLeft=0)};saveVibe();refresh()}
    fun lockVibe() {
        val current=playback.current ?: return
        if(!mutable.value.vibeAvailable)return
        val entry=JournalIdentityProvider.mappings.value.entries[current.id]?.takeIf{it.song==current} ?: return
        if(!entry.provenance.contains(":"))return
        mutable.update{it.copy(vibeLocalId=current.id,vibeUntil=System.currentTimeMillis()+30*60*1000L,vibeSongsLeft=10)};saveVibe();refresh()
    }
    fun actualStart(attempt:String) {if(lastAttempt==attempt)return;lastAttempt=attempt
        if(mutable.value.vibeLocalId!=null){val remaining=mutable.value.vibeSongsLeft-1;if(remaining<=0)clearVibe() else {mutable.update{it.copy(vibeSongsLeft=remaining)};saveVibe()}}
    }
    fun refresh() { refreshJob?.cancel();refreshJob=scope.launch {delay(75);compute()} }
    suspend fun recommendations(exclude:Set<String> = emptySet(),count:Int=5):RadioBatch? { val input=token();val rows=compute(exclude,count) ?: return null;return if(valid(input))RadioBatch(rows,input) else null }
    private suspend fun compute(exclude:Set<String> = emptySet(),count:Int=5):List<RadioSuggestion>? = mutex.withLock {
        val local=library.value;val mapping=JournalIdentityProvider.mappings.value
        val inputs=token()
        if(local.loading || local.scanning || !journal.state.value.feedbackLoaded || journal.state.value.pendingWrites>0)return@withLock null
        if(local.accessLost){mutable.update{it.copy(loaded=true,busy=false,suggestions=emptyList(),status="Music folder access is unavailable.",generation=it.generation+1)};return@withLock emptyList()}
        mutable.update{it.copy(busy=true,error=null)}
        try {
            val available=local.songs.filter{it.available && it.uri.startsWith("content:")}
            var record:PortableCatalogRevision?=null;var catalog:ValidatedCatalog?=null;var vectors=emptyList<RadioVector>();var damaged=false
            CatalogRepository.storageMutex.withLock {
                record=db.catalogs().latest()
                record?.let { saved->
                    try {
                        catalog=CatalogValidator.manifest(saved.manifest.toByteArray(Charsets.UTF_8))
                        require(catalog!!.sha256==saved.manifestSha256 && catalog!!.catalogId==saved.catalogId && catalog!!.revision==saved.revision)
                        vectors=catalog!!.descriptors.mapNotNull { d->
                            // Legacy unknown model provenance cannot prove compatible spaces.
                            if(d.model.contains("unknown",true) || d.modelVersion.contains("unknown",true) || d.model.contains("legacy",true))return@mapNotNull null
                            val file=File(directory,d.sha256+".bin");require(file.length()==d.byteLength.toLong());val bytes=file.readBytes();CatalogValidator.validateBlob(d,bytes)
                            val buffer=ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
                            RadioVector(d.songId,d.space,d.model,d.modelVersion,FloatArray(d.dimension){if(d.dtype=="float32")buffer.float else CatalogValidator.half(buffer.short.toInt() and 65535)})
                        }
                    } catch(cancelled:CancellationException){throw cancelled} catch(_:Exception){damaged=true;catalog=null;vectors=emptyList()}
                }
            }
            val accepted=available.mapNotNull { song->mapping.entries[song.id]?.takeIf { it.song==song && it.serverId==record?.serverId && it.catalogId==record?.catalogId && it.revision==record?.revision && it.provenance.contains(record!!.manifestSha256) && catalog?.songs?.containsKey(it.songId)==true }?.let{song.id to it.songId} }.toMap()
            val tracks=available.map { song->
                val sid=accepted[song.id] ?: "local:${song.id}";val metadata=catalog?.songs?.get(sid)?.let(::JSONObject)
                fun number(key:String)=metadata?.optDouble(key,.5)?.takeIf{it.isFinite()} ?: .5
                fun labels(key:String):Set<String> {val a=metadata?.optJSONArray(key) ?: return emptySet();return (0 until a.length()).map{a.optString(it)}.toSet()}
                RadioTrack(sid,song.id,metadata?.optString("artist")?.takeIf{it.isNotBlank()} ?: song.artist,number("energy"),number("valence"),metadata?.takeIf{!it.isNull("cluster_id")}?.optInt("cluster_id"),song.folderPath,labels("genres")+labels("genre_tags"),labels("collections"))
            }
            val byLocal=tracks.associateBy{it.localId}
            val rows=(db.journal().radioTasteEvents()+db.journal().radioRecentEvents(System.currentTimeMillis()-86400000L)+db.journal().radioLastStarts()).distinctBy{it.eventId}.sortedBy{it.logicalClock}
            val origins=rows.chunked(500).flatMap{db.journal().radioOrigins(it.map{row->row.eventId})}.associate{it.eventId to it.serverId}
            val events=rows.mapNotNull { row->
                val payload=JSONObject(row.payload)
                // A local-only observation stays local-only after later matching.
                val sid=if(origins[row.eventId]==record?.serverId && payload.optString("catalog_id")==record?.catalogId && payload.optString("song_id").let{it.isNotBlank() && it!="null"})payload.getString("song_id") else "local:${row.localId}"
                val kind=when(row.kind){"song_started"->"started";"song_completed"->"completed";"song_skipped"->"skipped";"song_replayed"->"replayed";"favorite_changed"->if(payload.optBoolean("value"))"hearted" else "unhearted";"dislike_changed"->if(payload.optBoolean("value"))"disliked" else "unblacklisted";else->return@mapNotNull null}
                RadioEvent(row.eventId,kind,sid,row.occurredAt/1000.0,row.terminationReason)
            }
            val feedback=journal.state.value.feedback
            val hearts=feedback.values.filter{it.favorite}.mapNotNull{byLocal[it.localId]?.songId}.toSet()
            val dislikes=feedback.values.filter{it.disliked}.mapNotNull{byLocal[it.localId]?.songId}.toSet()
            var streak=0;var effectiveMood=mutable.value.mood
            for(event in events){if(event.kind=="completed")streak=0 else if(event.kind=="skipped"){streak++;if(streak>=3 && event.at*1000>prefs.getLong("moodAt",0))effectiveMood=when(event.reason){"too_intense"->"chill";"too_slow"->"energetic";else->"discovery"}}}
            val profile=RadioProfile(events,hearts,dislikes,byLocal[playback.currentId]?.songId,events.filter{it.kind=="started"}.takeLast(5).map{id->tracks.firstOrNull{it.songId==id.songId}?.energy ?: .5},streak)
            val vibe=mutable.value.vibeLocalId?.let(accepted::get)?.takeIf{anchor->vectors.any{it.songId==anchor}}
            if(mutable.value.vibeLocalId!=null && vibe==null && (mapping.loaded || damaged || available.none{it.id==mutable.value.vibeLocalId})){mutable.update{it.copy(vibeLocalId=null,vibeUntil=0,vibeSongsLeft=0)};saveVibe()}
            val settings=RadioSettings(mood=effectiveMood,exploration=mutable.value.exploration,vibeLock=vibe)
            val excluded=tracks.filter{it.localId in exclude}.map{it.songId}.toSet()
            val picks=OfflineRecommender(tracks,vectors,17).recommend(profile,settings,System.currentTimeMillis()/1000.0,count,excluded)
            val byId=available.associateBy{it.id};val suggestions=picks.mapNotNull{p->byId[p.localId]?.let{RadioSuggestion(it,p)}}
            if(!valid(inputs) || db.catalogs().latest()!=record){mutable.update{it.copy(busy=false)};refresh();return@withLock null}
            val status=when{available.isEmpty()->"Choose music in Library to start Radio.";damaged->"Local recommendations · imported vectors need reimport";record==null->"Local recommendations · no catalog imported";accepted.isEmpty()->"Local recommendations · match songs to use imported metadata";else->"${accepted.size} matched · ${available.size-accepted.size} local only"}
            val vibeAvailable=accepted[playback.currentId]?.let{sid->vectors.any{it.songId==sid}}==true
            mutable.update{it.copy(loaded=true,busy=false,suggestions=if(exclude.isEmpty())suggestions else it.suggestions,mood=effectiveMood,matched=accepted.size,localOnly=available.size-accepted.size,status=status,generation=it.generation+1,vibeAvailable=vibeAvailable)}
            suggestions
        } catch(cancelled:CancellationException){mutable.update{it.copy(busy=false)};throw cancelled}
        catch(_:Exception){mutable.update{it.copy(loaded=true,busy=false,error="Couldn't prepare local recommendations. Refresh to retry.")};null}
    }
}
