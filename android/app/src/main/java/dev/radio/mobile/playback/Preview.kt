package dev.radio.mobile.playback

import android.content.Context
import android.net.Uri
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import dev.radio.mobile.data.LocalSong
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** What the preview player is doing. Previews are never plays: they never reach the journal, history or Radio. */
sealed interface PreviewState {
    data object Idle : PreviewState
    data class Playing(val songId: String, val positionMs: Long, val durationMs: Long, val clipStartMs: Long, val clipEndMs: Long) : PreviewState
}

/** Short song clips for feel sprints and this-or-that. Main playback is paused while previewing and never edited. */
interface PreviewController {
    val previewState: StateFlow<PreviewState>
    /** Pause main music (remembering whether it was playing). */
    fun enterPreview()
    /** Play a ~15 s clip of [song] from [startFraction] of its duration. */
    fun playPreview(song: LocalSong, startFraction: Float = PreviewPolicy.DEFAULT_START_FRACTION)
    fun stopPreview()
    /** Stop any clip; resume main music only if it was playing on [enterPreview]. */
    fun exitPreview()
}

data class PreviewWindow(val startMs: Long, val endMs: Long)

object PreviewPolicy {
    const val DEFAULT_START_FRACTION = 0.35f
    const val CLIP_MS = 15_000L

    /** Clip window inside the song; short songs play whole, unknown durations start at 0. */
    fun window(durationMs: Long?, startFraction: Float = DEFAULT_START_FRACTION, clipMs: Long = CLIP_MS): PreviewWindow {
        if (durationMs == null || durationMs <= 0) return PreviewWindow(0, clipMs)
        if (durationMs <= clipMs) return PreviewWindow(0, durationMs)
        val fraction = startFraction.coerceIn(0f, 1f)
        val start = Math.round(durationMs * fraction.toString().toDouble()).coerceIn(0, durationMs - clipMs)
        return PreviewWindow(start, start + clipMs)
    }
}

/** In-memory only, so process loss forgets it and main music stays paused. */
class PreviewSession {
    private var active = false
    private var resume = false
    val isActive get() = active
    /** Returns true when the caller must pause main playback now. */
    fun enter(mainPlaying: Boolean): Boolean {
        if (active) return false
        active = true; resume = mainPlaying
        return mainPlaying
    }
    /** Returns true when the caller should resume main playback. */
    fun exit(): Boolean {
        if (!active) return false
        active = false
        return resume.also { resume = false }
    }
}

/**
 * Lives in [RadioPlaybackService]. A second, bare ExoPlayer: no MediaSession, no journal/attempt listeners,
 * no snapshot callbacks. It takes audio focus itself (main is already paused, so Media3 just sees a loss on a
 * paused player); a call or another app taking focus pauses the clip and main stays paused.
 */
@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
internal class ServicePreviewController(
    context: Context,
    private val mainPlaying: () -> Boolean,
    private val pauseMain: () -> Unit,
    private val resumeMain: () -> Unit,
) : PreviewController {
    private val appContext = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val session = PreviewSession()
    private val mutable = MutableStateFlow<PreviewState>(PreviewState.Idle)
    override val previewState: StateFlow<PreviewState> = mutable.asStateFlow()
    private var player: ExoPlayer? = null
    private var ticker: Job? = null
    private var songId: String? = null
    private var window = PreviewWindow(0, PreviewPolicy.CLIP_MS)

    private fun player(): ExoPlayer = player ?: ExoPlayer.Builder(appContext).build().apply {
        setAudioAttributes(AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MUSIC).build(), true)
        setHandleAudioBecomingNoisy(true)
        repeatMode = Player.REPEAT_MODE_OFF
        addListener(object : Player.Listener {
            override fun onPlaybackStateChanged(state: Int) { if (state == Player.STATE_ENDED) stopPreview() }
            override fun onPlayerError(error: androidx.media3.common.PlaybackException) { stopPreview() }
        })
    }.also { player = it }

    override fun enterPreview() {
        if (session.enter(mainPlaying())) pauseMain()
    }

    override fun playPreview(song: LocalSong, startFraction: Float) {
        if (!session.isActive) enterPreview()
        else if (mainPlaying()) pauseMain() // listener resumed main from the notification meanwhile
        val p = player()
        window = PreviewPolicy.window(song.durationMs, startFraction)
        songId = song.id
        p.setMediaItem(MediaItem.Builder().setUri(Uri.parse(song.uri)).setMediaId(song.id).build(), window.startMs)
        p.prepare(); p.play()
        ticker?.cancel()
        ticker = scope.launch {
            while (isActive) {
                val duration = p.duration.takeIf { it != C.TIME_UNSET && it > 0 }
                // Correct the window once the real duration is known (catalog duration may be missing).
                if (duration != null && song.durationMs == null) {
                    val actual = PreviewPolicy.window(duration, startFraction)
                    if (actual != window) { window = actual; p.seekTo(actual.startMs) }
                }
                val position = p.currentPosition
                if (position >= window.endMs) { stopPreview(); break }
                mutable.value = PreviewState.Playing(song.id, position, duration ?: song.durationMs ?: 0, window.startMs, window.endMs)
                delay(200)
            }
        }
    }

    override fun stopPreview() {
        ticker?.cancel(); ticker = null
        player?.run { stop(); clearMediaItems() }
        songId = null
        mutable.value = PreviewState.Idle
    }

    override fun exitPreview() {
        stopPreview()
        player?.release(); player = null
        if (session.exit() && !mainPlaying()) resumeMain()
    }

    fun close() {
        ticker?.cancel(); scope.cancel()
        player?.release(); player = null
        session.exit() // dropped: never resume after the service ends
        mutable.value = PreviewState.Idle
    }
}
