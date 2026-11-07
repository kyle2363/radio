package dev.radio.mobile.recommendation

import android.content.Context
import androidx.core.content.edit
import dev.radio.mobile.connection.EndpointPolicy
import dev.radio.mobile.connection.PairedServerStore
import dev.radio.mobile.connection.PairingConnectedLink
import dev.radio.mobile.connection.PairingRepository
import dev.radio.mobile.connection.RadioApiClient
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
data class RadioInputToken(val library:LibraryState,val mappings:JournalIdentityProvider.MappingSnapshot,val feedback:ListeningJournalState,val mood:String,val exploration:Double,val vibe:String?,val current:String?,val queueRevision:Long,val playOrder:List<String>,val policyRevision:Long,val bootstrap:Set<String>,val legacyVectorSnapshot:String?,val syncRevision:Long=0,val syncEpoch:Long=0)
data class RadioBatch(val suggestions:List<RadioSuggestion>,val token:RadioInputToken)
data class RadioState(val loaded:Boolean=false,val busy:Boolean=false,val suggestions:List<RadioSuggestion> = emptyList(),val mood:String="neutral",val exploration:Double=.3,val matched:Int=0,val localOnly:Int=0,val status:String="Loading local Radio…",val error:String?=null,val vibeLocalId:String?=null,val vibeUntil:Long=0,val vibeSongsLeft:Int=0,val generation:Long=0,val vibeAvailable:Boolean=false,
    val diagnostics:RadioDiagnostics=RadioDiagnostics(),val bootstrapLocalIds:Set<String> = emptySet(),val bootstrapCandidates:Set<String> = emptySet(),val bootstrapDismissed:Boolean=false,
    val suggestionAnchorTitle:String?=null,val catalogSnapshot:String?=null,val legacyVectorTracks:Int=0,val legacyVectorsEnabled:Boolean=false)

