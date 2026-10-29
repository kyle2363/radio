package dev.radio.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.KeyboardType
import dev.radio.mobile.playback.PlaybackController
import dev.radio.mobile.playback.SleepTimerState

fun sleepLabel(timer: SleepTimerState): String = when {
    timer.endOfTrack -> "End of song"
    timer.active -> ((timer.remainingMs + 999) / 1000).let { "%d:%02d".format(it / 60, it % 60) }
    else -> "Off"
}

@Composable
fun SleepTimerDialog(timer: SleepTimerState, playback: PlaybackController, onDismiss: () -> Unit) {
    var minutes by remember { mutableStateOf("30") }
    var fade by remember { mutableStateOf(true) }
    val value = minutes.toIntOrNull()
    AlertDialog(onDismissRequest = onDismiss, title = { Text("Sleep timer") }, text = { Column {
        if (timer.active) Text("Active: ${sleepLabel(timer)}")
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            listOf(15, 30, 45, 60).forEach { preset -> TextButton(onClick = { minutes = preset.toString() }) { Text("${preset}m") } }
        }
        OutlinedTextField(minutes, { minutes = it.filter(Char::isDigit).take(3) }, label = { Text("Minutes (1–240)") }, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.testTag("sleep-minutes"))
        Row(verticalAlignment = Alignment.CenterVertically) { Checkbox(fade, { fade = it }); Text("Fade countdown in the final 10 seconds") }
        TextButton(onClick = { playback.sleepAtTrackEnd(); onDismiss() }) { Text("Stop at end of this song") }
        Text("The timer keeps counting while paused. It clears after an app restart; restored playback stays paused.", style = MaterialTheme.typography.bodySmall)
        if (timer.active) TextButton(onClick = { playback.cancelSleepTimer(); onDismiss() }) { Text("Cancel timer") }
    } }, confirmButton = { TextButton(onClick = { playback.setSleepTimer(value!!.toLong() * 60000, fade); onDismiss() }, enabled = value != null && value in 1..240) { Text(if (timer.active) "Replace timer" else "Start timer") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Close") } })
}
