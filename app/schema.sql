-- Source of truth for the database. No ORM, no migration tool: this file is
-- executed at startup and every statement is IF NOT EXISTS, so booting twice
-- against the same file is a no-op.

CREATE TABLE IF NOT EXISTS sessions (
    id                INTEGER PRIMARY KEY,
    code              TEXT NOT NULL UNIQUE,
    facilitator_token TEXT NOT NULL,
    phase             TEXT NOT NULL DEFAULT 'write'
                      CHECK (phase IN ('write', 'reveal', 'cluster', 'vote', 'discuss', 'done')),
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS participants (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    token      TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS clusters (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- kind is the API's "column" field; COLUMN is a SQLite keyword.
CREATE TABLE IF NOT EXISTS cards (
    id             INTEGER PRIMARY KEY,
    session_id     INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    cluster_id     INTEGER REFERENCES clusters(id) ON DELETE SET NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('start', 'stop', 'continue')),
    text           TEXT NOT NULL,
    anonymous      INTEGER NOT NULL DEFAULT 0 CHECK (anonymous IN (0, 1)),
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- No unique constraint: three votes from one person on one card is legal.
-- The budget of three per session is enforced in the insert transaction.
CREATE TABLE IF NOT EXISTS votes (
    id             INTEGER PRIMARY KEY,
    session_id     INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    card_id        INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS decisions (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('decision', 'action')),
    text       TEXT NOT NULL,
    owner      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
