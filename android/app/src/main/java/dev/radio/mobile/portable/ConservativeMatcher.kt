package dev.radio.mobile.portable

import java.text.Normalizer
import java.util.Locale

data class MatchTrack(val id: String, val fileName: String, val title: String = "", val artist: String = "", val album: String = "", val duration: Double? = null, val hash: String? = null, val recordingId: String? = null, val isrc: String? = null)
data class MatchCandidate(val songId: String, val title: String, val artist: String, val confidence: Double, val reasons: List<String>)
data class MatchResult(val localId: String, val fileName: String, val status: String, val songId: String? = null, val candidates: List<MatchCandidate> = emptyList())

/** Version words are retained; filename evidence alone always requires review. */
object ConservativeMatcher {
 fun normalize(value: String): String = Regex("[\\p{L}\\p{N}_]+").findAll(Normalizer.normalize(value, Normalizer.Form.NFKC).lowercase(Locale.ROOT).replace("ß", "ss").replace('ς','σ')).joinToString(" ") { it.value }
 private fun versions(track: MatchTrack): Set<String> {
  val words=normalize(track.title+" "+track.album).split(' ')
  val found=words.filter { it.matches(Regex("live|remaster.*|remix.*|acoustic|instrumental|karaoke|cover|edit|extended|demo|mono|stereo|radio|studio")) }.toMutableSet()
  if(found.isNotEmpty()) found.addAll(words.filter { it.matches(Regex("(?:19|20)\\d{2}")) })
  return found
 }
 private fun token(value: String?, isrc: Boolean): String? {
  val text=value?.trim()?.lowercase(Locale.ROOT) ?: return null
  if(isrc) return text.replace(Regex("[-\\s]"),"").takeIf { it.matches(Regex("[a-z]{2}[a-z0-9]{3}\\d{7}")) }
  return text.takeIf { ':' in it || it.matches(Regex("[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")) }
 }
 fun match(local: MatchTrack, songs: List<MatchTrack>): MatchResult {
  val candidates=songs.mapNotNull { song ->
   val reasons=mutableListOf<String>(); var confidence=0.0
   val delta=if((local.duration ?: 0.0)>0 && (song.duration ?: 0.0)>0) kotlin.math.abs(local.duration!!-song.duration!!) else null
   val exact=local.hash?.matches(Regex("[a-fA-F0-9]{64}"))==true && local.hash.equals(song.hash,true)
   if(exact) {confidence=1.0; reasons.add("exact_content_sha256")} else {
    for((left,right,name) in listOf(Triple(token(local.recordingId,false),token(song.recordingId,false),"same_recording_id"),Triple(token(local.isrc,true),token(song.isrc,true),"same_isrc"))) {
     if(left!=null && left==right) {reasons.add(name); confidence=maxOf(confidence,if(delta!=null && delta<=2) .97 else .86)}
    }
    val title=normalize(local.title); val artist=normalize(local.artist)
    if(title.isNotEmpty() && title==normalize(song.title)) {
     if(artist.isNotEmpty() && artist==normalize(song.artist)) {
      reasons.addAll(listOf("same_artist","same_title")); val album=normalize(local.album).isNotEmpty() && normalize(local.album)==normalize(song.album)
      if(album) reasons.add("same_album"); if(delta!=null && delta<=2) reasons.add("duration_within_2s")
      confidence=maxOf(confidence,if(album && delta!=null && delta<=2) .94 else .79)
     } else {reasons.add("same_title_only"); confidence=maxOf(confidence,.45)}
    }
    val stem=normalize(local.fileName.replace(Regex("(?i)\\.mp3$"),"")); val full=normalize(song.artist+" "+song.title)
    if(full.isNotEmpty() && (stem==full || (full.length>=5 && stem.startsWith("$full ")))) { reasons.add(if(stem==full) "filename_artist_title" else "filename_suffix_requires_review"); confidence=maxOf(confidence,.65) }
    if(reasons.isNotEmpty() && delta!=null && delta>maxOf(5.0,(song.duration ?: 0.0)*.03)) {reasons.add("duration_conflict"); confidence=minOf(confidence,.6)}
    if(reasons.isNotEmpty() && versions(local)!=versions(song)) {reasons.add("recording_version_conflict"); confidence=minOf(confidence,.6)}
   }
   if(confidence==0.0) null else MatchCandidate(song.id,song.title,song.artist,confidence,reasons)
  }.sortedWith(compareByDescending<MatchCandidate>{it.confidence}.thenBy{it.songId})
  val best=candidates.firstOrNull(); val unique=candidates.size<2 || best!!.confidence-candidates[1].confidence>=.05-1e-9
  val status=if(best==null) "unmatched" else if(best.confidence>=.94 && unique) "high_confidence" else "ambiguous"
  return MatchResult(local.id,local.fileName,status,if(status=="high_confidence") best?.songId else null,candidates.take(10))
 }
}
