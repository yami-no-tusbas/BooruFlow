ALTER TABLE tagging_review_batch_entries
ADD COLUMN batch_visible INTEGER NOT NULL DEFAULT 1;

PRAGMA user_version = 19;
