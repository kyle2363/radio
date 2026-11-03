package dev.radio.mobile.playback

import android.content.Context
import android.content.ContextWrapper
import androidx.activity.ComponentActivity
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.ViewModelProvider

internal tailrec fun Context.playbackActivity(): ComponentActivity? = when (this) {
    is ComponentActivity -> this
    is ContextWrapper -> baseContext.playbackActivity()
    else -> null
}

@Composable
fun rememberPlaybackController(): PlaybackController {
    val activity = checkNotNull(LocalContext.current.playbackActivity())
    return remember(activity) { ViewModelProvider(activity)[PlaybackViewModel::class.java].controller }
}