/** Local-only recommendation snapshots. Publishing a preview never calls playback. */
class RadioRepository internal constructor(context:Context,private val db:LibraryDatabase,private val library:StateFlow<LibraryState>,private val journal:ListeningJournalRepository,private val scope:CoroutineScope,
    private val directory:File=File(context.applicationContext.noBackupFilesDir,"portable_catalog_blobs"),
    connectedLink:(()->ConnectedServerLink)?=null) {
    private val prefs=context.getSharedPreferences("radio_recommendations",Context.MODE_PRIVATE)
    private val mutex=Mutex()
    private val mutable=MutableStateFlow(RadioState(mood=prefs.getString("mood","neutral") ?: "neutral",exploration=prefs.getFloat("exploration",.3f).toDouble(),vibeLocalId=prefs.getString("vibe",null),vibeUntil=prefs.getLong("vibeUntil",0),vibeSongsLeft=prefs.getInt("vibeLeft",0)))
    val state=mutable.asStateFlow()
    // Connected Radio (checkpoint 4). Off until the listener turns it on; never required for local play.
    private val serverAi=MutableStateFlow<Set<String>>(emptySet())
    private val appContext=context.applicationContext
    val connected=ConnectedRadioSource(object:ConnectedServerLink {
        private val real by lazy {
            (connectedLink ?: {
                val policy=EndpointPolicy(dev.radio.mobile.BuildConfig.DEBUG)
                PairingConnectedLink(PairingRepository(PairedServerStore(appContext,policy),RadioApiClient(policy),policy))
            })()
        }
        override suspend fun access()=real.access()
        override suspend fun post(path:String,body:String)=real.post(path,body)
    },scope)
    val connectedState:StateFlow<ConnectedUi> = combine(connected.status,connected.set,serverAi){status,set,ai->ConnectedUi(status,set,ai)}
        .stateIn(scope,SharingStarted.Eagerly,ConnectedUi())
    /** Emits whenever a connected answer starts, finishes or is dropped, so refill can re-evaluate. */
    val connectedSignal:Flow<Pair<String?,String?>> = combine(connected.status.map{it.pendingAnchor}.distinctUntilChanged(),connected.set.map{it?.requestId}.distinctUntilChanged()){a,b->a to b}
    @Volatile private var playback=PlaybackState()
    private var refreshJob:Job?=null
    private var lastAttempt:String?=null
    private val sessionSeed=kotlin.random.Random.nextInt()
    @Volatile private var legacyVectorSnapshot=prefs.getString("legacyVectorSnapshot",null)
    @Volatile private var policyRevision=0L
    @Volatile private var syncRevision=0L
    @Volatile private var sharedExcludedLocalIds=emptySet<String>()
    @Volatile private var explorationEdited=false
    private val explorationLock=Any()
    private val defaultsReady=CompletableDeferred<Unit>()
    private var sequenceOffset=0
    // Local IDs survive rematching; these are selection reservations, never listening events.
    @Volatile private var recentSelections=runCatching {
        val saved=org.json.JSONArray(prefs.getString("recentSelections","[]"))
        (0 until saved.length()).map{saved.getString(it)}.takeLast(50)
    }.getOrDefault(emptyList())
    fun token():RadioInputToken { val output=playback;val state=mutable.value
        return RadioInputToken(library.value,JournalIdentityProvider.mappings.value,journal.state.value,state.mood,state.exploration,state.vibeLocalId,output.currentId,output.queueRevision,output.playOrder,policyRevision,state.bootstrapLocalIds,legacyVectorSnapshot,syncRevision,PortableSyncChanges.revision)
    }
    fun valid(value:RadioInputToken)=!PortableSyncChanges.isApplying && token()==value && !library.value.accessLost && !library.value.scanning && journal.state.value.pendingWrites==0
    internal fun <T> accept(batch:RadioBatch, block:()->T):T? = PortableSyncChanges.accept(batch.token.syncEpoch) {
        if(valid(batch.token))block() else null
    }
    init {
        if(prefs.getBoolean("connected_radio",false))connected.setEnabled(true)
        scope.launch {
            try {
                val initial=db.catalogs().latest()?.let { readSharedRadioProfile(db,it).explorationDefault }
                synchronized(explorationLock) {
                    if(initial!=null && !explorationEdited) mutable.update{it.copy(exploration=initial)}
                }
            } catch(cancelled:CancellationException) {
                throw cancelled
            } catch(_:Exception) {
                // A shared default is optional; local tuning and local playback remain usable.
            } finally { defaultsReady.complete(Unit) }
        }
        scope.launch { db.journal().observeSyncCursors().collect {
            syncRevision++
            refresh()
        } }
        scope.launch { PortableSyncChanges.changes.collect { refresh() } }
        scope.launch { combine(library,JournalIdentityProvider.mappings,journal.state) { local,mappings,feedback-> Triple(local,mappings,feedback) }.collect { (local,_,feedback)->
            if(!local.loading && !local.scanning && feedback.feedbackLoaded && feedback.pendingWrites==0) refresh()
        } }
        scope.launch { while(isActive){delay(1000);if(mutable.value.vibeLocalId!=null && System.currentTimeMillis()>=mutable.value.vibeUntil) clearVibe()} }
    }
    fun updatePlayback(value:PlaybackState) { val changed=playback.currentId!=value.currentId || playback.queueRevision!=value.queueRevision || playback.playOrder!=value.playOrder;playback=value;if(changed)refresh() }
    fun setBootstrap(localIds:Set<String>) {
        val selected=localIds.intersect(mutable.value.bootstrapCandidates).sorted().take(5).toSet()
        mutable.update{it.copy(bootstrapLocalIds=selected,bootstrapDismissed=selected.isEmpty())};refresh()
    }
    fun dismissBootstrap(){mutable.update{it.copy(bootstrapDismissed=true)}}
    fun clearBootstrap(){mutable.update{it.copy(bootstrapLocalIds=emptySet(),bootstrapDismissed=false)};refresh()}
    /** Compatibility is an explicit choice for one immutable imported snapshot. */
    fun setLegacyVectorsEnabled(enabled:Boolean) {
        val snapshot=mutable.value.catalogSnapshot ?: return
        if(enabled && mutable.value.legacyVectorTracks==0)return
        legacyVectorSnapshot=if(enabled)snapshot else null
        prefs.edit {if(enabled)putString("legacyVectorSnapshot",snapshot) else remove("legacyVectorSnapshot")}
        mutable.update{it.copy(legacyVectorsEnabled=enabled)}
        refresh()
    }
    /** Called on the service thread only after a fenced batch actually enters the timeline. */
    @Synchronized internal fun committed(batch:RadioBatch,acceptedLocalIds:Set<String> = batch.suggestions.map{it.song.id}.toSet()):Boolean {
        // Queue/playback inputs change as a consequence of acceptance. The service
        // validates the full token immediately before its atomic timeline operation.
        val accepted=batch.suggestions.filter{it.song.id in acceptedLocalIds}
        if(batch.token.policyRevision!=policyRevision || accepted.isEmpty())return false
        recentSelections=(recentSelections+accepted.map{it.song.id}).takeLast(50)
        accepted.filter{it.pick.reason.startsWith(ConnectedPolicy.SERVER_AI_PREFIX)}.map{it.song.id}.takeIf{it.isNotEmpty()}?.let{ai->serverAi.update{(it+ai).toList().takeLast(50).toSet()}}
        sequenceOffset=(sequenceOffset+accepted.size)%1000000
        policyRevision++
        prefs.edit { putString("recentSelections",org.json.JSONArray(recentSelections).toString()) }
        refresh()
        return true
    }
    fun setMood(value:String) {if(value !in OfflineRecommender.MOODS)return;prefs.edit().putString("mood",value).putLong("moodAt",System.currentTimeMillis()).apply();mutable.update{it.copy(mood=value)};refresh()}
    fun setExploration(value:Double) {if(!value.isFinite())return; synchronized(explorationLock){explorationEdited=true;val bounded=value.coerceIn(0.0,1.0);prefs.edit().putFloat("exploration",bounded.toFloat()).apply();mutable.update{it.copy(exploration=bounded)};journal.setSharedExploration(bounded)};refresh()}
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
    @Volatile private var previewExclusions = emptySet<String>()
    fun isDisliked(id:String) = journal.state.value.feedback[id]?.disliked==true || id in sharedExcludedLocalIds
    fun refreshSuggestions() {
        if(mutable.value.suggestions.isNotEmpty()) previewExclusions = mutable.value.suggestions.map { it.song.id }.toSet()
        refresh()
    }
    suspend fun startRecommendations(seedLocalId:String?):RadioBatch? {
        if(seedLocalId==null)return recommendations(anchorLocalId=null)
        val seed=library.value.songs.firstOrNull{it.id==seedLocalId && it.available} ?: return null
        if(isDisliked(seedLocalId))return null
        val batch=recommendations(setOf(seedLocalId),4,seedLocalId) ?: return null
        if(isDisliked(seedLocalId))return null
        if(batch.token.library.songs.none{it==seed} || !valid(batch.token))return null
        return batch.copy(suggestions=listOf(RadioSuggestion(seed,RadioPick("local:$seedLocalId",seedLocalId,0.0,0.5,null,slot="exploit",reason="Chosen starting song")))+batch.suggestions)
    }
    val connectedEnabled:Boolean get()=connected.enabled
    fun setConnectedEnabled(value:Boolean) {
        prefs.edit{putBoolean("connected_radio",value)}
        connected.setEnabled(value)
        if(!value)serverAi.value=emptySet()
        val output=playback
        // Turning it on mid-song prepares an option set for the song already playing.
        if(value && output.radioActive)output.current?.let{songStarted(it,output)}
    }
    private fun connectedFence():ConnectedFence? { val id=playback.currentId ?: return null;val st=mutable.value
        return ConnectedFence(id,st.mood,st.exploration,st.vibeLocalId,JournalIdentityProvider.mappings.value.generation,PortableSyncChanges.revision)
    }
    /** Called only for an actual start. Asks the desktop for one option set anchored on this song; playback never waits. */
    @Volatile private var startedAnchor:String?=null
    /** True while Connected Radio is on and the current song's actual start has not yet been seen (its request isn't pending yet). */
    fun awaitingConnectedStart(currentId:String?)=connected.enabled && currentId!=null && startedAnchor!=currentId
    fun songStarted(song:LocalSong,output:PlaybackState) {
        if(!connected.enabled || !output.radioActive)return
        startedAnchor=song.id
        updatePlayback(output)
        val local=library.value;val mapping=JournalIdentityProvider.mappings.value
        if(local.accessLost || local.scanning || local.loading)return
        val recent=journal.state.value.history.filter{it.kind=="song_completed" || it.kind=="song_skipped"}.sortedBy{it.logicalClock}.takeLast(20)
            .map{ConnectedRecent(it.localId,if(it.kind=="song_completed")"completed" else "skipped")}
        val st=mutable.value
        connected.begin(ConnectedRequest(song,st.mood,st.exploration,st.vibeLocalId,mapping,local.songs.filter{it.available}.associateBy{it.id},recent,
            output.playOrder.toSet()+recent.map{it.localId}+song.id,::isDisliked,
            ConnectedFence(song.id,st.mood,st.exploration,st.vibeLocalId,mapping.generation,PortableSyncChanges.revision)))
    }
    private fun playable(id:String)=library.value.songs.any{it.id==id && it.available && it.uri.startsWith("content:")}
    /**
     * Refill batch for a running Radio queue: valid connected "similar" picks first (never more than the bound), then
     * the unchanged offline recommender for the remaining room. With no usable connected set this is exactly [recommendations].
     */
    suspend fun refill(exclude:Set<String>,room:Int,tail:String?):RadioBatch? {
        val held=connected.set.value
        if(held==null || room<=0)return recommendations(exclude,room,tail)
        connected.revalidate(connectedFence())
        if(connected.set.value!==held)return recommendations(exclude,room,tail)
        val chosen=ConnectedPolicy.refillPicks(held,exclude,mutable.value.vibeLocalId!=null || held.vibeLocked,room,::isDisliked,::playable)
        if(chosen.isEmpty())return recommendations(exclude,room,tail)
        val mapping=JournalIdentityProvider.mappings.value
        val input=token()
        val first=chosen.map{RadioSuggestion(it.song,RadioPick(mapping.entries[it.song.id]?.songId ?: "local:${it.song.id}",it.song.id,0.0,.5,null,slot="exploit",
            reason=ConnectedPolicy.SERVER_AI_PREFIX+" · "+it.reason.ifBlank{"Similar to the current song"}))}
        val rest=room-chosen.size
        val offline=if(rest>0)compute(exclude+chosen.map{it.song.id},rest,tail,publishPreview=false).orEmpty() else emptyList()
        connected.revalidate(connectedFence())
        // A late or changed set is dropped whole, never merged into a different situation.
        if(!valid(input) || connected.set.value!==held)return null
        return RadioBatch(first+offline.filter{row->first.none{it.song.id==row.song.id}}.take(rest),input)
    }
    fun refresh() { connected.revalidate(connectedFence());refreshJob?.cancel();refreshJob=scope.launch {delay(75);compute()} }
    suspend fun recommendations(exclude:Set<String> = emptySet(),count:Int=5,anchorLocalId:String?=playback.currentId):RadioBatch? { val input=token();val rows=compute(exclude,count,anchorLocalId,publishPreview=false) ?: return null;return if(valid(input))RadioBatch(rows,input) else null }
    private suspend fun compute(exclude:Set<String> = emptySet(),count:Int=5,anchorLocalId:String?=if(playback.radioActive)playback.playOrder.lastOrNull() ?: playback.currentId else playback.currentId,publishPreview:Boolean=true):List<RadioSuggestion>? = mutex.withLock {
        defaultsReady.await()
        val local=library.value;val mapping=JournalIdentityProvider.mappings.value
        val inputs=token()
        if(PortableSyncChanges.isApplying || local.loading || local.scanning || !journal.state.value.feedbackLoaded || journal.state.value.pendingWrites>0)return@withLock null
        if(local.accessLost){mutable.update{it.copy(loaded=true,busy=false,suggestions=emptyList(),suggestionAnchorTitle=null,diagnostics=RadioDiagnostics(),bootstrapCandidates=emptySet(),bootstrapLocalIds=emptySet(),catalogSnapshot=null,legacyVectorTracks=0,legacyVectorsEnabled=false,status="Music folder access is unavailable.",generation=it.generation+1)};return@withLock emptyList()}
        mutable.update{it.copy(busy=true,error=null)}
        try {
            val available=local.songs.filter{it.available && it.uri.startsWith("content:")}
            var record:PortableCatalogRevision?=null;var catalog:ValidatedCatalog?=null;var vectors=emptyList<RadioVector>();var damaged=false
            val legacySongIds=mutableSetOf<String>()
            CatalogRepository.storageMutex.withLock {
                record=db.catalogs().latest()
                record?.let { saved->
                    try {
                        catalog=CatalogValidator.manifest(saved.manifest.toByteArray(Charsets.UTF_8))
                        require(catalog!!.sha256==saved.manifestSha256 && catalog!!.catalogId==saved.catalogId && catalog!!.revision==saved.revision)
                        vectors=catalog!!.descriptors.mapNotNull { d->
                            val legacy=CatalogVectorPolicy.isLegacyCollection(d.model,d.modelVersion)
                            val identity=CatalogVectorPolicy.identity(d.model,d.modelVersion,saved.manifestSha256,inputs.legacyVectorSnapshot==saved.manifestSha256)
                            // Even with compatibility off, validate before advertising recovery.
                            if(identity==null && !legacy)return@mapNotNull null
                            val file=File(directory,d.sha256+".bin");require(file.length()==d.byteLength.toLong());val bytes=file.readBytes();CatalogValidator.validateBlob(d,bytes)
                            val buffer=ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
                            if(legacy)legacySongIds.add(d.songId)
                            if(identity==null)return@mapNotNull null
                            RadioVector(d.songId,d.space,identity.model,identity.version,FloatArray(d.dimension){if(d.dtype=="float32")buffer.float else CatalogValidator.half(buffer.short.toInt() and 65535)})
                        }
                    } catch(cancelled:CancellationException){throw cancelled} catch(_:Exception){damaged=true;catalog=null;vectors=emptyList();legacySongIds.clear()}
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
            val localEvents=rows.mapNotNull { row->
                val payload=JSONObject(row.payload)
                // A local-only observation stays local-only after later matching.
                val sid=if(origins[row.eventId]==record?.serverId && payload.optString("catalog_id")==record?.catalogId && payload.optString("song_id").let{it.isNotBlank() && it!="null"})payload.getString("song_id") else "local:${row.localId}"
                val kind=when(row.kind){"song_started"->"started";"song_completed"->"completed";"song_skipped"->"skipped";"song_replayed"->"replayed";"favorite_changed"->if(payload.optBoolean("value"))"hearted" else "unhearted";"dislike_changed"->if(payload.optBoolean("value"))"disliked" else "unblacklisted";else->return@mapNotNull null}
                RadioEvent(org.json.JSONArray(listOf(payload.optString("device_id"),row.eventId)).toString(),kind,sid,row.occurredAt/1000.0,row.terminationReason)
            }
            val shared=record?.let{readSharedRadioProfile(db,it)} ?: SharedRadioProfile()
            val localIdentities=rows.map{row->val payload=JSONObject(row.payload);payload.optString("device_id") to row.eventId}.toSet()
            val remoteEvents=record?.let{saved->db.journal().remoteRadioEvents(saved.serverId).mapNotNull { row->
                if((row.deviceId to row.eventId) in localIdentities)return@mapNotNull null
                val payload=JSONObject(row.payload)
                val sid=payload.opt("song_id") as? String ?: return@mapNotNull null
                if(payload.optString("catalog_id")!=saved.catalogId)return@mapNotNull null
                val kind=when(row.kind){"song_started"->"started";"song_completed"->"completed";"song_skipped"->"skipped";"song_replayed"->"replayed";"favorite_changed"->if(payload.optBoolean("value"))"hearted" else "unhearted";"dislike_changed"->if(payload.optBoolean("value"))"disliked" else "unblacklisted";else->return@mapNotNull null}
                RadioEvent(org.json.JSONArray(listOf(row.deviceId,row.eventId)).toString(),kind,sid,row.occurredAt/1000.0,payload.optString("reason").takeIf{it.isNotEmpty()})
            }}.orEmpty()
            val events=(localEvents+remoteEvents).distinctBy{it.id}
            val feedback=journal.state.value.feedback
            val hearts=feedback.values.filter{it.favorite}.mapNotNull{byLocal[it.localId]?.songId}.toMutableSet()
            val dislikes=feedback.values.filter{it.disliked}.mapNotNull{byLocal[it.localId]?.songId}.toMutableSet()
            shared.favorites.forEach{(sid,value)->if(value)hearts.add(sid) else hearts.remove(sid)}
            shared.dislikes.forEach{(sid,value)->if(value)dislikes.add(sid) else dislikes.remove(sid)}
            hearts.addAll(shared.localFavorites.mapNotNull{byLocal[it]?.songId})
            dislikes.addAll(shared.localDislikes.mapNotNull{byLocal[it]?.songId})
            dislikes.addAll(shared.blacklist)
            val sharedExclusions=tracks.filter{it.songId in dislikes}.map{it.localId}.toSet()
            var streak=0;var effectiveMood=mutable.value.mood
            for(event in localEvents){if(event.kind=="completed")streak=0 else if(event.kind=="skipped"){streak++;if(streak>=3 && event.at*1000>prefs.getLong("moodAt",0))effectiveMood=when(event.reason){"too_intense"->"chill";"too_slow"->"energetic";else->"discovery"}}}
            val profile=RadioProfile(events,hearts,dislikes,byLocal[anchorLocalId]?.songId,localEvents.filter{it.kind=="started"}.takeLast(5).map{id->tracks.firstOrNull{it.songId==id.songId}?.energy ?: .5},streak,userEnergy=shared.energy,
                bootstrap=inputs.bootstrap.mapNotNull{byLocal[it]?.songId}.toSet())
            val vibe=mutable.value.vibeLocalId?.let(accepted::get)?.takeIf{anchor->vectors.any{it.songId==anchor}}
            if(mutable.value.vibeLocalId!=null && vibe==null && (mapping.loaded || damaged || available.none{it.id==mutable.value.vibeLocalId})){mutable.update{it.copy(vibeLocalId=null,vibeUntil=0,vibeSongsLeft=0)};saveVibe()}
            val settings=RadioSettings(mood=effectiveMood,exploration=mutable.value.exploration,vibeLock=vibe)
            val excluded=tracks.filter{it.localId in exclude || (publishPreview && it.localId in previewExclusions)}.map{it.songId}.toSet()
            val engine=OfflineRecommender(tracks,vectors,sessionSeed)
            val now=System.currentTimeMillis()/1000.0
            val diagnostics=engine.diagnostics(profile,settings,now)
            val eligibleIds=engine.rank(profile,settings,now).map{it.songId}.toSet()
            val legacyVectorTracks=legacySongIds.count{it in eligibleIds}
            val legacyEnabled=legacyVectorTracks>0 && inputs.legacyVectorSnapshot==record?.manifestSha256
            val bootstrapCandidates=tracks.filter{it.songId in diagnostics.bootstrapCandidates}.map{it.localId}.toSet()
            val picks=engine.recommendSequence(profile,settings,now,count,excluded,recentSelections.mapNotNull{byLocal[it]?.songId},sequenceOffset)
            val byId=available.associateBy{it.id};val suggestions=picks.mapNotNull{p->byId[p.localId]?.let{RadioSuggestion(it,p)}}
            if(!valid(inputs) || db.catalogs().latest()!=record){mutable.update{it.copy(busy=false)};refresh();return@withLock null}
            sharedExcludedLocalIds=sharedExclusions
            val status=when{available.isEmpty()->"Choose music in Library to start Radio.";damaged->"Local recommendations · imported vectors need reimport";record==null->"Local recommendations · no catalog imported";accepted.isEmpty()->"Local recommendations · match songs to use imported metadata";legacyVectorTracks>0 && !legacyEnabled && diagnostics.textVectorTracks+diagnostics.audioVectorTracks==0->"Similarity is off · desktop vectors have unrecorded model provenance";legacyEnabled->"${accepted.size} matched · using legacy desktop similarity";else->"${accepted.size} matched · ${available.size-accepted.size} local only"}
            val vibeAvailable=accepted[playback.currentId]?.let{sid->vectors.any{it.songId==sid}}==true
            mutable.update{it.copy(loaded=true,busy=false,suggestions=if(publishPreview)suggestions else it.suggestions,suggestionAnchorTitle=if(publishPreview)available.firstOrNull{song->song.id==anchorLocalId}?.title else it.suggestionAnchorTitle,mood=effectiveMood,matched=accepted.size,localOnly=available.size-accepted.size,status=status,error=if(publishPreview && suggestions.size<count && previewExclusions.isNotEmpty())"Not enough different eligible songs for a full preview." else null,generation=it.generation+1,vibeAvailable=vibeAvailable,
                diagnostics=diagnostics,bootstrapCandidates=bootstrapCandidates,
                bootstrapLocalIds=if(diagnostics.hasUsableTaste)emptySet() else it.bootstrapLocalIds.intersect(bootstrapCandidates),
                catalogSnapshot=if(damaged)null else record?.manifestSha256,legacyVectorTracks=legacyVectorTracks,legacyVectorsEnabled=legacyEnabled)}
            suggestions
        } catch(cancelled:CancellationException){mutable.update{it.copy(busy=false)};throw cancelled}
        catch(_:Exception){mutable.update{it.copy(loaded=true,busy=false,error="Couldn't prepare local recommendations. Refresh to retry.")};null}
    }
}
