package dev.radio.mobile.recommendation

import dev.radio.mobile.playback.SlowedPreset
import kotlin.random.Random

/**
 * Slowed Radio policy on the phone (checkpoint 14C/14D, docs/SLOWED_RADIO_PLAN.md). Free of Android types so every rule
 * can be checked in JVM tests: when a set starts, how long it is, who voices it, which songs it slows and how it ends.
 */

enum class SlowedFrequency(val label: String, val factor: Double) {
    OFF("Off", 0.0), RARELY("Rarely", 0.25), NORMAL("Normal", 0.5), OFTEN("Often", 0.75)
}

/** Radio → Tune → Slowed. Master off by default; needs Talk on. Phone-only, never synced. */
data class SlowedSettings(
    val enabled: Boolean = false,
    val frequency: SlowedFrequency = SlowedFrequency.NORMAL,
    /** Top of the set length range; the bottom is always [MIN_SONGS]. */
    val maxSongs: Int = DEFAULT_MAX,
    val presets: Set<SlowedPreset> = SlowedPreset.entries.toSet(),
) {
    fun normalized() = copy(maxSongs = maxSongs.coerceIn(MIN_SONGS, MAX_SONGS), presets = presets.ifEmpty { setOf(SlowedPreset.SLOWED) })

    fun encode(): String = ConnectedJson.write(linkedMapOf("enabled" to enabled, "frequency" to frequency.name, "max_songs" to maxSongs,
        "presets" to SlowedPreset.entries.filter { it in presets }.map { it.id }))

    companion object {
        const val MIN_SONGS = 4; const val MAX_SONGS = 10; const val DEFAULT_MAX = 8
        fun decode(text: String?): SlowedSettings {
            val root = text?.let { runCatching { ConnectedJson.parse(it) as? Map<*, *> }.getOrNull() } ?: return SlowedSettings()
            val presets = (root["presets"] as? List<*>)?.mapNotNull { SlowedPreset.fromId(it as? String) }?.toSet()
            return SlowedSettings(root["enabled"] == true, SlowedFrequency.entries.firstOrNull { it.name == root["frequency"] } ?: SlowedFrequency.NORMAL,
                ((root["max_songs"] as? Long) ?: DEFAULT_MAX.toLong()).toInt(), presets ?: SlowedPreset.entries.toSet()).normalized()
        }
    }
}

/** The effects guests (plan 13.1). Both voice all three presets. */
enum class SlowedGuest(val id: String, val displayName: String) {
    MOONLIGHT("guest_chill", "DJ Moonlight"), UNDERTOW("guest_undertow", "The Undertow");
    companion object { fun fromId(id: String?) = entries.firstOrNull { it.id == id } }
}

/** What the guest says, and when (bank kinds on the desktop). */
enum class SlowedMoment(val wire: String) { OPENER("opener"), MID("mid"), CLOSER("closer") }

/** One Slowed set (automatic) or run (manual), from the decision to its revert. */
data class SlowedSet(
    val preset: SlowedPreset,
    val guest: SlowedGuest,
    val manual: Boolean,
    /** Songs to slow. A manual run that replaced an automatic set starts over with its own length. */
    val total: Int,
    /** The song the opener follows; the set's first song is whatever comes after it. */
    val afterId: String,
    /** True once the opener started playing: from then on the set runs even if the opener is skipped. */
    val committed: Boolean = false,
    /** Set songs that actually started, in order. */
    val played: List<String> = emptyList(),
    /** Mid-set lines already planned (by the number of songs played when they were asked for). */
    val midsAt: Set<Int> = emptySet(),
    /** The set's last song started (or the set was ended early): the next gap gets the closer. */
    val closing: Boolean = false,
) {
    val songsLeft: Int get() = (total - played.size).coerceAtLeast(0)
}

/** A line the station is about to play, for a talk turn's `effects` object. */
data class SlowedRequest(val moment: SlowedMoment, val preset: SlowedPreset, val guest: SlowedGuest, val songs: Int, val songsLeft: Int, val manual: Boolean) {
    fun toWire(): Map<String, Any?> = linkedMapOf("moment" to moment.wire, "preset" to preset.id, "guest" to guest.id,
        "songs" to songs.coerceIn(1, 10), "songs_left" to songsLeft.coerceIn(0, 10), "trigger" to if (manual) "manual" else "auto")
}

object SlowedPolicy {
    /** Songs between the end of one set and the start of the next automatic one. */
    const val COOLDOWN_SONGS = 15
    /** A set needs this many matching songs in its pool, or it is not started. */
    const val MIN_POOL = SlowedSettings.MIN_SONGS
    /** Before the boundary, the bank line goes in if no live line has arrived (the talk insertion margin plus a little). */
    const val BANK_FALLBACK_MS = 5_000L
    /** Manual run: the interrupted song fades out over this long before the opener. */
    const val MANUAL_FADE_MS = 2_000L

