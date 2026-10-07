CREATE TABLE runtime_state (
    key TEXT PRIMARY KEY NOT NULL,
    state_json TEXT,
    version INTEGER NOT NULL
);

CREATE TABLE runtime_state_cas_guard (
    id INTEGER PRIMARY KEY NOT NULL
);

INSERT INTO runtime_state_cas_guard (id) VALUES (1);

INSERT INTO runtime_state (key, state_json, version) VALUES (
    'current_match',
    '{"bench":[],"match_active":false,"match_count":0,"matches":[]}',
    1
);
INSERT INTO runtime_state (key, state_json, version)
VALUES ('current_draft', NULL, 1);

ALTER TABLE match_sessions ADD COLUMN creation_token TEXT;
CREATE UNIQUE INDEX uq_match_sessions_creation_token
ON match_sessions(creation_token) WHERE creation_token IS NOT NULL;
