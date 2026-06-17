-- songs table
CREATE TABLE IF NOT EXISTS songs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath        TEXT UNIQUE NOT NULL,
    file_hash       TEXT NOT NULL,
    artist          TEXT,
    title           TEXT,
    album           TEXT,
    duration_secs   REAL,
    bpm             INTEGER,
    energy          REAL DEFAULT 0.5,
    valence         REAL DEFAULT 0.5,
    raw_rms         REAL,
    crest_factor    REAL,
    spectral_contrast REAL,
    spectral_rolloff REAL,
    spectral_flatness REAL,
    cluster_id      INTEGER DEFAULT -1,

    ai_description  TEXT,
    lyrics          TEXT,
    emotional_tags  TEXT,
    genre_tags      TEXT,
    instrument_tags TEXT,

    user_tags       TEXT,
    user_notes      TEXT,

    play_count      INTEGER DEFAULT 0,
    skip_count      INTEGER DEFAULT 0,
    complete_count  INTEGER DEFAULT 0,
    replay_count    INTEGER DEFAULT 0,
    avg_completion  REAL DEFAULT 0.0,
    recovery_score  REAL DEFAULT 0.5,
    last_played     DATETIME,

    ingest_phase    TEXT DEFAULT 'raw',
    art_file        TEXT,
    dj_effect_eligible INTEGER DEFAULT 1,
    gain_db         REAL DEFAULT 0.0,
    added_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    file_mtime      REAL,

    user_energy     REAL,
    user_energy_count INTEGER DEFAULT 0,
    user_energy_updated_at REAL
);

CREATE INDEX IF NOT EXISTS idx_songs_ingest_phase ON songs(ingest_phase);
CREATE INDEX IF NOT EXISTS idx_songs_cluster ON songs(cluster_id);
CREATE INDEX IF NOT EXISTS idx_songs_energy ON songs(energy);
CREATE INDEX IF NOT EXISTS idx_songs_valence ON songs(valence);
CREATE INDEX IF NOT EXISTS idx_songs_last_played ON songs(last_played);
CREATE INDEX IF NOT EXISTS idx_songs_recovery_score ON songs(recovery_score);
CREATE INDEX IF NOT EXISTS idx_songs_avg_completion ON songs(avg_completion);
CREATE INDEX IF NOT EXISTS idx_songs_artist ON songs(artist);


-- play_events
CREATE TABLE IF NOT EXISTS play_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id         INTEGER REFERENCES songs(id),
    session_id      INTEGER REFERENCES sessions(id),
    played_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    skip_at_secs    REAL,
    completion_pct  REAL DEFAULT 0.0,
    queue_slot      TEXT,
    mood_state      TEXT,
    hour_of_day     INTEGER,
    day_type        TEXT,
    signal          TEXT
);
CREATE INDEX IF NOT EXISTS idx_play_events_song ON play_events(song_id);
CREATE INDEX IF NOT EXISTS idx_play_events_session ON play_events(session_id);
CREATE INDEX IF NOT EXISTS idx_play_events_song_signal ON play_events(song_id, signal);
CREATE INDEX IF NOT EXISTS idx_play_events_played_at ON play_events(played_at);

-- sessions
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    ended_at        DATETIME,
    initial_mood    TEXT,
    mood_changes    TEXT,
    skip_streak     INTEGER DEFAULT 0,
    session_type    TEXT DEFAULT 'normal'
);

-- transitions
CREATE TABLE IF NOT EXISTS transitions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_song_id    INTEGER REFERENCES songs(id),
    to_song_id      INTEGER REFERENCES songs(id),
    success_count   INTEGER DEFAULT 0,
    failure_count   INTEGER DEFAULT 0,
    score           REAL DEFAULT 0.5,
    UNIQUE(from_song_id, to_song_id)
);
CREATE INDEX IF NOT EXISTS idx_transitions_from ON transitions(from_song_id);

-- blacklist
CREATE TABLE IF NOT EXISTS blacklist (
    song_id     INTEGER REFERENCES songs(id) PRIMARY KEY,
    level       TEXT NOT NULL DEFAULT 'never_play',
    reason      TEXT,
    mood        TEXT,
    artist      TEXT,
    cluster_ids TEXT,
    added_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at  DATETIME
);

-- queue history
CREATE TABLE IF NOT EXISTS queue_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER REFERENCES sessions(id),
    song_id         INTEGER REFERENCES songs(id),
    position        INTEGER,
    slot            TEXT,
    reason          TEXT,
    played_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_queue_history_session ON queue_history(session_id);
CREATE INDEX IF NOT EXISTS idx_queue_history_song ON queue_history(song_id);

-- settings
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT
);

