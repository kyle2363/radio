package dev.radio.mobile.data

/**
 * Slowed Radio journal rules (checkpoint 14, plan §9): song events carry `effect` while a preset is on; skips with an
 * effect are about the effect, not the song, so neither Radio's learning nor connected tuning counts them.
 * Plain string handling, so the rules run in JVM tests without org.json.
 */
object SlowedEvents {
    private val effectField = Regex(""","effect":"[a-z_]{1,24}"|"effect":"[a-z_]{1,24}",?""")

    fun hasEffect(payload: String): Boolean = payload.contains("\"effect\":\"")

    fun isEffectSkip(kind: String, payload: String): Boolean = kind == "song_skipped" && hasEffect(payload)

    /** The same event without `effect`, for desktops that don't accept the field. */
    fun withoutEffect(payload: String): String = if (!hasEffect(payload)) payload else payload.replace(effectField, "")
}