    /**
     * How likely a set is to start at one song boundary: the frequency's share of a normal guest takeover's rate, so
     * Slowed is always rarer than a guest. A takeover is one of three segment kinds every [talkFrequency] songs.
     */
    fun chance(settings: SlowedSettings, talkFrequency: Int?): Double {
        if (!settings.enabled) return 0.0
        val every = (talkFrequency ?: 4).coerceIn(TalkSettings.MIN_FREQUENCY, TalkSettings.MAX_FREQUENCY)
        return settings.frequency.factor / every / 3.0
    }

    /** Whether Slowed may run at all on this station (plan §2 "Where" and "Feel gate"). */
    fun allowed(settings: SlowedSettings, talkOn: Boolean, radioActive: Boolean, feel: String?, allowedFeels: Set<String>): Boolean =
        settings.enabled && talkOn && radioActive && (feel == null || feel in allowedFeels)

    /** An automatic set may start at this boundary: allowed, nothing running, cooled down, and the roll came up. */
    fun shouldStartAuto(settings: SlowedSettings, talkFrequency: Int?, songsSinceLast: Int, otherGuestSet: Boolean, running: Boolean,
        random: Random): Boolean {
        if (running || otherGuestSet || songsSinceLast < COOLDOWN_SONGS || settings.frequency == SlowedFrequency.OFF) return false
        return random.nextDouble() < chance(settings, talkFrequency)
    }

    /** Automatic sets bias low: ~70% within the first three lengths (4–6 by default), the rest across the remainder. */
    fun autoLength(settings: SlowedSettings, random: Random): Int {
        val min = SlowedSettings.MIN_SONGS; val max = settings.normalized().maxSongs
        val lowTop = minOf(min + 2, max)
        return if (lowTop == max || random.nextDouble() < 0.7) random.nextInt(min, lowTop + 1) else random.nextInt(lowTop + 1, max + 1)
    }

    /** Manual runs sit at the top of the range: ~70% within the top three lengths (8–10 at most), capped at the max. */
    fun manualLength(settings: SlowedSettings, random: Random): Int {
        val min = SlowedSettings.MIN_SONGS; val max = settings.normalized().maxSongs
        val highBottom = maxOf(min, max - 2)
        return if (highBottom == min || random.nextDouble() < 0.7) random.nextInt(highBottom, max + 1) else random.nextInt(min, highBottom)
    }

    /** A set is shortened to the songs its pool can still offer (never below four), or not started. */
    fun fit(length: Int, poolUnplayed: Int?): Int? = when {
        poolUnplayed == null -> length
        poolUnplayed < MIN_POOL -> null
        else -> minOf(length, poolUnplayed)
    }

    /** Guests take turns more often than not; only guests with an opener available are offered. */
    fun pickGuest(available: Collection<SlowedGuest>, last: SlowedGuest?, random: Random): SlowedGuest? {
        val choices = SlowedGuest.entries.filter { it in available }
        if (choices.isEmpty()) return null
        val other = choices.filter { it != last }
        return if (other.isNotEmpty() && random.nextDouble() < 0.7) other[random.nextInt(other.size)] else choices[random.nextInt(choices.size)]
    }

    /**
     * Which songs play slowed right now: nothing before the opener starts; then the set's songs already started, plus
     * as many of the next songs in play order as the set still has room for. The current song is included only when
     * it is one of the set's (a song playing when a manual run starts never is).
     */
    fun effected(set: SlowedSet?, currentId: String?, playOrder: List<String>, talkNextId: String? = null): Set<String> {
        if (set == null || !set.committed) return emptySet()
        val ids = LinkedHashSet<String>()
        if (currentId != null && currentId in set.played) ids += currentId
        if (set.closing) return ids
        // While a speech item plays it is not in the play order: what follows starts at the song it introduces.
        val start = playOrder.indexOf(currentId)
        var room = set.songsLeft
        // A set song that just became current is slowed before its start is counted (the player reports it first).
        if (start >= 0 && currentId != set.afterId && currentId !in set.played && room > 0) { ids += currentId!!; room-- }
        val upcoming = when {
            start >= 0 -> playOrder.drop(start + 1)
            talkNextId != null && talkNextId in playOrder -> playOrder.drop(playOrder.indexOf(talkNextId))
            else -> return ids
        }
        ids += upcoming.filter { it !in set.played && it != set.afterId }.take(room)
        return ids
    }

    /** A song actually started: count it when it belongs to the set. Returns the updated set and whether it counted. */
    fun songStarted(set: SlowedSet, songId: String, effectedNow: Set<String>): SlowedSet {
        if (!set.committed || songId in set.played || songId !in effectedNow) return set
        val played = set.played + songId
        return set.copy(played = played, closing = played.size >= set.total)
    }

