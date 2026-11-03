package dev.radio.mobile.recommendation

import java.security.MessageDigest
import java.util.Locale
import kotlin.math.*

data class RadioTrack(val songId:String,val localId:String,val artist:String="",val energy:Double=.5,val valence:Double=.5,val cluster:Int?=null,val folder:String="",val genres:Set<String> = emptySet(),val collections:Set<String> = emptySet(),val favorite:Boolean=false)
data class RadioVector(val songId:String,val space:String,val model:String,val version:String,val values:FloatArray)
data class RadioEvent(val id:String,val kind:String,val songId:String,val at:Double,val reason:String?=null)
data class RadioProfile(val events:List<RadioEvent> = emptyList(),val hearts:Set<String> = emptySet(),val dislikes:Set<String> = emptySet(),val current:String?=null,val energyHistory:List<Double> = emptyList(),val skipStreak:Int=0,val userEnergy:Map<String,Double> = emptyMap(),val bootstrap:Set<String> = emptySet())
data class RadioSettings(val mood:String="neutral",val audioBlend:Double=.65,val exploration:Double=.3,val vibeLock:String?=null,val folders:Set<String> = emptySet(),val genres:Set<String> = emptySet(),val collections:Set<String> = emptySet(),val favoritesOnly:Boolean=false,val energyTarget:Double?=null,val energyDirection:String="stable",val artistPenalty:Double=.12,val recentWindow:Double=1800.0,val skipWindow:Double=86400.0)
data class RadioPick(val songId:String,val localId:String,val score:Double,val energy:Double,val cluster:Int?,val slot:String="fallback",val textSimilarity:Double?=null,val audioSimilarity:Double?=null,val energyMatch:Double=0.0,val moodMatch:Double=0.0,val recentPenalty:Double=0.0,val skipPenalty:Double=0.0,val artistPenalty:Double=0.0,val vectorFallback:Boolean=true,val reason:String="Local choice",val evidence:String?=null)
data class RadioDiagnostics(val eligibleTracks:Int=0,val textVectorTracks:Int=0,val audioVectorTracks:Int=0,val tasteEvents:Int=0,val contributingTasteEvents:Int=0,val hasUsableTaste:Boolean=false,val bootstrapCandidates:Set<String> = emptySet())

/** Pure portable v1 scorer. No Android, storage, player or network dependencies. */
class OfflineRecommender(tracks:List<RadioTrack>,vectors:List<RadioVector>,private val seed:Int=0) {
    private val tracks=tracks.sortedBy{it.localId}.distinctBy{it.songId}.associateBy{it.songId}
    private data class Group(val space:String,val model:String,val version:String,val dimension:Int)
    private val groups=vectors.filter { it.space in setOf("text","audio") && it.values.isNotEmpty() && it.values.all(Float::isFinite) }
        .groupBy { Group(it.space,it.model,it.version,it.values.size) }.mapValues { (_,rows)->rows.mapNotNull { row->unit(row.values)?.let{row.songId to it} }.toMap() }
    private fun unit(v:FloatArray):FloatArray? { val norm=sqrt(v.fold(0.0){a,b->a+b.toDouble()*b.toDouble()});return if(norm.isFinite() && norm>1e-12) FloatArray(v.size){(v[it]/norm).toFloat()} else null }
    private fun fold(s:String)=s.lowercase(Locale.ROOT)
    private val tieKeys=mutableMapOf<String,String>()
    private fun tie(s:String)=tieKeys.getOrPut(s) {
        val digest=MessageDigest.getInstance("SHA-256").digest("$seed:$s".toByteArray(Charsets.UTF_8))
        val hex="0123456789abcdef"
        buildString(64){for(byte in digest){val value=byte.toInt() and 255;append(hex[value ushr 4]);append(hex[value and 15])}}
    }
    private fun energy(id:String,profile:RadioProfile):Double {val base=tracks[id]?.energy?.takeIf{it.isFinite()} ?: .5;return (profile.userEnergy[id]?.takeIf{it.isFinite()}?.let{base*.7+it*.3} ?: base).coerceIn(0.0,1.0)}
    private fun mood(name:String)=MOODS[name] ?: MOODS.getValue("neutral")
    private fun eligible(row:RadioTrack,profile:RadioProfile,settings:RadioSettings):Boolean = row.songId !in profile.dislikes && (!settings.favoritesOnly || row.songId in profile.hearts) &&
        (settings.folders.isEmpty() || fold(row.folder) in settings.folders.map(::fold)) &&
        (settings.genres.isEmpty() || row.genres.map(::fold).intersect(settings.genres.map(::fold).toSet()).isNotEmpty()) &&
        (settings.collections.isEmpty() || row.collections.map(::fold).intersect(settings.collections.map(::fold).toSet()).isNotEmpty())

