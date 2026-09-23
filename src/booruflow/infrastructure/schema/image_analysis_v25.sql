ALTER TABLE tagging_review_batch_entries
    ADD COLUMN requested_additions_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE tagging_review_batch_entries
    ADD COLUMN requested_removals_json TEXT NOT NULL DEFAULT '[]';
PRAGMA user_version = 25;
