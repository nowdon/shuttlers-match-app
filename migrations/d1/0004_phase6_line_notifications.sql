-- Phase 6 LINE notification relational storage.
-- LINE API credentials remain Worker secrets and are never stored here.
CREATE TABLE line_accounts (
    id INTEGER PRIMARY KEY,
    participant_id INTEGER NOT NULL UNIQUE REFERENCES participants(id),
    line_user_id TEXT NOT NULL UNIQUE,
    display_name TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE notification_subscriptions (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES match_sessions(id),
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    channel TEXT NOT NULL DEFAULT 'line',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (session_id, participant_id, channel)
);

CREATE TABLE line_link_tokens (
    id INTEGER PRIMARY KEY,
    token TEXT NOT NULL UNIQUE,
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    session_id INTEGER NOT NULL REFERENCES match_sessions(id),
    used_at TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE match_notifications (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES match_sessions(id),
    match_count INTEGER NOT NULL,
    channel TEXT NOT NULL DEFAULT 'line',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    UNIQUE (session_id, match_count, channel)
);

CREATE TABLE notification_delivery_logs (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES match_sessions(id),
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    match_count INTEGER NOT NULL,
    channel TEXT NOT NULL DEFAULT 'line',
    status TEXT NOT NULL,
    error_message TEXT,
    sent_at TEXT NOT NULL
);
