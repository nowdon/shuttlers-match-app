CREATE TABLE match_sessions (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'draft',
    match_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    notification_sent_at TEXT
);

CREATE TABLE match_rounds (
    id INTEGER PRIMARY KEY,
    session_id INTEGER REFERENCES match_sessions(id),
    round_number INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE match_histories (
    id INTEGER PRIMARY KEY,
    round_id INTEGER NOT NULL REFERENCES match_rounds(id),
    court_number INTEGER NOT NULL,
    team1_player1_id INTEGER NOT NULL REFERENCES participants(id),
    team1_player2_id INTEGER NOT NULL REFERENCES participants(id),
    team2_player1_id INTEGER NOT NULL REFERENCES participants(id),
    team2_player2_id INTEGER NOT NULL REFERENCES participants(id),
    team1_score INTEGER,
    team2_score INTEGER,
    score_text TEXT,
    winner_team INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE bench_histories (
    id INTEGER PRIMARY KEY,
    round_id INTEGER NOT NULL REFERENCES match_rounds(id),
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX uq_match_round_session_round
ON match_rounds(session_id, round_number)
WHERE session_id IS NOT NULL;

CREATE UNIQUE INDEX uq_match_history_round_court
ON match_histories(round_id, court_number);

CREATE UNIQUE INDEX uq_bench_history_round_participant
ON bench_histories(round_id, participant_id);