    private fun vectorExists(id:String,space:String)=groups.any{(group,vectors)->group.space==space && id in vectors}

    private fun learnedTaste(group:Group,vectors:Map<String,FloatArray>,profile:RadioProfile,relevant:List<RadioEvent>):FloatArray? {
        val blended=FloatArray(group.dimension)
        val weights=if(profile.skipStreak>=3) listOf(.15,.20,.25,.40) else listOf(.40,.25,.20,.15)
        listOf(10 to .85,50 to .92,200 to .96,2000 to .99).forEachIndexed { index,(window,decay)->
            val total=FloatArray(group.dimension);var denominator=0.0
            relevant.takeLast(window).asReversed().forEachIndexed { position,event->vectors[event.songId]?.let { vector->
                val weight=SIGNALS.getValue(event.kind)*decay.pow(position); denominator+=abs(weight)
                for(i in total.indices) total[i]+=vector[i]*weight.toFloat()
            } }
            if(denominator>0) for(i in blended.indices) blended[i]+=(total[i]/denominator.toFloat())*weights[index].toFloat()
        }
        val favorites=profile.hearts.mapNotNull(vectors::get)
        if(favorites.isNotEmpty()) for(i in blended.indices) blended[i]+=favorites.sumOf{it[i].toDouble()}.toFloat()/favorites.size*.15f
        return unit(blended)
    }

    fun diagnostics(profile:RadioProfile,settings:RadioSettings,now:Double):RadioDiagnostics {
        require(now.isFinite())
        val eligibleIds=tracks.values.filter{eligible(it,profile,settings)}.map{it.songId}.toSet()
        val events=profile.events.filter{it.kind in SIGNALS}.distinctBy{it.id}.sortedWith(compareBy<RadioEvent>{it.at}.thenBy{it.id}).takeLast(2000)
        val contributing=events.count{event->groups.any{(group,vectors)->
            val enabled=if(group.space=="text") settings.audioBlend<1.0 else settings.audioBlend>0.0
            enabled && event.songId in vectors
        }}
        val hasLearnedTaste=groups.any{(group,vectors)->
            val enabled=if(group.space=="text") settings.audioBlend<1.0 else settings.audioBlend>0.0
            enabled && learnedTaste(group,vectors,profile,events)!=null && eligibleIds.any{it in vectors}
        }
        val bootstrap=eligibleIds.filter{id->vectorExists(id,"text") || vectorExists(id,"audio")}.toSet()
        return RadioDiagnostics(
            eligibleTracks=eligibleIds.size,
            textVectorTracks=eligibleIds.count{vectorExists(it,"text")},
            audioVectorTracks=eligibleIds.count{vectorExists(it,"audio")},
            tasteEvents=events.size,
            contributingTasteEvents=contributing,
            hasUsableTaste=hasLearnedTaste,
            bootstrapCandidates=bootstrap
        )
    }
    fun rank(profile:RadioProfile,settings:RadioSettings,now:Double):List<RadioPick> = rank(profile,settings,now,similarityOnly=false)

