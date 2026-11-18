package dev.radio.mobile.portable

/**
 * Playlist-sync rules shared with backend/portable/playlists.py (checkpoint 7A).
 *
 * Name, deletion and each member are last-writer-wins registers. Member order uses
 * fractional position keys: lowercase base-36, compared as strings, never ending in
 * "0". Members sort by (position, member key). A member key is a catalog song id or
 * "local:<id>" for a song the server cannot identify.
 */
object PlaylistSyncRules {
    const val NAME_LIMIT = 80
    val KINDS = setOf("playlist_renamed", "playlist_deleted", "playlist_member_changed")
    private const val DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
    private val KEY = Regex("^[0-9a-z]{0,63}[1-9a-z]$")
    private val UUID_TEXT = Regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

    data class MemberChange(val member: String, val present: Boolean, val position: String)
    data class State(val name: String?, val deleted: Boolean, val members: List<Pair<String, String>>) {
        val positions: Map<String, String> get() = members.toMap()
    }

    fun validKey(key: String) = KEY.matches(key)
    fun validPlaylistId(id: String) = UUID_TEXT.matches(id)
    fun validName(name: String) = name.length in 1..NAME_LIMIT && name.trim() == name && name.none { it.code < 32 }
    fun memberKey(songId: String?, localId: String?): String = songId ?: "local:${requireNotNull(localId)}"

    fun registerKey(kind: String, playlistId: String, songId: String?, localId: String?): String = when (kind) {
        "playlist_renamed" -> "playlist_name:$playlistId"
        "playlist_deleted" -> "playlist_deleted:$playlistId"
        "playlist_member_changed" -> "playlist_member:$playlistId:${memberKey(songId, localId)}"
        else -> throw IllegalArgumentException("Not a playlist event")
    }

    fun keyBetween(lower: String?, upper: String?): String {
        val a = lower.orEmpty()
        var b = upper
        require((a.isEmpty() || validKey(a)) && (b == null || validKey(b) && a < b)) { "Invalid position bounds" }
        val result = StringBuilder()
        var i = 0
        while (true) {
            val low = if (i < a.length) DIGITS.indexOf(a[i]) else 0
            val high = if (b != null && i < b.length) DIGITS.indexOf(b[i]) else DIGITS.length
            if (low == high) result.append(DIGITS[low])
            else {
                val middle = (low + high) / 2
                if (middle > low) return result.append(DIGITS[middle]).toString()
                result.append(DIGITS[low])
                b = null // every continuation of this prefix already sorts before the upper bound
            }
            i++
        }
    }

    /**
     * Position keys for [desired]. The longest increasing run of [current] keys is kept
     * (earliest such run on ties), so moving one member changes one key.
     */
    fun reposition(desired: List<String>, current: Map<String, String>): Map<String, String> {
        val known = desired.indices.filter { current[desired[it]] != null }
        // Patience longest-increasing-subsequence over the known keys.
        val tails = mutableListOf<Int>(); val parent = IntArray(known.size) { -1 }
        known.forEachIndexed { slot, index ->
            val key = current.getValue(desired[index])
            var lo = 0; var hi = tails.size
            while (lo < hi) { val mid = (lo + hi) / 2; if (current.getValue(desired[known[tails[mid]]]) < key) lo = mid + 1 else hi = mid }
            if (lo > 0) parent[slot] = tails[lo - 1]
            if (lo == tails.size) tails += slot else tails[lo] = slot
        }
        val kept = HashSet<Int>()
        var cursor = tails.lastOrNull() ?: -1
        while (cursor >= 0) { kept += known[cursor]; cursor = parent[cursor] }
        val result = LinkedHashMap<String, String>()
        var previous: String? = null
        desired.forEachIndexed { index, member ->
            previous = if (index in kept) current.getValue(member)
            else keyBetween(previous, ((index + 1) until desired.size).firstOrNull { it in kept }?.let { current.getValue(desired[it]) })
            result[member] = previous!!
        }
        return result
    }

    /**
     * Changes needed to turn the known members [current] (member to position) into [desired].
     * Members this phone cannot show are not in [current] and are never touched.
     */
    fun changes(current: Map<String, String>, desired: List<String>): List<MemberChange> {
        require(desired.toSet().size == desired.size) { "Duplicate playlist member" }
        val positions = reposition(desired, current.filterKeys { it in desired })
        val updates = desired.mapNotNull { member ->
            positions.getValue(member).takeIf { it != current[member] }?.let { MemberChange(member, true, it) }
        }
        val removals = current.filterKeys { it !in positions }.map { (member, position) -> MemberChange(member, false, position) }
        return updates + removals
    }

    /** Winning register values (key to canonical JSON value) to one playlist's state. */
    fun state(playlistId: String, registers: Map<String, String>): State {
        var name: String? = null
        var deleted = false
        val members = mutableListOf<Pair<String, String>>()
        val prefix = "playlist_member:$playlistId:"
        for ((key, json) in registers) {
            val value = runCatching { CatalogJson.parseStrict(json.toByteArray(Charsets.UTF_8)) }.getOrNull() ?: continue
            when {
                key == "playlist_name:$playlistId" -> name = (value as? CatalogJson.Str)?.value
                key == "playlist_deleted:$playlistId" -> deleted = (value as? CatalogJson.Bool)?.value == true
                key.startsWith(prefix) -> {
                    val fields = (value as? CatalogJson.Obj)?.fields ?: continue
                    val position = (fields["position"] as? CatalogJson.Str)?.value ?: continue
                    if ((fields["present"] as? CatalogJson.Bool)?.value == true && validKey(position))
                        members += key.removePrefix(prefix) to position
                }
            }
        }
        return State(name, deleted, members.sortedWith(compareBy({ it.second }, { it.first })))
    }

    fun playlistIds(registerKeys: Iterable<String>): Set<String> = registerKeys.mapNotNull { key ->
        if (!key.startsWith("playlist_")) null else key.split(":").getOrNull(1)?.takeIf(::validPlaylistId)
    }.toSet()

    /** Canonical register value for a member, matching the server's sorted-key JSON. */
    fun memberValue(present: Boolean, position: String) = """{"position":"$position","present":$present}"""
}
