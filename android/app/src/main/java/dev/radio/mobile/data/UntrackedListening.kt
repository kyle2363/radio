package dev.radio.mobile.data

import android.content.SharedPreferences
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Untracked listening: while on, starts, completions, skips and replays are not journaled, so Radio learning,
 * Stats, recency rules and sync never see them. Likes, dislikes and feel ratings still save (they're deliberate).
 * It ends when turned off, after [MAX_MS], or after [IDLE_MS] without playback, so it can't be left on by accident.
 */
class UntrackedListening(private val store: SharedPreferences?, private val now: () -> Long = System::currentTimeMillis) {
    data class Session(val since: Long, val lastPlayingAt: Long, val playing: Boolean)
    companion object { const val MAX_MS = 2 * 60 * 60_000L; const val IDLE_MS = 15 * 60_000L }

    private val mutable = MutableStateFlow(store?.takeIf { it.contains("untrackedSince") }?.let {
        Session(it.getLong("untrackedSince", 0), it.getLong("untrackedLast", 0), false) })
    val state: StateFlow<Session?> = mutable.asStateFlow()

    init { active() }

    fun start() { val t = now(); set(Session(t, t, false)) }
    fun stop() = set(null)

    /** Call on playback changes; playing time keeps the session alive. */
    fun touch(playing: Boolean) {
        val s = mutable.value ?: return
        if (expired(s)) { stop(); return }
        if (playing || s.playing) set(s.copy(lastPlayingAt = now(), playing = playing))
    }

    fun active(): Boolean {
        val s = mutable.value ?: return false
        if (expired(s)) { stop(); return false }
        return true
    }

    private fun expired(s: Session): Boolean { val t = now()
        return t - s.since > MAX_MS || (!s.playing && t - s.lastPlayingAt > IDLE_MS) }

    private fun set(value: Session?) {
        mutable.value = value
        store?.edit()?.apply { if (value == null) remove("untrackedSince").remove("untrackedLast")
            else putLong("untrackedSince", value.since).putLong("untrackedLast", value.lastPlayingAt) }?.apply()
    }
}
