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
    added_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_songs_ingest_phase ON songs(ingest_phase);
CREATE INDEX IF NOT EXISTS idx_songs_cluster ON songs(cluster_id);
CREATE INDEX IF NOT EXISTS idx_songs_energy ON songs(energy);
CREATE INDEX IF NOT EXISTS idx_songs_valence ON songs(valence);

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

-- blacklist
CREATE TABLE IF NOT EXISTS blacklist (
    song_id     INTEGER REFERENCES songs(id) PRIMARY KEY,
    added_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    reason      TEXT
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
    ('theme', 'dark'),
    ('announcement_interval', '30'),
    ('section_spacing', 'normal'),
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
    ('talk_caller_melancholy_enabled', 'false');
