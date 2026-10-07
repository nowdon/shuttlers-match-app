CREATE TABLE d1_poc_participants (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  card TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE d1_poc_batch_items (
  id INTEGER PRIMARY KEY,
  batch_key TEXT NOT NULL,
  value TEXT NOT NULL,
  UNIQUE(batch_key, value)
);

CREATE TABLE d1_poc_parent (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);

CREATE TABLE d1_poc_child (
  id INTEGER PRIMARY KEY,
  parent_id INTEGER NOT NULL REFERENCES d1_poc_parent(id),
  value TEXT NOT NULL
);
