-- Linked manager accounts and per-server settings (M2).

CREATE TABLE user_links (
    discord_user_id   INTEGER PRIMARY KEY,
    manager_user_id   INTEGER NOT NULL UNIQUE,
    manager_username  TEXT NOT NULL,
    token_ciphertext  BLOB,             -- NULL once the manager stopped accepting the token
    token_hint        TEXT,             -- last 4 characters only, shown by /mydata
    status            TEXT NOT NULL CHECK (status IN ('active', 'invalid')),
    locale            TEXT NOT NULL DEFAULT 'en' CHECK (locale IN ('en', 'de', 'fr', 'nl')),
    linked_at         REAL NOT NULL,
    checked_at        REAL NOT NULL
);

CREATE TABLE guild_settings (
    guild_id             INTEGER PRIMARY KEY,
    language             TEXT NOT NULL DEFAULT 'en' CHECK (language IN ('en', 'de', 'fr', 'nl')),
    timezone             TEXT NOT NULL DEFAULT 'UTC',
    games_channel_id     INTEGER,
    signups_channel_id   INTEGER,
    overview_channel_id  INTEGER,
    organizer_role_id    INTEGER,
    admin_role_id        INTEGER,
    scheduled_events     INTEGER NOT NULL DEFAULT 0 CHECK (scheduled_events IN (0, 1)),
    updated_at           REAL NOT NULL
);
