package dev.radio.mobile.playback

import android.app.Application
import androidx.lifecycle.AndroidViewModel

class PlaybackViewModel(application: Application) : AndroidViewModel(application) {
    val controller = ServicePlaybackController(application)
    override fun onCleared() { controller.close() }
}
