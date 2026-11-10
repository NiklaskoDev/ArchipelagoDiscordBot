-- Live features (M3): per-game threads and roles, feed cursors, alert choices, the overview message, scheduled events.

CREATE TABLE game_spaces (
    guild_id    INTEGER NOT NULL,
    game_id     INTEGER NOT NULL,
    thread_id   INTEGER,
    role_id     INTEGER,
    kinds       TEXT NOT NULL,          -- JSON list of the feed kinds shown in the thread
    closed_at   REAL,                   -- set once the game is finished and the thread archived
    created_at  REAL NOT NULL,
    PRIMARY KEY (guild_id, game_id)
);

CREATE TABLE feed_cursors (
    game_id     INTEGER PRIMARY KEY,
    last_seq    INTEGER NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE alert_prefs (
    discord_user_id  INTEGER NOT NULL REFERENCES user_links (discord_user_id) ON DELETE CASCADE,
    kind             TEXT NOT NULL
        CHECK (kind IN ('item_received', 'hint_found', 'stuck_in_my_world', 'game_online', 'slot_offered')),
    PRIMARY KEY (discord_user_id, kind)  -- a row means the alert is on; everything is off by default
);

CREATE TABLE overview_messages (
    guild_id      INTEGER PRIMARY KEY,
    channel_id    INTEGER NOT NULL,
    message_id    INTEGER NOT NULL,
    content_hash  TEXT NOT NULL
);

CREATE TABLE scheduled_events (
    guild_id    INTEGER NOT NULL,
    source      TEXT NOT NULL CHECK (source IN ('game_start', 'signup_deadline')),
    source_id   INTEGER NOT NULL,
    event_id    INTEGER NOT NULL,
    starts_at   REAL NOT NULL,
    PRIMARY KEY (guild_id, source, source_id)
);
