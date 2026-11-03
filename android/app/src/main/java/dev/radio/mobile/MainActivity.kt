package dev.radio.mobile

import android.os.Bundle
import android.content.Intent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import dev.radio.mobile.ui.RadioApp

class MainActivity : ComponentActivity() {
    private var openPlayerRequest by mutableIntStateOf(0)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        if (intent.action == ACTION_NOW_PLAYING) openPlayerRequest++
        setContent { RadioApp(openPlayerRequest = openPlayerRequest) }
    }
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (intent.action == ACTION_NOW_PLAYING) openPlayerRequest++
    }
    companion object { const val ACTION_NOW_PLAYING = "dev.radio.mobile.NOW_PLAYING" }
}
