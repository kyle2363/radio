package dev.radio.mobile.portable

import dev.radio.mobile.portable.FeelRules.Comparison
import dev.radio.mobile.portable.FeelRules.Rating
import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** Parity with backend/portable/feel.py via backend/tests/fixtures/feel_golden.json. */
class FeelRulesTest {
    private val now = 1_800_000_000.0
    private val day = 86_400.0

    @Test fun goldenParity() {
        val file = listOf("../../backend/tests/fixtures/feel_golden.json", "../backend/tests/fixtures/feel_golden.json")
            .map(::File).first { it.exists() }
        @Suppress("UNCHECKED_CAST")
        val cases = (MiniJson(file.readText()).parse() as Map<String, Any?>)["score_feel"] as List<Map<String, Any?>>
        assertTrue("golden cases missing", cases.size >= 5)
        for (case in cases) {
            @Suppress("UNCHECKED_CAST")
            val ratings = (case["ratings"] as List<Map<String, Any?>>).map {
                Rating(it["song"] as String, it["value"] as Double, it["at"] as Double, it["weight"] as Double)
            }
            @Suppress("UNCHECKED_CAST")
            val comparisons = (case["comparisons"] as List<Map<String, Any?>>).map {
                Comparison(it["winner"] as String, it["loser"] as String, it["at"] as Double, it["tie"] as Boolean)
            }
            val states = FeelRules.scoreFeel(ratings, comparisons, case["now"] as Double)
            @Suppress("UNCHECKED_CAST")
            val expected = case["expected"] as Map<String, Map<String, Any?>>
            assertEquals(case["name"].toString(), expected.keys, states.keys)
            for ((song, want) in expected) {
                val got = states.getValue(song)
                assertEquals("${case["name"]}/$song score", want["score"] as Double, got.score, 1e-9)
                assertEquals("${case["name"]}/$song confidence", want["confidence"] as Double, got.confidence, 1e-9)
                assertEquals("${case["name"]}/$song member", want["member"] as Boolean, got.member)
            }
        }
    }

    @Test fun recencyHalvesAfterSixMonths() {
        assertEquals(0.5, FeelRules.recencyWeight(FeelRules.HALF_LIFE_DAYS), 1e-12)
        assertEquals(1.0, FeelRules.recencyWeight(-3.0), 0.0)
    }

    @Test fun neighboursOnlyFillUnratedSongs() {
        val states = FeelRules.scoreFeel(listOf(Rating("a", 1.0, now)), emptyList(), now)
        val borrowed = FeelRules.neighbourScores(states, mapOf(
            "a" to floatArrayOf(1f, 0f), "near" to floatArrayOf(0.9f, 0.1f), "far" to floatArrayOf(-1f, 0f)))
        assertEquals(setOf("near"), borrowed.keys)
        assertFalse(borrowed.getValue("near").member)
        assertTrue(borrowed.getValue("near").confidence < states.getValue("a").confidence)
    }

    @Test fun uncertainPairsPickCloseSongs() {
        val states = FeelRules.scoreFeel(
            listOf(Rating("a", 0.9, now), Rating("b", 0.88, now), Rating("c", 0.1, now)), emptyList(), now)
        assertEquals(setOf("a", "b"), FeelRules.uncertainPairs(states, emptySet(), 1).single().toList().toSet())
    }

    @Test fun settledRisesWithRounds() {
        val one = FeelRules.settledPercent(FeelRules.scoreFeel(emptyList(), listOf(Comparison("a", "b", now)), now))
        val many = FeelRules.settledPercent(FeelRules.scoreFeel(emptyList(), List(8) { Comparison("a", "b", now - it * day) }, now))
        assertTrue(one < many)
    }

    @Test fun energyThreshold() {
        assertTrue(FeelRules.energyChanged(null, 0.4))
        assertFalse(FeelRules.energyChanged(0.40, 0.41))
        assertTrue(FeelRules.energyChanged(0.40, 0.43))
    }
}

/** Minimal JSON reader for golden fixtures (objects, arrays, strings, numbers as Double, booleans, null). */
internal class MiniJson(private val s: String) {
    private var i = 0
    fun parse(): Any? { val v = value(); ws(); require(i == s.length) { "trailing data at $i" }; return v }
    private fun ws() { while (i < s.length && s[i].isWhitespace()) i++ }
    private fun value(): Any? {
        ws()
        return when (s[i]) {
            '{' -> { i++; val m = linkedMapOf<String, Any?>(); ws()
                if (s[i] == '}') { i++; return m }
                while (true) { ws(); val k = string(); ws(); i++; m[k] = value(); ws(); if (s[i++] == '}') return m } }
            '[' -> { i++; val l = mutableListOf<Any?>(); ws()
                if (s[i] == ']') { i++; return l }
                while (true) { l += value(); ws(); if (s[i++] == ']') return l } }
            '"' -> string()
            't' -> { i += 4; true }
            'f' -> { i += 5; false }
            'n' -> { i += 4; null }
            else -> { val start = i; while (i < s.length && s[i] in "+-0123456789.eE") i++; s.substring(start, i).toDouble() }
        }
    }
    private fun string(): String {
        i++; val b = StringBuilder()
        while (s[i] != '"') { if (s[i] == '\\') { i++; b.append(s[i]) } else b.append(s[i]); i++ }
        i++; return b.toString()
    }
}
