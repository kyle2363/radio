package dev.radio.mobile.portable
import org.junit.Assert.*
import org.junit.Test
class ConservativeMatcherTest {
 @Test fun renamedBytesAndDuplicates() {
 val local=MatchTrack("local","renamed.mp3",hash="a".repeat(64)); val song=MatchTrack("one","",hash=local.hash)
 assertEquals("high_confidence",ConservativeMatcher.match(local,listOf(song)).status)
 assertEquals("ambiguous",ConservativeMatcher.match(local,listOf(song,song.copy(id="two"))).status)
 }
 @Test fun filenameNeverAutoConfirms() {
 assertEquals("ambiguous",ConservativeMatcher.match(MatchTrack("local","Artist - Song.mp3"),listOf(MatchTrack("one","",title="Song",artist="Artist"))).status)
 }
 @Test fun durationConflictNeedsReview() {
 val local=MatchTrack("local","",title="Song",artist="Artist",album="Album",duration=200.0)
 assertEquals("ambiguous",ConservativeMatcher.match(local,listOf(local.copy(id="one",duration=230.0))).status)
 }
 @Test fun filenameSuffixIsReviewOnlyAndVersionWordsAreRetained() {
  val song=MatchTrack("one","",title="Song",artist="Artist",album="Album",duration=200.0)
  assertEquals("ambiguous",ConservativeMatcher.match(MatchTrack("local","Artist - Song (Live 2020).mp3"),listOf(song)).status)
  assertEquals("ambiguous",ConservativeMatcher.match(song.copy(id="local",album="Album Live 2020"),listOf(song)).status)
 }
 @Test fun malformedIdentifiersAndMissingDurationAreNotAutomaticEvidence() {
  val song=MatchTrack("one","",recordingId="1",isrc="bad",duration=200.0)
  assertEquals("unmatched",ConservativeMatcher.match(song.copy(id="local"),listOf(song)).status)
  val recording=song.copy(recordingId="musicbrainz:recording",isrc=null)
  assertEquals("high_confidence",ConservativeMatcher.match(recording.copy(id="local"),listOf(recording)).status)
  assertEquals("ambiguous",ConservativeMatcher.match(recording.copy(id="local",duration=null),listOf(recording)).status)
 }
}