INSERT OR IGNORE INTO settings (key, value) VALUES
    ('exploration_level', '0.3'),
    ('current_mode', 'radio'),
    ('archetype', 'mood_listener'),
    ('vibe_lock_active', 'false'),
    ('vibe_lock_song_id', 'NULL'),
    ('vibe_lock_expires', 'NULL'),
    ('onboarding_complete', 'false'),
    ('vlc_installed', 'true'),
    ('piper_downloaded', 'false'),
    ('last_session_id', 'NULL'),
    ('chat_provider', 'auto'),
    ('embed_provider', 'auto'),
    ('tts_enabled', 'true'),
    ('tts_engine', 'omnivoice'),
    ('tts_voice', ''),
    ('tts_voice_mode', 'user_select'),
    ('omnivoice_downloaded', 'false'),
    ('music_dir', ''),
    ('genre_filter', ''),
    ('artist_filter', ''),
    ('constellations_enabled', 'true'),
    ('app_icon_style', 'drift'),
    ('theme', 'dark'),
    ('waveform_mode', 'orbit'),
    ('announcement_interval', '30'),
    ('section_spacing', '10'),
    ('show_archetype', 'true'),
    ('show_mode', 'true'),
    ('talk_segments_enabled', 'false'),
    ('talk_caller_enabled', 'true'),
    ('talk_caller_mode', 'influences_queue'),
    ('talk_monologue_enabled', 'true'),
    ('talk_guest_takeover_enabled', 'true'),
    ('talk_frequency', '4'),
    ('talk_guest_songs_per_set', '4'),
    ('talk_segment_cooldown', '2'),
    ('talk_caller_heartbroken_enabled', 'false'),
    ('talk_caller_melancholy_enabled', 'false'),
    ('talk_volume', '100'),
    ('radio_intro_frequency', '3'),
    ('right_rail_width', '255'),
    ('dj_effect_enabled', 'true'),
    ('dj_effect_preset', 'slowed_reverb'),
    ('folder_filter', ''),
    ('jitter_strength', '0.12'),
    ('jitter_override', ''),
    ('recency_exclude_hours', '1'),
    ('vibe_lock_duration_minutes', '30'),
    ('vibe_lock_max_songs', '10'),
    ('dislike_decay_rate', '0.029'),
    ('dj_speech_speed', '0.90'),
    ('dj_intro_start_hour', '6'),
    ('dj_intro_end_hour', '23'),
    ('play_mode', 'ai'),
    ('notify_on_song_change', 'true'),
    ('auto_pause_on_lock', 'true'),
    ('auto_resume_on_unlock', 'true'),
    ('alt_hotkeys_enabled', 'true'),
    ('start_minimized', 'false'),
    ('start_on_login', 'false'),
    ('mini_player_enabled', 'true'),
    ('widget_enabled', 'false'),
    ('hotkey_bindings', '{"heart":"Alt+H","thumbs_down":"Alt+D","vibe_lock":"Alt+V","mood_chill":"Alt+1","mood_energetic":"Alt+2","mood_focus":"Alt+3","mood_melancholy":"Alt+4","mood_discovery":"Alt+5","volume_up":"Alt+Up","volume_down":"Alt+Down"}'),
    ('accent_color', '#4eccc4'),
    ('show_lyrics', 'true'),
    ('eq_preset', 'off'),
    ('command_response_timeout', '4'),
    ('command_inactive_timeout', '6');

-- skip_feedback — structured skip reason collection
CREATE TABLE IF NOT EXISTS skip_feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER REFERENCES sessions(id),
    skip_streak INTEGER,
    reason      TEXT,        -- "too_intense", "too_slow", "too_samey", "wrong_mood", "just_skip"
    duration    TEXT,        -- "24h", "session", "permanent", or NULL
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- collections
CREATE TABLE IF NOT EXISTS collections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS collection_songs (
    collection_id INTEGER REFERENCES collections(id) ON DELETE CASCADE,
    song_id       INTEGER REFERENCES songs(id),
    position      INTEGER DEFAULT 0,
    added_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (collection_id, song_id)
);

CREATE INDEX IF NOT EXISTS idx_collection_songs_collection ON collection_songs(collection_id);
CREATE INDEX IF NOT EXISTS idx_collection_songs_song ON collection_songs(song_id);

-- sleep_timer — persisted timer state
CREATE TABLE IF NOT EXISTS sleep_timer (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    total_seconds   INTEGER,
    fade_seconds    INTEGER DEFAULT 30,
    switch_to_chill INTEGER DEFAULT 1,
    cancelled       INTEGER DEFAULT 0
);

-- mood_skip_repulsion — per-mood audio embedding repulsion from skip feedback
CREATE TABLE IF NOT EXISTS mood_skip_repulsion (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mood_state      TEXT NOT NULL,
    reason          TEXT NOT NULL,
    song_id         INTEGER NOT NULL,
    audio_embedding TEXT NOT NULL,  -- JSON serialized 512-dim vector
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mood_skip_repulsion_mood ON mood_skip_repulsion(mood_state);

-- song_suppression — temporal skip suppression (skip for 24h, session, etc.)
CREATE TABLE IF NOT EXISTS song_suppression (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id     INTEGER REFERENCES songs(id),
    reason      TEXT,
    expires_at  DATETIME,
    session_id  INTEGER REFERENCES sessions(id),
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_song_suppression_song ON song_suppression(song_id);
CREATE INDEX IF NOT EXISTS idx_song_suppression_expires ON song_suppression(expires_at);
