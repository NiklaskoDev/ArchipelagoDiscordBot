-- Friend alerts: `alert_prefs.kind` has a CHECK list that SQLite cannot extend in place, so the table is rebuilt with
-- the two new kinds. Nothing references alert_prefs; the rows are kept, and the new alerts are off for everyone.

CREATE TABLE alert_prefs_new (
    discord_user_id  INTEGER NOT NULL REFERENCES user_links (discord_user_id) ON DELETE CASCADE,
    kind             TEXT NOT NULL
        CHECK (kind IN ('item_received', 'hint_found', 'stuck_in_my_world', 'game_online', 'slot_offered',
                        'friend_request', 'friend_game')),
    PRIMARY KEY (discord_user_id, kind)  -- a row means the alert is on; everything is off by default
);

INSERT INTO alert_prefs_new (discord_user_id, kind) SELECT discord_user_id, kind FROM alert_prefs;
DROP TABLE alert_prefs;
ALTER TABLE alert_prefs_new RENAME TO alert_prefs;