    /**
     * Whether the gap after this set song gets a "still slowed" line: once around the middle of an automatic set of 6+
     * (about half the time), and in a manual run about every 3–4 songs (about half the time).
     */
    fun wantsMid(set: SlowedSet, random: Random): Boolean {
        val n = set.played.size
        if (!set.committed || set.closing || n == 0 || n >= set.total || n in set.midsAt) return false
        return if (set.manual) n % (3 + random.nextInt(2)) == 0 && random.nextBoolean()
        else set.total >= 6 && n == set.total / 2 && set.midsAt.isEmpty() && random.nextBoolean()
    }
}

/** A line from the phone's bank (plan §5): desktop-voiced, generic, stored without expiry. */
data class SlowedLine(val id: String, val guest: String, val kind: String, val preset: String?, val sha256: String, val bytes: Int,
    val durationMs: Long, val created: Long, val lastPlayed: Long = 0, val plays: Int = 0)

/** Bank line kinds, as the desktop names them. */
object SlowedKinds {
    const val OPENER_AUTO = "opener_auto"; const val OPENER_MANUAL = "opener_manual"; const val MID = "mid"
    const val CLOSER = "closer"; const val CLOSER_GENERIC = "closer_generic"
    val ALL = setOf(OPENER_AUTO, OPENER_MANUAL, MID, CLOSER, CLOSER_GENERIC)
    fun perPreset(kind: String) = kind == OPENER_AUTO || kind == OPENER_MANUAL || kind == CLOSER
}

object SlowedBankPolicy {
    /** Never repeat any of the last few lines of the same kind. */
    const val RECENT = 4
    const val CAP_BYTES = 30L * 1024 * 1024
    const val METERED_BYTES_PER_SYNC = 5L * 1024 * 1024
    /** Offline automatic sets need this many openers and closers per allowed preset first. */
    const val READY_MIN = 2

    private fun candidates(lines: Collection<SlowedLine>, guest: SlowedGuest, kind: String, preset: SlowedPreset?) =
        lines.filter { it.guest == guest.id && it.kind == kind && (if (SlowedKinds.perPreset(kind)) it.preset == preset?.id else it.preset == null) }

    /**
     * A line for [moment]: least recently played first, never one of the last [RECENT] of its kind (unless nothing else
     * is left). A closer falls back to a generic closer. Null when the bank has nothing for it.
     */
    fun choose(lines: Collection<SlowedLine>, recent: Map<String, List<String>>, guest: SlowedGuest, moment: SlowedMoment,
        preset: SlowedPreset, manual: Boolean, random: Random = Random.Default): SlowedLine? {
        val kinds = when (moment) {
            SlowedMoment.OPENER -> if (manual) listOf(SlowedKinds.OPENER_MANUAL, SlowedKinds.OPENER_AUTO) else listOf(SlowedKinds.OPENER_AUTO)
            SlowedMoment.MID -> listOf(SlowedKinds.MID)
            SlowedMoment.CLOSER -> listOf(SlowedKinds.CLOSER, SlowedKinds.CLOSER_GENERIC)
        }
        for (kind in kinds) {
            val all = candidates(lines, guest, kind, preset)
            if (all.isEmpty()) continue
            val avoid = recent[kind].orEmpty().takeLast(RECENT).toSet()
            val fresh = all.filter { it.id !in avoid }.ifEmpty { all }
            val oldest = fresh.minOf { it.lastPlayed }
            val pool = fresh.filter { it.lastPlayed == oldest }
            return pool[random.nextInt(pool.size)]
        }
        return null
    }

    /** Guests that can open a set with [preset] from the bank. */
    fun guestsWithOpeners(lines: Collection<SlowedLine>, preset: SlowedPreset, manual: Boolean): Set<SlowedGuest> =
        SlowedGuest.entries.filter { guest ->
            candidates(lines, guest, SlowedKinds.OPENER_AUTO, preset).isNotEmpty() || manual && candidates(lines, guest, SlowedKinds.OPENER_MANUAL, preset).isNotEmpty()
        }.toSet()

    /** Offline automatic sets start only once each allowed preset has two openers and two closers. */
    fun ready(lines: Collection<SlowedLine>, presets: Set<SlowedPreset>): Boolean = presets.isNotEmpty() && presets.all { preset ->
        lines.count { it.kind == SlowedKinds.OPENER_AUTO && it.preset == preset.id } >= READY_MIN &&
            lines.count { it.kind == SlowedKinds.CLOSER && it.preset == preset.id || it.kind == SlowedKinds.CLOSER_GENERIC } >= READY_MIN
    }

    /** Which listed lines to fetch: missing ones, oldest manifest order first, within the cap and the per-sync budget. */
    fun toDownload(listed: List<SlowedLine>, stored: Map<String, SlowedLine>, storedBytes: Long, budget: Long?): List<SlowedLine> {
        val out = ArrayList<SlowedLine>()
        var total = storedBytes; var spent = 0L
        for (line in listed) {
            if (line.id in stored) continue
            if (total + line.bytes > CAP_BYTES) break
            if (budget != null && spent + line.bytes > budget) break
            out += line; total += line.bytes; spent += line.bytes
        }
        return out
    }
}
