package dev.radio.mobile
import dev.radio.mobile.playback.isLateNavigation
import org.junit.Assert.*
import org.junit.Test
class LateNavigationTest {
 @Test fun boundary() {
  assertTrue(isLateNavigation(180000,150000))
  assertTrue(isLateNavigation(180000,180000))
  assertFalse(isLateNavigation(180000,149999))
  assertFalse(isLateNavigation(null,150000))
  assertFalse(isLateNavigation(-1,0))
  assertFalse(isLateNavigation(180000,-1))
  assertFalse(isLateNavigation(180000,180001))
 }
}
