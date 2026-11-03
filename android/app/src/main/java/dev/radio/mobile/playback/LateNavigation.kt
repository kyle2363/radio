package dev.radio.mobile.playback

/** Media position determines navigation intent; audible time still determines completion. */
internal fun isLateNavigation(durationMs: Long?, positionMs: Long): Boolean =
    durationMs != null && durationMs > 0 && positionMs in 0..durationMs && durationMs - positionMs <= 30_000
