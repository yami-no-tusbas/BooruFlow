CREATE TABLE local_filename_metadata (
    item_id INTEGER NOT NULL REFERENCES analysis_items(id) ON DELETE CASCADE,
    local_path TEXT NOT NULL,
    artist_tag TEXT NOT NULL,
    post_id TEXT NOT NULL,
    rating TEXT NOT NULL,
    source_md5 TEXT NOT NULL,
    site TEXT NOT NULL CHECK (site IN ('local', 'gelbooru', 'e621')),
    state TEXT NOT NULL CHECK (state IN ('applied', 'conflict')),
    conflict_reason TEXT,
    parsed_at TEXT NOT NULL,
    PRIMARY KEY (item_id, local_path)
) WITHOUT ROWID;

CREATE INDEX idx_filename_metadata_state
ON local_filename_metadata(state, site, artist_tag);

PRAGMA user_version = 10;
