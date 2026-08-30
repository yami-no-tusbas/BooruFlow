CREATE TABLE tagging_review_batch_entries (
    item_id INTEGER PRIMARY KEY REFERENCES analysis_items(id) ON DELETE CASCADE,
    site TEXT,
    post_id TEXT,
    original_tags_json TEXT NOT NULL,
    additions_json TEXT NOT NULL,
    removals_json TEXT NOT NULL,
    reviewed_final_tags_json TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    publish_state TEXT NOT NULL CHECK (publish_state IN (
        'reviewed', 'pending_publish', 'publishing', 'published', 'failed'
    )),
    CHECK ((site IS NULL AND post_id IS NULL) OR (site IS NOT NULL AND post_id IS NOT NULL))
);

CREATE INDEX idx_tagging_review_batch_entries_state
    ON tagging_review_batch_entries(publish_state, reviewed_at DESC);

PRAGMA user_version=15;
