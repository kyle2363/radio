"""Session-local overlay for recommendation settings backed by global settings."""


TALK_SETTING_KEYS = frozenset({
    "talk_segments_enabled", "talk_caller_enabled", "talk_caller_mode",
    "talk_monologue_enabled", "talk_guest_takeover_enabled", "talk_frequency",
    "talk_guest_songs_per_set", "talk_segment_cooldown",
    "talk_caller_heartbroken_enabled", "talk_caller_melancholy_enabled", "talk_volume",
})

SESSION_SETTING_KEYS = TALK_SETTING_KEYS | frozenset({
    "play_mode",
    "current_mode",
    "folder_filter",
    "recommendation_filters",
    "genre_filter",
    "artist_filter",
    "exploration_level",
    "llm_reranking_enabled",
})


class SessionSettings:
    """Wrap the database settings API while isolating recommendation controls."""

    def __init__(self, database, overrides=None):
        self._database = database
        self.overrides = overrides if overrides is not None else {}
        for key in SESSION_SETTING_KEYS:
            if key not in self.overrides:
                value = database.get_setting(key)
                self.overrides[key] = ('true' if key == 'llm_reranking_enabled' and value is None else value)

    def get_setting(self, key):
        if key in SESSION_SETTING_KEYS:
            return self.overrides.get(key)
        return self._database.get_setting(key)

    def get_setting_float(self, key, default=0.0):
        if key not in SESSION_SETTING_KEYS:
            return self._database.get_setting_float(key, default)
        try:
            raw = self.get_setting(key)
            return float(raw) if raw is not None and str(raw).strip() else default
        except (ValueError, TypeError):
            return default

    def get_setting_int(self, key, default=0):
        if key not in SESSION_SETTING_KEYS:
            return self._database.get_setting_int(key, default)
        try:
            raw = self.get_setting(key)
            value = int(raw) if raw is not None and str(raw).strip() else default
            lower, upper = {
                "talk_frequency": (1, 100), "talk_guest_songs_per_set": (1, 100),
                "talk_segment_cooldown": (0, 100), "talk_volume": (0, 200),
            }.get(key, (-2147483648, 2147483647))
            return value if lower <= value <= upper else default
        except (ValueError, TypeError, OverflowError):
            return default

    def set_setting(self, key, value):
        if key in SESSION_SETTING_KEYS:
            self.overrides[key] = str(value) if value is not None else ""
            return
        self._database.set_setting(key, value)

    def __getattr__(self, name):
        return getattr(self._database, name)
