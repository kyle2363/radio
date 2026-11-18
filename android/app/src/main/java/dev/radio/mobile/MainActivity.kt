package dev.radio.mobile

import android.os.Bundle
import android.content.Intent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import dev.radio.mobile.ui.RadioApp

class MainActivity : ComponentActivity() {
    private var openPlayerRequest by mutableIntStateOf(0)
    private var shortcut by mutableStateOf<dev.radio.mobile.ui.ShortcutRequest?>(null)
    private fun route(intent: Intent) {
        if (intent.action == ACTION_NOW_PLAYING) openPlayerRequest++
        if (intent.action in dev.radio.mobile.ui.ShortcutRequest.ACTIONS) shortcut = dev.radio.mobile.ui.ShortcutRequest(intent.action!!, (shortcut?.serial ?: 0) + 1)
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        if (savedInstanceState == null) route(intent)
        setContent { RadioApp(openPlayerRequest = openPlayerRequest, shortcut = shortcut) }
    }
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        route(intent)
    }
    companion object { const val ACTION_NOW_PLAYING = "dev.radio.mobile.NOW_PLAYING" }
}
