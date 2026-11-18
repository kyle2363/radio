package dev.radio.mobile.portable

import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** Parity with backend/portable/playlists.py via backend/tests/fixtures/playlist_golden.json. */
class PlaylistSyncRulesTest {
    private data class Case(val a: String?, val b: String?, val key: String)

    private fun golden(): List<Case> {
        val file = listOf("../../backend/tests/fixtures/playlist_golden.json", "../backend/tests/fixtures/playlist_golden.json")
            .map(::File).first { it.exists() }
        val row = Regex("""\{"a":\s*(null|"[0-9a-z]*"),\s*"b":\s*(null|"[0-9a-z]*"),\s*"key":\s*"([0-9a-z]+)"\}""")
        fun value(token: String) = if (token == "null") null else token.trim('"')
        return row.findAll(file.readText()).map { Case(value(it.groupValues[1]), value(it.groupValues[2]), it.groupValues[3]) }.toList()
            .also { assertTrue("golden cases missing", it.size >= 5) }
    }

    @Test fun keyBetweenMatchesServerGolden() {
        for (case in golden()) {
            val key = PlaylistSyncRules.keyBetween(case.a, case.b)
            assertEquals(case.toString(), case.key, key)
            assertTrue(PlaylistSyncRules.validKey(key))
        }
    }

    @Test fun repeatedInsertsStayOrdered() {
        val keys = mutableListOf(PlaylistSyncRules.keyBetween(null, null))
        repeat(40) { keys.add(1, PlaylistSyncRules.keyBetween(keys[0], keys.getOrNull(1))) }
        assertEquals(keys.drop(1).sorted(), keys.drop(1))
        assertTrue(keys.all(PlaylistSyncRules::validKey))
    }

    @Test fun invalidKeysAreRejected() {
        for (bad in listOf("", "A", "g0", "a-b", "z".repeat(65))) assertFalse(bad, PlaylistSyncRules.validKey(bad))
    }

    @Test fun moveEmitsOnlyTheMovedMember() {
        val old = linkedMapOf("1" to "g", "2" to "m", "3" to "t")
        val changes = PlaylistSyncRules.changes(old, listOf("3", "1", "2"))
        assertEquals(1, changes.size)
        assertEquals("3", changes[0].member); assertTrue(changes[0].present)
        assertTrue(changes[0].position < "g")
    }

    @Test fun addAndRemoveEmitPresenceChanges() {
        val changes = PlaylistSyncRules.changes(linkedMapOf("1" to "g", "2" to "m"), listOf("1", "local:x"))
        assertEquals(setOf(PlaylistSyncRules.MemberChange("2", false, "m")), changes.filter { !it.present }.toSet())
        val added = changes.single { it.present }
        assertEquals("local:x", added.member); assertTrue(added.position > "g")
    }

    @Test fun firstShareAssignsIncreasingKeys() {
        val changes = PlaylistSyncRules.changes(emptyMap(), listOf("5", "local:a", "2"))
        assertEquals(listOf("5", "local:a", "2"), changes.map { it.member })
        assertEquals(changes.map { it.position }.sorted(), changes.map { it.position })
    }

    @Test fun stateOrdersByPositionThenMemberAndSkipsAbsent() {
        val registers = mapOf(
            "playlist_name:p" to "\"Trip\"",
            "playlist_member:p:2" to """{"position":"g","present":true}""",
            "playlist_member:p:1" to """{"position":"g","present":true}""",
            "playlist_member:p:local:z" to """{"position":"a","present":true}""",
            "playlist_member:p:9" to """{"position":"b","present":false}""",
            "playlist_member:other:7" to """{"position":"b","present":true}""",
        )
        val state = PlaylistSyncRules.state("p", registers)
        assertEquals("Trip", state.name); assertFalse(state.deleted)
        assertEquals(listOf("local:z", "1", "2"), state.members.map { it.first })
        assertEquals(mapOf("local:z" to "a", "1" to "g", "2" to "g"), state.positions)
        assertTrue(PlaylistSyncRules.state("p", mapOf("playlist_deleted:p" to "true")).deleted)
    }

    @Test fun eventKindAndMemberKeyFollowServerRules() {
        assertEquals("playlist_member:p:12", PlaylistSyncRules.registerKey("playlist_member_changed", "p", "12", null))
        assertEquals("playlist_member:p:local:abc", PlaylistSyncRules.registerKey("playlist_member_changed", "p", null, "abc"))
        assertEquals("playlist_name:p", PlaylistSyncRules.registerKey("playlist_renamed", "p", null, null))
        assertEquals("playlist_deleted:p", PlaylistSyncRules.registerKey("playlist_deleted", "p", null, null))
    }
}
