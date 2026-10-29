package dev.radio.mobile.playback

import dev.radio.mobile.data.LocalSong

/** Commands tapped during disk restore run afterward, so the user's newest choice wins. */
internal class InitializingPlaybackController(private val engine: PlaybackController) : PlaybackController by engine {
    private var ready = false
    private val pending = mutableListOf<() -> Unit>()
    private fun command(action: () -> Unit) { if (ready) action() else pending += action }
    fun initialized() {
        ready = true
        val actions = pending.toList(); pending.clear()
        actions.forEach { it() }
    }
    override fun playFrom(items: List<LocalSong>, selectedId: String, source: String) {
        val snapshot = items.toList()
        command { engine.playFrom(snapshot, selectedId, source) }
    }
    override fun toggle() = command { engine.toggle() }
    override fun pause() = command { engine.pause() }
    override fun seek(positionMs: Long) = command { engine.seek(positionMs) }
    override fun next() = command { engine.next() }
    override fun previous() = command { engine.previous() }
    override fun jumpTo(songId: String) = command { engine.jumpTo(songId) }
    override fun shuffle(enabled: Boolean) = command { engine.shuffle(enabled) }
    override fun addToQueue(items: List<LocalSong>, playNext: Boolean) { val snapshot = items.toList(); command { engine.addToQueue(snapshot, playNext) } }
    override fun removeFromQueue(songId: String) = command { engine.removeFromQueue(songId) }
    override fun moveInQueue(songId: String, target: Int) = command { engine.moveInQueue(songId, target) }
    override fun clearUpcoming() = command { engine.clearUpcoming() }
    override fun setSleepTimer(durationMs: Long, fade: Boolean) = command { engine.setSleepTimer(durationMs, fade) }
    override fun sleepAtTrackEnd() = command { engine.sleepAtTrackEnd() }
    override fun cancelSleepTimer() = command { engine.cancelSleepTimer() }
}
