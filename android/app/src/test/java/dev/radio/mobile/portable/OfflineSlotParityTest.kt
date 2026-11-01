package dev.radio.mobile.portable

import dev.radio.mobile.recommendation.OfflineRecommender
import dev.radio.mobile.recommendation.RadioPick
import org.junit.Assert.assertEquals
import org.junit.Test

class OfflineSlotParityTest {
    private fun CatalogJson.Obj.value(key: String): CatalogJson = this[key]
    private fun CatalogJson.Obj.string(key: String) = (value(key) as CatalogJson.Str).value
    private fun CatalogJson.Obj.number(key: String) = (value(key) as CatalogJson.Num).token.toDouble()
    private fun CatalogJson.Obj.integerOrNull(key: String) = when (val item = value(key)) {
        CatalogJson.Null -> null
        is CatalogJson.Num -> item.token.toInt()
        else -> error("Unexpected integer fixture field")
    }
    private fun CatalogJson.Obj.boolean(key: String) = (value(key) as CatalogJson.Bool).value

    @Test fun slotSelectionMatchesPythonReferenceAcrossRepresentativeMatrix() {
        val input = javaClass.classLoader!!.getResourceAsStream("portable_slots_parity.json")!!
            .use { it.readBytes() }
        val fixture = CatalogJson.parse(input).obj()
        val cases = fixture.array("cases")
        assertEquals(120, cases.size)
        cases.forEachIndexed { caseIndex, raw ->
            val case = raw.obj()
            val cluster = case.integerOrNull("cluster")
            val exploration = case.number("exploration")
            val vibe = case.boolean("vibe")
            val ranked = case.array("candidates").map { candidateRaw ->
                val candidate = candidateRaw.obj()
                RadioPick(candidate.string("song_id"), candidate.string("song_id"),
                    candidate.number("score"), candidate.number("energy"), candidate.integerOrNull("cluster_id"))
            }
            val expected = case.array("expected").map { expectedRaw ->
                val row = expectedRaw.obj()
                row.string("song_id") to row.string("slot")
            }
            val actual = OfflineRecommender.allocateSlots(ranked, cluster, .5, exploration, vibe)
                .map { it.songId to it.slot }
            assertEquals("Python portable slot parity case $caseIndex", expected, actual)
        }
    }
}
