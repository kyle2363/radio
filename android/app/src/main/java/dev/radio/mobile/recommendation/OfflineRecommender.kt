package dev.radio.mobile.recommendation

import java.security.MessageDigest
import java.util.Locale
import kotlin.math.*

data class RadioTrack(val songId:String,val localId:String,val artist:String="",val energy:Double=.5,val valence:Double=.5,val cluster:Int?=null,val folder:String="",val genres:Set<String> = emptySet(),val collections:Set<String> = emptySet(),val favorite:Boolean=false)
data class RadioVector(val songId:String,val space:String,val model:String,val version:String,val values:FloatArray)
data class RadioEvent(val id:String,val kind:String,val songId:String,val at:Double,val reason:String?=null)
data class RadioProfile(val events:List<RadioEvent> = emptyList(),val hearts:Set<String> = emptySet(),val dislikes:Set<String> = emptySet(),val current:String?=null,val energyHistory:List<Double> = emptyList(),val skipStreak:Int=0,val userEnergy:Map<String,Double> = emptyMap())
data class RadioSettings(val mood:String="neutral",val audioBlend:Double=.65,val exploration:Double=.3,val vibeLock:String?=null,val folders:Set<String> = emptySet(),val genres:Set<String> = emptySet(),val collections:Set<String> = emptySet(),val favoritesOnly:Boolean=false,val energyTarget:Double?=null,val energyDirection:String="stable",val artistPenalty:Double=.12,val recentWindow:Double=1800.0,val skipWindow:Double=86400.0)
data class RadioPick(val songId:String,val localId:String,val score:Double,val energy:Double,val cluster:Int?,val slot:String="fallback",val textSimilarity:Double?=null,val audioSimilarity:Double?=null,val energyMatch:Double=0.0,val moodMatch:Double=0.0,val recentPenalty:Double=0.0,val skipPenalty:Double=0.0,val artistPenalty:Double=0.0,val vectorFallback:Boolean=true)

/** Pure portable v1 scorer. No Android, storage, player or network dependencies. */
class OfflineRecommender(tracks:List<RadioTrack>,vectors:List<RadioVector>,private val seed:Int=0) {
    private val tracks=tracks.sortedBy{it.localId}.distinctBy{it.songId}.associateBy{it.songId}
    private data class Group(val space:String,val model:String,val version:String,val dimension:Int)
    private val groups=vectors.filter { it.space in setOf("text","audio") && it.values.isNotEmpty() && it.values.all(Float::isFinite) }
        .groupBy { Group(it.space,it.model,it.version,it.values.size) }.mapValues { (_,rows)->rows.mapNotNull { row->unit(row.values)?.let{row.songId to it} }.toMap() }
    private fun unit(v:FloatArray):FloatArray? { val norm=sqrt(v.fold(0f){a,b->a+b*b}.toDouble()).toFloat();return if(norm>1e-12f) FloatArray(v.size){v[it]/norm} else null }
    private fun fold(s:String)=s.lowercase(Locale.ROOT)
    private fun tie(s:String)=MessageDigest.getInstance("SHA-256").digest("$seed:$s".toByteArray(Charsets.UTF_8)).joinToString(""){"%02x".format(it)}
    private fun energy(id:String,profile:RadioProfile):Double {val base=tracks[id]?.energy?.takeIf{it.isFinite()} ?: .5;return (profile.userEnergy[id]?.takeIf{it.isFinite()}?.let{base*.7+it*.3} ?: base).coerceIn(0.0,1.0)}
    private fun mood(name:String)=MOODS[name] ?: MOODS.getValue("neutral")
    fun rank(profile:RadioProfile,settings:RadioSettings,now:Double):List<RadioPick> {
        require(now.isFinite())
        val relevant=profile.events.filter{it.kind in SIGNALS}.distinctBy{it.id}.sortedWith(compareBy<RadioEvent>{it.at}.thenBy{it.id}).takeLast(2000)
        val similarities=mutableMapOf<String,MutableMap<String,Double>>()
        val weights=if(profile.skipStreak>=3) listOf(.15,.20,.25,.40) else listOf(.40,.25,.20,.15)
        for((group,vectors) in groups) {
            val blended=FloatArray(group.dimension)
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
            var query=unit(blended)
            vectors[settings.vibeLock]?.let { anchor->query=unit(FloatArray(group.dimension){anchor[it]*.8f+(query?.get(it) ?: 0f)*.2f}) }
            val q=query ?: continue
            for((id,v) in vectors) if(id in tracks) similarities.getOrPut(id){mutableMapOf()}[group.space]=v.indices.fold(0f){a,i->a+v[i]*q[i]}.toDouble()
        }
        var target=(settings.energyTarget ?: mood(settings.mood).first)+when(settings.energyDirection){"rising"->.15;"falling"->-.15;else->0.0}
        profile.energyHistory.lastOrNull()?.let{target=target.coerceIn(it-.25,it+.25)};target=target.coerceIn(0.0,1.0)
        val valence=mood(settings.mood).second
        val recent=profile.events.filter{it.kind=="started"}.groupBy{it.songId}.mapValues{it.value.maxOf{e->e.at}}
        val skipped=profile.events.filter{it.kind in setOf("skipped","disliked")}.groupBy{it.songId}.mapValues{it.value.maxOf{e->e.at}}
        val currentArtist=tracks[profile.current]?.artist?.let(::fold).orEmpty()
        return tracks.values.filter { row->row.songId !in profile.dislikes && (!settings.favoritesOnly || row.songId in profile.hearts) &&
            (settings.folders.isEmpty() || fold(row.folder) in settings.folders.map(::fold)) &&
            (settings.genres.isEmpty() || row.genres.map(::fold).intersect(settings.genres.map(::fold).toSet()).isNotEmpty()) &&
            (settings.collections.isEmpty() || row.collections.map(::fold).intersect(settings.collections.map(::fold).toSet()).isNotEmpty()) }.map { row->
            val sims=similarities[row.songId].orEmpty();val text=sims["text"];val audio=sims["audio"]
            val similarity=if(text!=null && audio!=null) text*(1-settings.audioBlend.coerceIn(0.0,1.0))+audio*settings.audioBlend.coerceIn(0.0,1.0) else text ?: audio ?: 0.0
            val em=1-abs(energy(row.songId,profile)-target);val mm=1-abs(row.valence-valence)
            fun penalty(last:Double?,window:Double,weight:Double)=if(last==null) 0.0 else weight*max(0.0,1-max(0.0,now-last)/max(1.0,window))
            val rp=penalty(recent[row.songId],settings.recentWindow,.35);val sp=penalty(skipped[row.songId],settings.skipWindow,.55)
            val ap=if(currentArtist.isNotEmpty() && fold(row.artist)==currentArtist) settings.artistPenalty else 0.0
            val cp=if(row.songId==profile.current && tracks.size>1) .5 else 0.0
            val score=.6*similarity+.2*em+.1*mm+(if(row.songId in profile.hearts || row.favorite) .1 else 0.0)-rp-sp-ap-cp
            RadioPick(row.songId,row.localId,Math.rint(score*1e8)/1e8,energy(row.songId,profile),row.cluster,textSimilarity=text,audioSimilarity=audio,energyMatch=em,moodMatch=mm,recentPenalty=rp,skipPenalty=sp,artistPenalty=ap,vectorFallback=sims.isEmpty())
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
    companion object {
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
