CREATE TABLE local_source_links (
    item_id INTEGER PRIMARY KEY REFERENCES analysis_items(id) ON DELETE CASCADE,
    site TEXT NOT NULL,
    post_id TEXT NOT NULL,
    confidence TEXT NOT NULL,
    enrichment_state TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT,
    detected_at TEXT NOT NULL,
    enriched_at TEXT,
    CHECK (site IN ('gelbooru', 'e621')),
    CHECK (confidence IN ('high')),
    CHECK (enrichment_state IN ('pending', 'resolved', 'failed'))
);

CREATE INDEX idx_local_source_link_post ON local_source_links(site, post_id);

CREATE TABLE post_metadata_cache (
    site TEXT NOT NULL,
    post_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    state TEXT NOT NULL,
    file_url TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    artist_tags_json TEXT NOT NULL DEFAULT '[]',
    last_error TEXT,
    PRIMARY KEY (site, post_id),
    CHECK (site IN ('gelbooru', 'e621')),
    CHECK (state IN ('resolved', 'missing'))
) WITHOUT ROWID;

PRAGMA user_version = 4;
