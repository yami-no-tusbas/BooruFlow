ALTER TABLE tagging_review_batch_entries RENAME TO tagging_review_batch_entries_v24;

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
        'reviewed', 'pending_publish', 'publishing', 'published', 'failed', 'skipped'
    )),
    publish_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_attempt_at TEXT,
    published_at TEXT,
    published_final_tags_json TEXT,
    published_verified_at TEXT,
    batch_visible INTEGER NOT NULL DEFAULT 1,
    failure_reason TEXT,
    failure_retryable INTEGER,
    CHECK ((site IS NULL AND post_id IS NULL) OR (site IS NOT NULL AND post_id IS NOT NULL))
);

INSERT INTO tagging_review_batch_entries(
    item_id, site, post_id, original_tags_json, additions_json, removals_json,
    reviewed_final_tags_json, reviewed_at, publish_state, publish_attempts,
    last_error, last_attempt_at, published_at, published_final_tags_json,
    published_verified_at, batch_visible, failure_reason, failure_retryable
)
SELECT item_id, site, post_id, original_tags_json, additions_json, removals_json,
       reviewed_final_tags_json, reviewed_at, publish_state, publish_attempts,
       last_error, last_attempt_at, published_at, published_final_tags_json,
       published_verified_at, batch_visible, failure_reason, failure_retryable
FROM tagging_review_batch_entries_v24;

DROP TABLE tagging_review_batch_entries_v24;

CREATE INDEX idx_tagging_review_batch_entries_state
    ON tagging_review_batch_entries(publish_state, reviewed_at DESC);

PRAGMA user_version = 24;