    private fun rank(profile:RadioProfile,settings:RadioSettings,now:Double,similarityOnly:Boolean):List<RadioPick> {
        require(now.isFinite())
        val relevant=profile.events.filter{it.kind in SIGNALS}.distinctBy{it.id}.sortedWith(compareBy<RadioEvent>{it.at}.thenBy{it.id}).takeLast(2000)
        val similarities=mutableMapOf<String,MutableMap<String,Double>>()
        val bases=mutableMapOf<String,MutableMap<String,String>>()
        for((group,vectors) in groups) {
            val learned=learnedTaste(group,vectors,profile,relevant)
            var taste=learned
            var bootstrapWasUsed=false
            if(taste==null && profile.bootstrap.isNotEmpty()) {
                val seeds=profile.bootstrap.mapNotNull(vectors::get)
                if(seeds.isNotEmpty()) {
                    taste=unit(FloatArray(group.dimension){index->seeds.sumOf{it[index].toDouble()}.div(seeds.size).toFloat()})
                    bootstrapWasUsed=taste!=null
                }
            }
            var query=taste
            var basis=if(bootstrapWasUsed) "Temporary starting songs" else "Matches listening taste"
            val vibe=vectors[settings.vibeLock]
            val current=vectors[profile.current]
            if(vibe!=null) {
                // Explicit lock stays stronger and blends with taste, not ordinary continuity.
                query=if(similarityOnly)vibe else unit(FloatArray(group.dimension){vibe[it]*.8f+(taste?.get(it) ?: 0f)*.2f})
                basis="Matches locked vibe"
            } else if(current!=null) {
                val weight=(.75-.4*settings.exploration.coerceIn(0.0,1.0)).toFloat()
                query=if(taste==null || similarityOnly)current else unit(FloatArray(group.dimension){(1f-weight)*taste[it]+weight*current[it]})
                basis=if(taste==null || similarityOnly)"Similar to current song" else if(bootstrapWasUsed) "Current song + temporary starting songs" else "Current song + listening taste"
            }
            val q=query ?: continue
            for((id,v) in vectors) if(id in tracks) {
                similarities.getOrPut(id){mutableMapOf()}[group.space]=v.indices.fold(0f){a,i->a+v[i]*q[i]}.toDouble()
                bases.getOrPut(id){mutableMapOf()}[group.space]=basis
            }
        }
        var target=(settings.energyTarget ?: mood(settings.mood).first)+when(settings.energyDirection){"rising"->.15;"falling"->-.15;else->0.0}
        profile.energyHistory.lastOrNull()?.let{target=target.coerceIn(it-.25,it+.25)};target=target.coerceIn(0.0,1.0)
        val valence=mood(settings.mood).second
        val recent=profile.events.filter{it.kind=="started"}.groupBy{it.songId}.mapValues{it.value.maxOf{e->e.at}}
        val skipped=profile.events.filter{it.kind in setOf("skipped","disliked")}.groupBy{it.songId}.mapValues{it.value.maxOf{e->e.at}}
        val currentArtist=tracks[profile.current]?.artist?.let(::fold).orEmpty()
        return tracks.values.filter { row->eligible(row,profile,settings) }.map { row->
            val sims=similarities[row.songId].orEmpty();val text=sims["text"];val audio=sims["audio"]
            val similarity=if(text!=null && audio!=null) text*(1-settings.audioBlend.coerceIn(0.0,1.0))+audio*settings.audioBlend.coerceIn(0.0,1.0) else text ?: audio ?: 0.0
            val em=1-abs(energy(row.songId,profile)-target);val mm=1-abs(row.valence-valence)
            fun penalty(last:Double?,window:Double,weight:Double)=if(last==null) 0.0 else weight*max(0.0,1-max(0.0,now-last)/max(1.0,window))
            val rp=penalty(recent[row.songId],settings.recentWindow,.35);val sp=penalty(skipped[row.songId],settings.skipWindow,.55)
            val ap=if(currentArtist.isNotEmpty() && fold(row.artist)==currentArtist) settings.artistPenalty else 0.0
            val cp=if(row.songId==profile.current && tracks.size>1) .5 else 0.0
            val score=.6*similarity+.2*em+.1*mm+(if(row.songId in profile.hearts || row.favorite) .1 else 0.0)-rp-sp-ap-cp
            // Describe the strongest contributing modality only when it supports similarity.
            // Similarity is model evidence, never a calibrated confidence percentage.
            val evidence=sims.entries.filter{(space,value)->similarity>.3 && value>.3 && (text==null || audio==null || if(space=="audio")settings.audioBlend>0 else settings.audioBlend<1)}
                .maxByOrNull{(space,value)->value*if(text!=null && audio!=null){if(space=="audio")settings.audioBlend.coerceIn(0.0,1.0) else 1-settings.audioBlend.coerceIn(0.0,1.0)}else 1.0}
            val reason=when {
                evidence!=null->bases[row.songId]?.get(evidence.key) ?: "Local choice"
                row.songId in profile.hearts || row.favorite->"Liked song"
                sims.isEmpty()->"Local choice"
                else->"Mood and energy ranking"
            }
            RadioPick(row.songId,row.localId,Math.rint(score*1e8)/1e8,energy(row.songId,profile),row.cluster,textSimilarity=text,audioSimilarity=audio,energyMatch=em,moodMatch=mm,recentPenalty=rp,skipPenalty=sp,artistPenalty=ap,vectorFallback=sims.isEmpty(),reason=reason,
                evidence=evidence?.let { if(it.key=="audio") "Audio similarity" else "Musical description similarity" })
        }.sortedWith(compareByDescending<RadioPick>{it.score}.thenBy{tie(it.songId)}.thenBy{it.songId})
    }
    fun recommend(profile:RadioProfile,settings:RadioSettings,now:Double,count:Int=5,excluded:Set<String> = emptySet()):List<RadioPick> {
        require(count in 0..100)
        val ranked=rank(profile,settings,now).filter{it.songId !in excluded};val current=tracks[profile.current]
        val chosen=allocateSlots(ranked,current?.cluster,current?.let{energy(it.songId,profile)},settings.exploration,settings.vibeLock!=null).toMutableList()
        chosen+=ranked.filter{row->chosen.none{it.songId==row.songId}}
        val selected=chosen.take(count).toMutableList();val used=selected.map{it.songId}.toMutableSet();val seen=mutableSetOf<String>()
        selected.indices.forEach { index-> val pick=selected[index];var artist=fold(tracks.getValue(pick.songId).artist)
            if(artist.isNotEmpty() && artist in seen) ranked.firstOrNull{it.songId !in used && fold(tracks.getValue(it.songId).artist) !in seen && it.score>=pick.score-.12}?.let { replacement->
                used.remove(pick.songId);used.add(replacement.songId);selected[index]=replacement.copy(slot=pick.slot);artist=fold(tracks.getValue(replacement.songId).artist)
            };if(artist.isNotEmpty()) seen.add(artist)
        }
        return selected
    }

