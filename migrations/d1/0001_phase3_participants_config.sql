-- Phase 3 isolated local/preview schema subset.
-- Do not apply this migration to the production database until the cutover phase.
CREATE TABLE participants (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    gender TEXT NOT NULL,
    level TEXT NOT NULL,
    weight REAL NOT NULL,
    games_played INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    card TEXT NOT NULL UNIQUE
);

CREATE TABLE app_config (
    key TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    version INTEGER NOT NULL
);
