package dev.radio.mobile.playback

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import dev.radio.mobile.data.LocalSong
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Activity's lightweight connection. Releasing the UI never releases or pauses the player. */
class ServicePlaybackController(context: Context) : PlaybackController {
    private val context = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val mutable = MutableStateFlow(PlaybackState())
    override val state: StateFlow<PlaybackState> = mutable.asStateFlow()
    private var engine: PlaybackController? = null
    private var previewEngine: PreviewController? = null
    private var previewObserver: Job? = null
    private val previewMutable = MutableStateFlow<PreviewState>(PreviewState.Idle)
    /** Song clips owned by the service; commands sent before binding run once it connects. */
    val preview: PreviewController = object : PreviewController {
        override val previewState: StateFlow<PreviewState> = previewMutable.asStateFlow()
        override fun enterPreview() = previewCommand { it.enterPreview() }
        override fun playPreview(song: LocalSong, startFraction: Float) = previewCommand { it.playPreview(song, startFraction) }
        override fun stopPreview() = previewCommand { it.stopPreview() }
        override fun exitPreview() = previewCommand { it.exitPreview() }
    }
    private fun previewCommand(action: (PreviewController) -> Unit) = command { _ -> previewEngine?.let(action) }
    private var observer: Job? = null
    private val pending = mutableListOf<(PlaybackController) -> Unit>()
    private var bound = false
    private var closed = false
    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, service: IBinder) {
            if (closed) return
            val controller = (service as? RadioPlaybackService.LocalBinder)?.controller
            if (controller == null) { disconnected(); return }
            engine = controller
            previewEngine = (service as RadioPlaybackService.LocalBinder).preview
            previewObserver?.cancel()
            previewObserver = scope.launch { previewEngine?.previewState?.collect { previewMutable.value = it } }
            observer?.cancel()
            observer = scope.launch { controller.state.collect { mutable.value = it } }
            val actions = pending.toList(); pending.clear()
            actions.forEach { it(controller) }
        }
        override fun onServiceDisconnected(name: ComponentName) = disconnected()
        override fun onNullBinding(name: ComponentName) { disconnected(); unbind() }
        override fun onBindingDied(name: ComponentName) { disconnected(); unbind(); if (!closed) bind() }
    }
    init { bind() }
    private fun bind() {
        bound = context.bindService(Intent(context, RadioPlaybackService::class.java).setAction(RadioPlaybackService.ACTION_LOCAL_BIND), connection, Context.BIND_AUTO_CREATE)
        if (!bound) disconnected()
    }
    private fun disconnected() {
        engine = null; observer?.cancel(); observer = null
        previewEngine = null; previewObserver?.cancel(); previewObserver = null; previewMutable.value = PreviewState.Idle
        mutable.value = mutable.value.copy(isPlaying = false, playWhenReady = false, buffering = false,
            error = "Playback disconnected. Reopen the app to reconnect.")
    }
    private fun unbind() { if (bound) { context.unbindService(connection); bound = false } }
    private fun command(action: (PlaybackController) -> Unit) {
        if (closed) return
        val controller = engine
        if (controller != null) action(controller)
        else {
            if (!bound) bind()
            if (bound) pending += action
        }
    }
    override fun playFrom(items: List<LocalSong>, selectedId: String, source: PlaySource) {
        val snapshot = items.toList()
        command { it.playFrom(snapshot, selectedId, source) }
    }
    override fun toggle() = command { it.toggle() }
    // Decided by the service once bound: before binding this controller can't know whether music is already playing.
    override fun play() = command { it.play() }
    override fun pause() = command { it.pause() }
    override fun next() = command { it.next() }
    override fun next(reason: String) = command { it.next(reason) }
    override fun replay() = command { it.replay() }
    override fun previous() = command { it.previous() }
    override fun seek(positionMs: Long) = command { it.seek(positionMs) }
    override fun jumpTo(songId: String) = command { it.jumpTo(songId) }
    override fun shuffle(enabled: Boolean) = command { it.shuffle(enabled) }
    override fun setRepeat(mode: RepeatMode) = command { it.setRepeat(mode) }
    override fun addToQueue(items: List<LocalSong>, playNext: Boolean) { val snapshot = items.toList(); command { it.addToQueue(snapshot, playNext) } }
    override fun removeFromQueue(songId: String) = command { it.removeFromQueue(songId) }
    override fun moveInQueue(songId: String, target: Int) = command { it.moveInQueue(songId, target) }
    override fun clearUpcoming() = command { it.clearUpcoming() }
    override fun setSleepTimer(durationMs: Long, fade: Boolean) = command { it.setSleepTimer(durationMs, fade) }
    override fun sleepAtTrackEnd() = command { it.sleepAtTrackEnd() }
    override fun sleepAtQueueEnd() = command { it.sleepAtQueueEnd() }
    override fun cancelSleepTimer() = command { it.cancelSleepTimer() }
    override fun startRadio(seedLocalId: String?) = command { it.startRadio(seedLocalId) }
    override fun stopRadio() = command { it.stopRadio() }
    override fun endRadio(stopNow: Boolean) = command { it.endRadio(stopNow) }
    override fun newMix() = command { it.newMix() }
    // The service observes the canonical repository even while there is no activity.
    override fun reconcileLibrary(songs: List<LocalSong>, accessLost: Boolean) = Unit
    fun close() {
        if (closed) return
        closed = true; pending.clear(); observer?.cancel(); engine = null
        unbind(); scope.cancel()
    }
}
