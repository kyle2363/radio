package dev.radio.mobile.playback

import android.content.Context
import android.graphics.Bitmap
import androidx.core.content.ContextCompat
import androidx.core.graphics.drawable.toBitmap
import androidx.media3.common.MediaMetadata
import androidx.media3.common.util.BitmapLoader
import androidx.media3.datasource.DataSourceBitmapLoader
import androidx.media3.session.CacheBitmapLoader
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import com.google.common.util.concurrent.MoreExecutors
import dev.radio.mobile.R

@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
internal object PlaybackArtwork {
    const val TALK = "dev.radio.mobile.playback.TALK_ARTWORK"

    fun drawable(talk: Boolean) = if (talk) R.drawable.player_talk_artwork else R.drawable.player_artwork

    fun loader(context: Context): BitmapLoader {
        // The lock screen shows artwork up to 320dp wide (about 850px on a phone), and the system scales anything
        // larger down to that before it leaves the app. Drawing smaller makes it blurry there.
        val size = (320 * context.resources.displayMetrics.density).toInt().coerceIn(512, 1024)
        val embedded = CacheBitmapLoader(DataSourceBitmapLoader.Builder(context).setMaximumOutputDimension(size).build())
        fun fallback(talk: Boolean) = checkNotNull(ContextCompat.getDrawable(context, drawable(talk))).toBitmap(size, size)
        val musicArtwork by lazy { fallback(false) }
        val talkArtwork by lazy { fallback(true) }
        return object : BitmapLoader by embedded {
            override fun loadBitmapFromMetadata(metadata: MediaMetadata): ListenableFuture<Bitmap>? {
                val artwork = embedded.loadBitmapFromMetadata(metadata)
                if (metadata.title.isNullOrBlank()) return artwork
                val talk = metadata.extras?.getBoolean(TALK) == true
                fun fallbackBitmap() = if (talk) talkArtwork else musicArtwork
                return if (artwork == null) Futures.immediateFuture(fallbackBitmap())
                else Futures.catching(artwork, Exception::class.java, { fallbackBitmap() }, MoreExecutors.directExecutor())
            }
        }
    }
}
