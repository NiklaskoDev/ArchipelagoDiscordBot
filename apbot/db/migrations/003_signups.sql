-- Sign-ups (M5): which deadline reminders were already posted, so each is posted once per round, deadline and lead time.

CREATE TABLE signup_reminders (
    guild_id        INTEGER NOT NULL,
    signup_id       INTEGER NOT NULL,
    deadline        REAL NOT NULL,      -- the deadline the reminder was for: a moved deadline gets its reminders again
    before_seconds  INTEGER NOT NULL,   -- how long before the deadline it was posted (24 h, 1 h)
    sent_at         REAL NOT NULL,
    PRIMARY KEY (guild_id, signup_id, deadline, before_seconds)
);
