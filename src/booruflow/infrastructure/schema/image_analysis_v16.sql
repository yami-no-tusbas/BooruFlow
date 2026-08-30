ALTER TABLE tagging_review_batch_entries ADD COLUMN publish_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tagging_review_batch_entries ADD COLUMN last_error TEXT;
ALTER TABLE tagging_review_batch_entries ADD COLUMN last_attempt_at TEXT;
ALTER TABLE tagging_review_batch_entries ADD COLUMN published_at TEXT;
PRAGMA user_version=16;
