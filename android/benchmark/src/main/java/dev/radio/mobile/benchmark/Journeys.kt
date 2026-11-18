package dev.radio.mobile.benchmark

import androidx.benchmark.macro.MacrobenchmarkScope
import androidx.test.uiautomator.By
import androidx.test.uiautomator.Direction
import androidx.test.uiautomator.Until

const val PACKAGE = "dev.radio.mobile"
private const val WAIT = 5_000L

/**
 * The everyday journeys measured by the benchmarks and replayed to generate the baseline profile.
 * They assume a phone with the Music folder already chosen; nothing here changes the library or plays music,
 * except opening Now Playing for a song that is already in the mini-player.
 */
/** The app reopens on the last screen; Now Playing and sub-pages hide the bottom bar, so back out until it shows. */
private fun MacrobenchmarkScope.toMainScreen() {
    repeat(3) {
        if (device.wait(Until.hasObject(By.res("nav-Library")), 1_500)) return
        device.pressBack(); device.waitForIdle()
    }
}

fun MacrobenchmarkScope.openLibrary() {
    toMainScreen()
    device.findObject(By.res("nav-Library"))?.click()
    device.wait(Until.hasObject(By.res("library-list")), WAIT)
    device.waitForIdle()
}

fun MacrobenchmarkScope.scrollAllSongs() {
    val list = device.findObject(By.res("library-list")) ?: return
    list.setGestureMargin(device.displayWidth / 5)
    repeat(3) { list.fling(Direction.DOWN); device.waitForIdle() }
    repeat(3) { list.fling(Direction.UP); device.waitForIdle() }
}

fun MacrobenchmarkScope.openNowPlaying() {
    toMainScreen()
    val mini = device.wait(Until.findObject(By.res("mini-open")), WAIT) ?: return
    mini.click()
    device.waitForIdle()
    device.pressBack()
    device.waitForIdle()
}
