package dev.radio.mobile

import dev.radio.mobile.data.LocalSong
import dev.radio.mobile.portable.FeelRules
import dev.radio.mobile.recommendation.FeelSprint
import dev.radio.mobile.recommendation.SprintInput
import org.junit.Assert.*
import org.junit.Test
import kotlin.random.Random

class FeelSprintTest {
    private fun song(id: String, available: Boolean = true) = LocalSong(id, "tree", "folder", "content://fixture/$id", "$id.mp3", "Folder", 1, 1, 1, 1000, available)
    private val member = FeelRules.FeelState(0.8, 0.4, true)
    private val rejected = FeelRules.FeelState(0.2, 0.4, false)

    @Test fun excludesRatedAndUnavailableSongs() {
        val songs = listOf(song("a"), song("b"), song("c", available = false), song("d"))
        val deck = FeelSprint.deck(SprintInput(songs, states = mapOf("a" to member, "b" to rejected)), Random(1))
        assertEquals(listOf("d"), deck.map { it.id })
    }

    @Test fun coldStartFollowsHintTagsAndEnergy() {
        val songs = (1..6).map { song("s$it") }
        val input = SprintInput(songs, tags = mapOf("s4" to setOf("dreamy", "intimate"), "s2" to setOf("dreamy")),
            energy = mapOf("s4" to 0.2, "s2" to 0.9, "s5" to 0.1, "s1" to 0.9, "s3" to 0.9, "s6" to 0.9),
            hintTags = setOf("dreamy", "intimate"), hintEnergy = 0.0..0.35, randomShare = 0.0)
        val deck = FeelSprint.deck(input, Random(1)).map { it.id }
        assertEquals("s4", deck[0])
        assertEquals(setOf("s2", "s5"), deck.subList(1, 3).toSet())
    }

    @Test fun songsNearMembersComeFirst() {
        val songs = listOf(song("m"), song("near"), song("far"), song("plain"))
        val input = SprintInput(songs, states = mapOf("m" to member),
            vectors = mapOf("m" to floatArrayOf(1f, 0f), "near" to floatArrayOf(0.9f, 0.1f), "far" to floatArrayOf(0f, 1f)), randomShare = 0.0)
        val deck = FeelSprint.deck(input, Random(3)).map { it.id }
        assertEquals("near", deck.first())
        assertFalse("m" in deck)
    }

    @Test fun songsNearRejectionsDoNotRankAsMembers() {
        val songs = listOf(song("no"), song("yes"), song("byNo"), song("byYes"))
        val input = SprintInput(songs, states = mapOf("no" to rejected, "yes" to member),
            vectors = mapOf("no" to floatArrayOf(0f, 1f), "yes" to floatArrayOf(1f, 0f), "byNo" to floatArrayOf(0.05f, 1f), "byYes" to floatArrayOf(1f, 0.05f)), randomShare = 0.0)
        assertEquals(listOf("byYes", "byNo"), FeelSprint.deck(input, Random(3)).map { it.id })
    }

    @Test fun coPlayWithMembersRanksHigher() {
        val songs = listOf(song("m"), song("x"), song("y"), song("z"))
        val input = SprintInput(songs, states = mapOf("m" to member), sessions = listOf(listOf("m", "y"), listOf("y", "m", "z"), listOf("x")), randomShare = 0.0)
        assertEquals(listOf("y", "z", "x"), FeelSprint.deck(input, Random(1)).map { it.id })
    }

    @Test fun aboutOneFifthAreRandomAndDeckIsDistinctAndSized() {
        val songs = (1..100).map { song("s%03d".format(it)) }
        val energy = songs.associate { it.id to it.id.drop(1).toInt() / 100.0 }
        val input = SprintInput(songs, energy = energy, hintEnergy = 0.9..1.0, size = 20)
        val deck = FeelSprint.deck(input, Random(7)).map { it.id }
        assertEquals(20, deck.size)
        assertEquals(20, deck.toSet().size)
        val inHint = deck.count { energy.getValue(it) >= 0.9 }
        assertTrue("ranked part comes from the hint: $inHint", inHint >= 11)
        assertTrue("some random cards: $deck", deck.any { energy.getValue(it) < 0.85 })
        assertEquals(deck, FeelSprint.deck(input, Random(7)).map { it.id })
    }

    @Test fun onlyRestrictsToChosenSongs() {
        val songs = (1..5).map { song("s$it") }
        val deck = FeelSprint.deck(SprintInput(songs, only = setOf("s2", "s4")), Random(1))
        assertEquals(setOf("s2", "s4"), deck.map { it.id }.toSet())
    }

    @Test fun sessionsSplitOnLongGaps() {
        val starts = listOf("a" to 0L, "b" to 10 * 60_000L, "c" to 3 * 3_600_000L, "d" to 3 * 3_600_000L + 60_000L)
        assertEquals(listOf(listOf("a", "b"), listOf("c", "d")), FeelSprint.sessions(starts))
    }
}
