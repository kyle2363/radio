"""SleepTimer — background countdown with fade-out and auto-chill.

Spawns a daemon thread that decrements every second.  When time runs
out, fades volume and stops playback.  Optionally switches to "chill"
mood before the end.
"""

import threading
import time
import logging

logger = logging.getLogger(__name__)


class SleepTimer:
    """Countdown timer with volume fade and optional chill switch."""

    def __init__(self, player, mood_machine, broadcast_callback=None):
        self._player = player
        self._mood = mood_machine
        self._broadcast = broadcast_callback or (lambda *a, **kw: None)
        self._timer_thread = None
        self._cancel_event = threading.Event()
        self._remaining_secs = 0
        self._total_secs = 0
        self._fade_secs = 30
        self._switch_to_chill = True
        self._chill_before_secs = 600
        self._chill_switched = False
        self._fading = False
        self._original_volume = 70
        self._lock = threading.Lock()

    def start(self, minutes, fade_seconds=30, switch_to_chill=True):
        """Start the sleep timer.

        Args:
            minutes: Duration in minutes (0 = end after current song).
            fade_seconds: How many seconds to fade volume over.
            switch_to_chill: Whether to auto-switch mood to 'chill'.
        """
        with self._lock:
            self.cancel()
            self._total_secs = minutes * 60 if minutes > 0 else 0
            self._remaining_secs = self._total_secs
            self._fade_secs = fade_seconds
            self._switch_to_chill = switch_to_chill
            self._chill_switched = False
            self._fading = False
            self._original_volume = self._player.get_volume()
            self._cancel_event.clear()
            self._timer_thread = threading.Thread(target=self._tick, daemon=True)
            self._timer_thread.start()
            logger.info(f"Sleep timer started: {minutes}min, fade={fade_seconds}s, chill={switch_to_chill}")

    def cancel(self):
        """Cancel the timer and restore volume."""
        with self._lock:
            if self._timer_thread and self._timer_thread.is_alive():
                self._cancel_event.set()
                if self._fading:
                    self._restore_volume()
                self._fading = False
                logger.info("Sleep timer cancelled")

    def get_status(self):
        with self._lock:
            return {
                "active": self._timer_thread is not None and self._timer_thread.is_alive(),
                "total_seconds": self._total_secs,
                "remaining_seconds": self._remaining_secs,
                "fade_seconds": self._fade_secs,
                "switch_to_chill": self._switch_to_chill,
                "fading": self._fading,
                "chill_switched": self._chill_switched,
            }

    def _restore_volume(self):
        try:
            self._player.set_volume(self._original_volume)
        except Exception:
            pass

    def _tick(self):
        while self._remaining_secs > 0 and not self._cancel_event.is_set():
            with self._lock:
                r = self._remaining_secs

            # Switch to chill before end
            if (self._switch_to_chill and not self._chill_switched
                    and r <= self._chill_before_secs):
                try:
                    self._mood.set_state("chill")
                    self._chill_switched = True
                    self._broadcast("mood_changed", {
                        "state": "chill",
                        "reason": "Sleep timer — switching to chill",
                    })
                    logger.info("Sleep timer: switched to chill mood")
                except Exception as e:
                    logger.warning(f"Sleep timer chill switch failed: {e}")

            # Fade out
            if self._fade_secs > 0 and r <= self._fade_secs:
                with self._lock:
                    self._fading = True
                    pct = r / self._fade_secs
                    vol = max(0, int(self._original_volume * pct))
                try:
                    self._player.set_volume(vol)
                except Exception as e:
                    logger.warning(f"Sleep timer fade volume failed: {e}")

            time.sleep(1)
            with self._lock:
                self._remaining_secs -= 1

        # Timer expired
        if not self._cancel_event.is_set():
            with self._lock:
                self._fading = False
            try:
                self._player.stop()
                logger.info("Sleep timer: stopped playback")
            except Exception as e:
                logger.warning(f"Sleep timer stop failed: {e}")
            self._broadcast("sleep_timer_expired", {})