    /** Builds an ordered queue whose transitions are re-scored from each preceding pick. */
    fun recommendSequence(
        profile:RadioProfile,
        settings:RadioSettings,
        now:Double,
        count:Int=5,
        excluded:Set<String> = emptySet(),
        recentRecommendations:List<String> = emptyList(),
        sequenceOffset:Int=0
    ):List<RadioPick> {
        require(now.isFinite())
        require(count in 0..100)
        require(sequenceOffset>=0)
        if(count==0) return emptyList()
        val hard=excluded + profile.dislikes
        val cooldown=mutableListOf<String>()
        // Caller order is oldest-to-newest. Preserve it when relaxing old recommendations.
        recentRecommendations.asReversed().filter{it !in hard}.distinct().asReversed().forEach(cooldown::add)
        val recentEvents=profile.events.filter{it.kind in setOf("started","skipped","disliked") && it.songId !in hard &&
            now-it.at>=0 && now-it.at<=if(it.kind=="started")settings.recentWindow else settings.skipWindow}
            .distinctBy{it.id}.sortedWith(compareBy<RadioEvent>{it.at}.thenBy{it.id})
        recentEvents.forEach{if(it.songId !in cooldown) cooldown.add(it.songId)}

        val exploration=settings.exploration.coerceIn(0.0,1.0)
        val greedy=exploration==0.0
        val schedule=if(greedy) listOf("exploit") else if(settings.vibeLock!=null) listOf("exploit","exploit","exploit","exploit","explore") else {
            val exploreCount=1+if(exploration>.6)1 else 0
            val wildcard=if(exploration>.4)1 else 0
            val exploit=(5-exploreCount-wildcard-1).coerceAtLeast(1)
            buildList { repeat(exploit){add("exploit")};repeat(exploreCount){add("explore")};if(wildcard>0)add("wildcard");add("context") }
        }
        val result=mutableListOf<RadioPick>()
        val used=mutableSetOf<String>()
        val anchorRanks=rank(profile,settings,now,similarityOnly=true).associateBy{it.songId}
        val tasteRanks=rank(profile.copy(current=null),settings.copy(vibeLock=null),now,similarityOnly=false).associateBy{it.songId}
        val hasTaste=diagnostics(profile,settings,now).hasUsableTaste
        var current=profile.current
        while(result.size<count) {
            val currentProfile=profile.copy(current=current)
            val available=rank(currentProfile,settings,now,similarityOnly=greedy).filter{
                it.songId !in hard && it.songId !in used &&
                    (!greedy || tracks.size==1 || (it.songId!=current && it.songId!=profile.current))
            }
            if(available.isEmpty()) break
            var fresh=available.filter{it.songId !in cooldown}
            while(fresh.isEmpty() && cooldown.isNotEmpty()) {
                cooldown.removeAt(0)
                fresh=available.filter{it.songId !in cooldown}
            }
            if(fresh.isEmpty()) fresh=available
            val slot=schedule[(sequenceOffset+result.size)%schedule.size]
            val currentTrack=current?.let(tracks::get)
            fun transitionCost(pick:RadioPick):Double {
                val row=tracks.getValue(pick.songId)
                var cost=0.0
                if(currentTrack!=null && currentTrack.cluster!=null && row.cluster!=currentTrack.cluster) cost+=.025
                if(currentTrack!=null && abs(pick.energy-energy(current!!,currentProfile))>.4) cost+=.015
                return (cost.coerceAtMost(.05) * if(settings.vibeLock!=null).4 else 1.0)
            }
            fun jitter(id:String):Double {
                val hex=tie("sequence:$sequenceOffset:${result.size}:$id").take(8).toLong(16)
                return (hex.toDouble()/0xffffffffL.toDouble()-.5)*.025
            }
            fun blendedSimilarity(pick:RadioPick):Double? {
                val text=pick.textSimilarity;val audio=pick.audioSimilarity
                val blend=settings.audioBlend.coerceIn(0.0,1.0)
                return if(text!=null && audio!=null)text*(1-blend)+audio*blend else text ?: audio
            }
            fun anchoredSimilarity(pick:RadioPick):Double? {
                val next=blendedSimilarity(pick) ?: return null
                val anchor=anchorRanks[pick.songId]?.let(::blendedSimilarity) ?: return next
                return if(result.isEmpty() || settings.vibeLock!=null) next else .65*next+.35*anchor
            }
            fun adjusted(pick:RadioPick):Double {
                val continuity=blendedSimilarity(pick)
                val anchored=anchoredSimilarity(pick)
                return pick.score+(if(continuity!=null && anchored!=null).6*(anchored-continuity) else 0.0)-transitionCost(pick)+jitter(pick.songId)
            }
            // Keep zero-exploration transitions near the best remaining seed
            // neighbors before ordering by continuity. Apply after cooldowns so
            // sparse libraries still make progress without replaying recent songs.
            val seedBest=if(greedy && profile.current!=null && settings.vibeLock==null)
                fresh.mapNotNull{anchorRanks[it.songId]?.let(::blendedSimilarity)}.maxOrNull() else null
            val selectionPool=if(seedBest!=null) fresh.filter {
                val seedSimilarity=anchorRanks[it.songId]?.let(::blendedSimilarity)
                seedSimilarity!=null && seedSimilarity>=seedBest-ZERO_SEED_WINDOW
            } else fresh
            // Taste only breaks near ties; a broad neighborhood can change style.
            fun similarity(pick:RadioPick)=anchoredSimilarity(pick) ?: Double.NEGATIVE_INFINITY
            val closest=selectionPool.maxOf(::similarity)
            val close=if(greedy && hasTaste && closest.isFinite()) selectionPool.filter{similarity(it)>=closest-ZERO_TASTE_TIE} else selectionPool
            val scoreOrder=if(greedy && hasTaste && closest.isFinite()) close.sortedWith(
                compareByDescending<RadioPick>{tasteRanks[it.songId]?.let(::blendedSimilarity) ?: -1.0}
                    .thenByDescending{similarity(it)}.thenBy{tie(it.songId)}.thenBy{it.songId})
                else if(greedy)selectionPool.sortedWith(compareByDescending<RadioPick>{similarity(it)}
                    .thenByDescending{it.score}.thenBy{tie(it.songId)}.thenBy{it.songId})
                else fresh.sortedWith(compareByDescending<RadioPick>{adjusted(it)}.thenBy{it.songId})
            val best=adjusted(scoreOrder.first())
            val ordered=when(slot) {
                "explore" -> {
                    val nearBest=scoreOrder.filter{adjusted(it)>=best-.10}
                    val varied=nearBest.filter{currentTrack?.cluster!=null && it.cluster!=currentTrack.cluster}
                    (if(varied.isNotEmpty())varied else nearBest).sortedWith(compareByDescending<RadioPick>{adjusted(it)}.thenBy{it.songId})
                }
                "wildcard" -> {
                    val nearBest=scoreOrder.filter{adjusted(it)>=best-.15}
                    nearBest.sortedWith(compareBy<RadioPick>{it.textSimilarity ?: it.audioSimilarity ?: 0.0}.thenByDescending{adjusted(it)}.thenBy{it.songId})
                }
                "context" -> {
                    val nearBest=scoreOrder.filter{adjusted(it)>=best-.12}
                    if(currentTrack==null) nearBest else nearBest.sortedWith(compareBy<RadioPick>{abs(it.energy-currentTrack.energy)}.thenByDescending{adjusted(it)}.thenBy{it.songId})
                }
                else -> scoreOrder
            }
            val rankedPick=ordered.first()
            val reason=if(result.isEmpty()) when(rankedPick.reason) {
                "Similar to current song" -> "Similar to starting song"
                "Current song + listening taste" -> "Starting song + listening taste"
                "Current song + temporary starting songs" -> "Starting song + temporary starting songs"
                else -> rankedPick.reason
            } else when(rankedPick.reason) {
                "Similar to current song" -> "Similar to previous suggestion"
                "Current song + listening taste" -> "Previous suggestion + listening taste"
                "Current song + temporary starting songs" -> "Previous suggestion + temporary starting songs"
                else -> rankedPick.reason
            }
            val evidence=listOfNotNull(rankedPick.evidence,
                if(greedy && hasTaste && closest.isFinite() && close.size>1) "Listening taste" else null)
                .joinToString(" · ").ifEmpty{null}
            val pick=rankedPick.copy(slot=slot,reason=reason,evidence=evidence)
            result+=pick
            used+=pick.songId
            current=pick.songId
        }
        return result
    }
    companion object {
        private const val ZERO_TASTE_TIE=0.0001
        private const val ZERO_SEED_WINDOW=0.04
        val MOODS=linkedMapOf("chill" to (.25 to .55),"neutral" to (.5 to .5),"energetic" to (.8 to .65),"focus" to (.4 to .5),"melancholy" to (.3 to .25),"discovery" to (.55 to .55))
        private val SIGNALS=mapOf("completed" to 1.0,"skipped" to -.6,"replayed" to 1.3,"hearted" to 1.5,"unhearted" to -.3,"disliked" to -1.0)
        fun allocateSlots(ranked:List<RadioPick>,currentCluster:Int?,currentEnergy:Double?,exploration:Double=.3,vibe:Boolean=false):List<RadioPick> {
            val ordered=ranked.sortedByDescending{it.score}
            val selected=mutableListOf<RadioPick>();val used=mutableSetOf<String>()
            fun take(slot:String,count:Int=1,predicate:(RadioPick)->Boolean={true}):Boolean {val candidates=ordered.filter{it.songId !in used && predicate(it)}.take(count);selected+=candidates.map{it.copy(slot=slot)};used+=candidates.map{it.songId};return candidates.isNotEmpty()}
            if(vibe){take("exploit",4);take("explore");return selected}
            val explore=1+if(exploration>.6)1 else 0
            val wildcard=if(exploration>.4)1 else 0
            take("exploit",5-explore-wildcard-1)
            fun varied(slot:String,count:Int) {
                take(slot,count){it.cluster!=currentCluster}
                val remaining=count-selected.count{it.slot==slot}
                if(remaining>0) take(slot,remaining)
            }
            varied("explore",explore);varied("wildcard",wildcard)
            if(currentEnergy==null || !take("context"){abs(it.energy-currentEnergy)<.2}) take("context")
            return selected
        }
    }
}
