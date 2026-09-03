CREATE TABLE library_index_jobs (
    id TEXT PRIMARY KEY,
    roots_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending','running','paused','cancelled','completed','failed')),
    scanned INTEGER NOT NULL DEFAULT 0,
    imported INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    invalid INTEGER NOT NULL DEFAULT 0,
    metadata_parsed INTEGER NOT NULL DEFAULT 0,
    artists_found INTEGER NOT NULL DEFAULT 0,
    last_path TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE remote_artist_state (
    site TEXT NOT NULL,
    artist_tag TEXT NOT NULL COLLATE NOCASE,
    last_seen_at TEXT NOT NULL,
    last_used_at TEXT,
    protected INTEGER NOT NULL DEFAULT 0 CHECK (protected IN (0,1)),
    PRIMARY KEY (site,artist_tag),
    CHECK (site IN ('gelbooru','e621'))
) WITHOUT ROWID;

CREATE INDEX idx_remote_artist_last_used
ON remote_artist_state(last_used_at,site,artist_tag);

PRAGMA user_version = 11;
