ALTER TABLE tagging_review_batch_entries
    ADD COLUMN published_verified_at TEXT;

UPDATE tagging_review_batch_entries
SET publish_state='pending_publish'
WHERE publish_state='published';

PRAGMA user_version=18;
